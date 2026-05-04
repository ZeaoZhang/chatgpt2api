from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.config import DATA_DIR


VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")
RAYCAST_ARGUMENT_PATTERN = re.compile(
    r"\{argument\s+name=(?P<quote>[\"'])(?P<name>.+?)(?P=quote)(?:\s+default=(?P<default_quote>[\"'])(?P<default>.*?)(?P=default_quote))?\s*\}"
)
BRACKET_VARIABLE_PATTERN = re.compile(r"\[([A-Za-z_][A-Za-z0-9_-]{1,48})\]")
SIMPLE_BRACE_VARIABLE_PATTERN = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_-]{1,48})\}(?!\})")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_PATTERN = re.compile(r"!\[[^\]]*]\(([^)]+)\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)
MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)]\([^)]+\)")
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: object, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _normalize_label(value: str) -> str:
    text = _clean(value)
    text = re.sub(r"^[^\w\u4e00-\u9fff]+", "", text)
    text = MARKDOWN_LINK_PATTERN.sub(r"\1", text)
    text = HTML_TAG_PATTERN.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _canonical_variable_name(value: str) -> str:
    text = _clean(value).lower()
    text = re.sub(r"[^a-z0-9_ -]+", "", text)
    text = re.sub(r"[\s-]+", "_", text).strip("_")
    return text or "value"


def _normalize_template_text(text: str) -> tuple[str, list[dict[str, str]]]:
    variables: dict[str, dict[str, str]] = {}

    def replace_raycast(match: re.Match[str]) -> str:
        raw_name = _clean(match.group("name"))
        name = _canonical_variable_name(raw_name)
        default = _clean(match.group("default"))
        variables[name] = {"name": name, "label": raw_name or name, "default": default}
        return f"{{{{{name}}}}}"

    normalized = RAYCAST_ARGUMENT_PATTERN.sub(replace_raycast, text)

    def replace_bracket(match: re.Match[str]) -> str:
        raw_name = _clean(match.group(1))
        name = _canonical_variable_name(raw_name)
        variables.setdefault(name, {"name": name, "label": raw_name, "default": ""})
        return f"{{{{{name}}}}}"

    normalized = BRACKET_VARIABLE_PATTERN.sub(replace_bracket, normalized)

    def replace_simple_brace(match: re.Match[str]) -> str:
        raw_name = _clean(match.group(1))
        name = _canonical_variable_name(raw_name)
        variables.setdefault(name, {"name": name, "label": raw_name, "default": ""})
        return f"{{{{{name}}}}}"

    normalized = SIMPLE_BRACE_VARIABLE_PATTERN.sub(replace_simple_brace, normalized)
    for match in VARIABLE_PATTERN.finditer(normalized):
        name = _canonical_variable_name(match.group(1))
        variables.setdefault(name, {"name": name, "label": name, "default": ""})
    return normalized.strip(), list(variables.values())


def _prompt_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_github_url(repo_url: str, path: str | None = None) -> list[str]:
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.netloc == "raw.githubusercontent.com":
        return [repo_url]
    if parsed.netloc not in {"github.com", "www.github.com"}:
        return [repo_url]

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError("github repo url must include owner and repository")
    owner, repo = parts[0], parts[1]

    if len(parts) >= 5 and parts[2] in {"blob", "raw"}:
        branch = parts[3]
        raw_path = "/".join(parts[4:])
        return [f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{raw_path}"]

    target_paths = [path] if path else ["README.md"]
    urls: list[str] = []
    for target_path in target_paths:
        clean_path = target_path.strip("/")
        urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/main/{clean_path}")
        urls.append(f"https://raw.githubusercontent.com/{owner}/{repo}/master/{clean_path}")
    return urls


def _fetch_url(url: str, timeout: float = 12.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "chatgpt2api-prompt-template-importer"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


class MarkdownPromptExtractor:
    def __init__(self, markdown: str, *, source_url: str = "", source_path: str = ""):
        self.markdown = str(markdown or "")
        self.source_url = _clean(source_url)
        self.source_path = _clean(source_path)
        self._items: list[dict[str, Any]] = []
        self._seen_hashes: set[str] = set()
        self._last_image_url = ""

    def extract(self) -> list[dict[str, Any]]:
        category = ""
        current_title = ""
        pending_prompt_label = False
        in_code = False
        code_lines: list[str] = []
        code_start_line = 0
        code_title = ""
        code_category = ""
        lines = self.markdown.splitlines()

        for index, line in enumerate(lines, start=1):
            image_match = IMAGE_PATTERN.search(line)
            if image_match:
                self._last_image_url = _clean(image_match.group(1) or image_match.group(2))

            fence = line.strip().startswith("```")
            if fence and not in_code:
                in_code = True
                code_lines = []
                code_start_line = index
                code_title = current_title
                code_category = category
                continue
            if fence and in_code:
                text = "\n".join(code_lines).strip()
                if text:
                    self._add_candidate(
                        text,
                        title=code_title,
                        category=code_category,
                        source_heading=code_title,
                        source_line=code_start_line,
                        preview_image_url=self._last_image_url,
                        preferred=pending_prompt_label,
                    )
                in_code = False
                pending_prompt_label = False
                continue
            if in_code:
                code_lines.append(line)
                continue

            heading_match = HEADING_PATTERN.match(line)
            if heading_match:
                level = len(heading_match.group(1))
                heading = _normalize_label(heading_match.group(2))
                lower_heading = heading.lower()
                if level <= 2:
                    category = heading
                elif "prompt" not in lower_heading and "description" not in lower_heading:
                    current_title = heading
                pending_prompt_label = "prompt" in lower_heading
                continue

            stripped = line.strip()
            lowered = stripped.lower().strip(":")
            if lowered in {"prompt", "**prompt**", "#### 📝 prompt"} or stripped.startswith("**Prompt:**"):
                pending_prompt_label = True
                continue
            if stripped.startswith(">") and len(stripped) > 80:
                self._add_candidate(
                    stripped.lstrip("> ").strip(),
                    title=current_title,
                    category=category,
                    source_heading=current_title,
                    source_line=index,
                    preview_image_url=self._last_image_url,
                    preferred=False,
                )
            if stripped.startswith(("-", "*")) and len(stripped) > 100 and not stripped.startswith(("- [", "* [")):
                self._add_candidate(
                    stripped.lstrip("-* ").strip(),
                    title=current_title,
                    category=category,
                    source_heading=current_title,
                    source_line=index,
                    preview_image_url=self._last_image_url,
                    preferred=False,
                )

        return self._items

    def _add_candidate(
        self,
        text: str,
        *,
        title: str,
        category: str,
        source_heading: str,
        source_line: int,
        preview_image_url: str,
        preferred: bool,
    ) -> None:
        normalized_text, variables = _normalize_template_text(text)
        if not self._looks_like_prompt(normalized_text, preferred=preferred):
            return
        digest = _prompt_hash(normalized_text)
        if digest in self._seen_hashes:
            return
        self._seen_hashes.add(digest)
        title = _normalize_label(title)
        category = _normalize_label(category)
        if not title:
            title = f"Imported Prompt {len(self._items) + 1}"
        self._items.append({
            "title": title[:160],
            "category": category[:80],
            "template_text": normalized_text,
            "variables": variables,
            "source_heading": _normalize_label(source_heading)[:160],
            "source_line": source_line,
            "preview_image_url": preview_image_url,
        })

    @staticmethod
    def _looks_like_prompt(text: str, *, preferred: bool) -> bool:
        clean_text = text.strip()
        if len(clean_text) < 40:
            return False
        lower = clean_text.lower()
        if lower.startswith(("curl ", "npx ", "git clone ", "pip install ")):
            return False
        if "authorization:" in lower and "bearer" in lower:
            return False
        if preferred:
            return True
        prompt_words = {
            "create", "generate", "render", "image", "poster", "photography", "style",
            "cinematic", "portrait", "product", "illustration", "构图", "生成", "图片", "海报",
        }
        return any(word in lower for word in prompt_words)


class PromptTemplateService:
    def __init__(
        self,
        path: Path,
        *,
        url_fetcher: Callable[[str], str] | None = None,
    ):
        self.path = path
        self.url_fetcher = url_fetcher or _fetch_url
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._items = self._load_locked()

    def list_templates(
        self,
        identity: dict[str, object],
        *,
        query: str = "",
        category: str = "",
        tag: str = "",
    ) -> dict[str, Any]:
        owner = _owner_id(identity)
        query_text = _clean(query).lower()
        category_text = _clean(category).lower()
        tag_text = _clean(tag).lower()
        with self._lock:
            items = [self._public_item(item) for item in self._items if self._can_read(owner, item)]
        if query_text:
            items = [
                item for item in items
                if query_text in " ".join([
                    _clean(item.get("title")),
                    _clean(item.get("description")),
                    _clean(item.get("category")),
                    _clean(item.get("template_text")),
                    " ".join(str(tag) for tag in item.get("tags") or []),
                ]).lower()
            ]
        if category_text:
            items = [item for item in items if _clean(item.get("category")).lower() == category_text]
        if tag_text:
            items = [item for item in items if tag_text in {str(value).lower() for value in item.get("tags") or []}]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"items": items}

    def get_template(self, identity: dict[str, object], template_id: str) -> dict[str, Any] | None:
        owner = _owner_id(identity)
        normalized_id = _clean(template_id)
        with self._lock:
            for item in self._items:
                if item.get("id") == normalized_id and self._can_read(owner, item):
                    return self._public_item(item)
        return None

    def create_template(self, identity: dict[str, object], data: dict[str, Any]) -> dict[str, Any]:
        owner = _owner_id(identity)
        item = self._build_item(owner, data, scope=_clean(data.get("scope"), "user") or "user")
        with self._lock:
            self._items.append(item)
            self._save_locked()
            return self._public_item(item)

    def update_template(self, identity: dict[str, object], template_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        owner = _owner_id(identity)
        normalized_id = _clean(template_id)
        with self._lock:
            for index, item in enumerate(self._items):
                if item.get("id") != normalized_id or not self._can_write(owner, item):
                    continue
                next_item = dict(item)
                for key in ("title", "description", "category", "language", "preview_image_url"):
                    if key in data:
                        next_item[key] = _clean(data.get(key))
                if "tags" in data:
                    next_item["tags"] = self._normalize_tags(data.get("tags"))
                if "template_text" in data:
                    template_text, variables = _normalize_template_text(_clean(data.get("template_text")))
                    if not template_text:
                        raise ValueError("template_text is required")
                    next_item["template_text"] = template_text
                    next_item["variables"] = variables
                    next_item["prompt_hash"] = _prompt_hash(template_text)
                next_item["updated_at"] = _now_iso()
                next_item["version"] = int(next_item.get("version") or 1) + 1
                self._items[index] = self._normalize_item(next_item)
                self._save_locked()
                return self._public_item(self._items[index])
        return None

    def delete_template(self, identity: dict[str, object], template_id: str) -> bool:
        owner = _owner_id(identity)
        normalized_id = _clean(template_id)
        with self._lock:
            before = len(self._items)
            self._items = [
                item for item in self._items
                if not (item.get("id") == normalized_id and self._can_write(owner, item))
            ]
            if len(self._items) == before:
                return False
            self._save_locked()
            return True

    def render_template(
        self,
        identity: dict[str, object],
        *,
        template_id: str = "",
        template_text: str = "",
        variables: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        source: dict[str, Any] | None = None
        if template_id:
            source = self.get_template(identity, template_id)
            if source is None:
                raise KeyError("template not found")
            template_text = _clean(source.get("template_text"))
        normalized_text, detected_variables = _normalize_template_text(template_text)
        if not normalized_text:
            raise ValueError("template_text is required")

        values = {str(key): "" if value is None else str(value) for key, value in (variables or {}).items()}
        variable_map = {item["name"]: item for item in detected_variables}
        missing: list[str] = []

        def replace(match: re.Match[str]) -> str:
            name = _canonical_variable_name(match.group(1))
            if name in values and values[name] != "":
                return values[name]
            default = _clean(variable_map.get(name, {}).get("default"))
            if default:
                return default
            missing.append(name)
            return match.group(0)

        rendered = VARIABLE_PATTERN.sub(replace, normalized_text)
        return {
            "template_id": source.get("id") if source else None,
            "rendered_prompt": rendered,
            "missing_variables": sorted(set(missing)),
            "variables": list(variable_map.values()),
        }

    def import_markdown(
        self,
        identity: dict[str, object],
        *,
        markdown: str,
        source_url: str = "",
        source_repo: str = "",
        source_path: str = "",
        tags: list[str] | None = None,
        language: str = "",
    ) -> dict[str, Any]:
        owner = _owner_id(identity)
        candidates = MarkdownPromptExtractor(markdown, source_url=source_url, source_path=source_path).extract()
        imported: list[dict[str, Any]] = []
        skipped = 0
        now = _now_iso()
        normalized_tags = self._normalize_tags(tags)
        with self._lock:
            existing_hashes = {
                str(item.get("prompt_hash"))
                for item in self._items
                if item.get("owner_id") == owner
            }
            for candidate in candidates:
                prompt_hash = _prompt_hash(str(candidate.get("template_text") or ""))
                if prompt_hash in existing_hashes:
                    skipped += 1
                    continue
                existing_hashes.add(prompt_hash)
                item = self._normalize_item({
                    "id": _new_id(),
                    "owner_id": owner,
                    "scope": "imported",
                    "title": candidate.get("title"),
                    "description": "",
                    "category": candidate.get("category"),
                    "tags": normalized_tags,
                    "language": language,
                    "template_text": candidate.get("template_text"),
                    "variables": candidate.get("variables"),
                    "source_type": "github_markdown" if source_url else "markdown",
                    "source_url": source_url,
                    "source_repo": source_repo,
                    "source_path": source_path,
                    "source_sha": "",
                    "source_heading": candidate.get("source_heading"),
                    "source_line": candidate.get("source_line"),
                    "preview_image_url": candidate.get("preview_image_url"),
                    "prompt_hash": prompt_hash,
                    "created_at": now,
                    "updated_at": now,
                    "imported_at": now,
                    "version": 1,
                })
                self._items.append(item)
                imported.append(self._public_item(item))
            if imported:
                self._save_locked()
        return {"items": imported, "imported": len(imported), "skipped": skipped, "candidates": len(candidates)}

    def import_github(
        self,
        identity: dict[str, object],
        *,
        repo_url: str,
        paths: list[str] | None = None,
        tags: list[str] | None = None,
        language: str = "",
    ) -> dict[str, Any]:
        normalized_repo_url = _clean(repo_url)
        if not normalized_repo_url:
            raise ValueError("repo_url is required")
        target_paths = [path for path in (paths or ["README.md"]) if _clean(path)]
        all_items: list[dict[str, Any]] = []
        imported = 0
        skipped = 0
        candidates = 0
        errors: list[dict[str, str]] = []

        for path in target_paths:
            urls = _parse_github_url(normalized_repo_url, path)
            markdown = None
            fetched_url = ""
            last_error = ""
            for url in urls:
                try:
                    markdown = self.url_fetcher(url)
                    fetched_url = url
                    break
                except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError, ValueError) as exc:
                    last_error = str(exc)
            if markdown is None:
                errors.append({"path": path, "error": last_error or "failed to fetch markdown"})
                continue
            result = self.import_markdown(
                identity,
                markdown=markdown,
                source_url=fetched_url,
                source_repo=normalized_repo_url,
                source_path=path,
                tags=tags,
                language=language,
            )
            all_items.extend(result["items"])
            imported += int(result["imported"])
            skipped += int(result["skipped"])
            candidates += int(result["candidates"])

        return {"items": all_items, "imported": imported, "skipped": skipped, "candidates": candidates, "errors": errors}

    @staticmethod
    def _can_read(owner: str, item: dict[str, Any]) -> bool:
        return item.get("scope") == "builtin" or item.get("owner_id") == owner

    @staticmethod
    def _can_write(owner: str, item: dict[str, Any]) -> bool:
        return item.get("scope") != "builtin" and item.get("owner_id") == owner

    @staticmethod
    def _normalize_tags(value: object) -> list[str]:
        if isinstance(value, str):
            raw_tags = re.split(r"[,，\s]+", value)
        elif isinstance(value, list):
            raw_tags = [str(item) for item in value]
        else:
            raw_tags = []
        tags: list[str] = []
        for raw_tag in raw_tags:
            tag = _clean(raw_tag)
            if tag and tag not in tags:
                tags.append(tag[:40])
        return tags

    def _build_item(self, owner: str, data: dict[str, Any], *, scope: str) -> dict[str, Any]:
        now = _now_iso()
        template_text, variables = _normalize_template_text(_clean(data.get("template_text")))
        if not template_text:
            raise ValueError("template_text is required")
        title = _clean(data.get("title")) or template_text.splitlines()[0][:80]
        return self._normalize_item({
            "id": _clean(data.get("id")) or _new_id(),
            "owner_id": owner,
            "scope": scope if scope in {"builtin", "user", "imported"} else "user",
            "title": title,
            "description": _clean(data.get("description")),
            "category": _clean(data.get("category")),
            "tags": self._normalize_tags(data.get("tags")),
            "language": _clean(data.get("language")),
            "template_text": template_text,
            "variables": variables,
            "source_type": _clean(data.get("source_type"), "manual") or "manual",
            "source_url": _clean(data.get("source_url")),
            "source_repo": _clean(data.get("source_repo")),
            "source_path": _clean(data.get("source_path")),
            "source_sha": _clean(data.get("source_sha")),
            "source_heading": _clean(data.get("source_heading")),
            "source_line": int(data.get("source_line") or 0),
            "preview_image_url": _clean(data.get("preview_image_url")),
            "prompt_hash": _prompt_hash(template_text),
            "created_at": _clean(data.get("created_at"), now),
            "updated_at": now,
            "imported_at": _clean(data.get("imported_at")),
            "version": int(data.get("version") or 1),
        })

    def _normalize_item(self, raw: dict[str, Any]) -> dict[str, Any]:
        template_text, variables = _normalize_template_text(_clean(raw.get("template_text")))
        if not template_text:
            raise ValueError("template_text is required")
        now = _now_iso()
        scope = _clean(raw.get("scope"), "user")
        if scope not in {"builtin", "user", "imported"}:
            scope = "user"
        return {
            "id": _clean(raw.get("id")) or _new_id(),
            "owner_id": _clean(raw.get("owner_id")) or "anonymous",
            "scope": scope,
            "title": (_clean(raw.get("title")) or template_text.splitlines()[0])[:160],
            "description": _clean(raw.get("description")),
            "category": _clean(raw.get("category"))[:80],
            "tags": self._normalize_tags(raw.get("tags")),
            "language": _clean(raw.get("language")),
            "template_text": template_text,
            "variables": variables if isinstance(raw.get("variables"), list) else variables,
            "source_type": _clean(raw.get("source_type"), "manual") or "manual",
            "source_url": _clean(raw.get("source_url")),
            "source_repo": _clean(raw.get("source_repo")),
            "source_path": _clean(raw.get("source_path")),
            "source_sha": _clean(raw.get("source_sha")),
            "source_heading": _clean(raw.get("source_heading")),
            "source_line": int(raw.get("source_line") or 0),
            "preview_image_url": _clean(raw.get("preview_image_url")),
            "prompt_hash": _clean(raw.get("prompt_hash")) or _prompt_hash(template_text),
            "created_at": _clean(raw.get("created_at"), now),
            "updated_at": _clean(raw.get("updated_at"), now),
            "imported_at": _clean(raw.get("imported_at")),
            "version": int(raw.get("version") or 1),
        }

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if key not in {"owner_id", "prompt_hash"}}

    def _load_locked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        raw_items = raw.get("items") if isinstance(raw, dict) else raw
        if not isinstance(raw_items, list):
            return []
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            try:
                items.append(self._normalize_item(item))
            except ValueError:
                continue
        return items

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"items": self._items}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


prompt_template_service = PromptTemplateService(DATA_DIR / "prompt_templates.json")
