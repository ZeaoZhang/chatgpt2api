from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from curl_cffi import requests

from services.config import config
from services.proxy_service import proxy_settings
from services.protocol.conversation import ConversationRequest, collect_text, text_backend


DEFAULT_OPTIMIZER_MODEL = "auto"
DEFAULT_AGENT_SYSTEM_PROMPT = (
    "你是图像生成 Agent。你的任务是判断用户需求是否足够清晰，并把清晰需求改写成适合 GPT Image 2 的高质量提示词。"
    "必须保留用户原意，不新增品牌、人物身份、违法或敏感内容。"
    "只输出 JSON，不要输出 Markdown。JSON 字段："
    "needs_clarification 布尔值；"
    "clarification_questions 字符串数组，最多 3 个；"
    "final_prompt 字符串；"
    "style_consistency_notes 字符串数组；"
    "sequence_plan 字符串数组；"
    "safety_notes 字符串数组。"
    "如果用户需求缺少主体、用途、风格、画幅、连续图关系、编辑目标等关键信息，设置 needs_clarification=true 并提出问题。"
)


def _clean(value: object, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = _clean(text)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _normalize_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [_clean(item) for item in value if _clean(item)]
    text = _clean(value)
    return [text] if text else []


def _system_prompt() -> str:
    agent_config = config.agent_service
    return _clean(agent_config.get("prompt")) or DEFAULT_AGENT_SYSTEM_PROMPT


def _user_prompt(
    *,
    prompt: str,
    mode: str,
    count: int,
    size: str,
    template_text: str,
    context: str,
) -> str:
    parts = [
        f"模式: {mode or 'single'}",
        f"目标图片数量: {count}",
        f"画幅比例: {size or '未指定'}",
    ]
    if template_text:
        parts.append(f"模板内容:\n{template_text}")
    if context:
        parts.append(f"额外上下文:\n{context}")
    parts.append(f"用户原始提示词:\n{prompt}")
    parts.append(
        "先判断是否需要向用户澄清。若不需要，请优化为一个可以直接用于图像生成的 final_prompt。"
        "如果目标图片数量大于 1 或模式是 sequence，请在 sequence_plan 中给出每张图的连续性说明。"
    )
    return "\n\n".join(parts)


def _heuristic_clarification(prompt: str, mode: str, count: int, has_images: bool = False) -> list[str]:
    text = _clean(prompt)
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 4:
        return ["你想生成或编辑的主体是什么？", "希望图片用于什么场景？", "偏好的风格或画幅是什么？"]
    vague_terms = {"好看", "随便", "优化一下", "改一下", "生成图片", "做图", "图片"}
    if compact in vague_terms:
        return ["请补充图片主体、用途和风格偏好。"]
    if has_images and len(compact) < 10:
        return ["你希望如何编辑参考图？请说明要保留和要改变的部分。"]
    if (mode == "sequence" or count > 1) and not any(word in text for word in ("连续", "第一", "第二", "故事", "分镜", "变化", "前后", "系列")):
        return ["这几张图之间需要怎样连续？例如同一角色、同一场景、动作变化还是故事分镜？"]
    return []


class AgentPromptService:
    def __init__(self, text_generator: Callable[[list[dict[str, str]], str], str] | None = None):
        self.text_generator = text_generator or self._generate_text

    def optimize_prompt(
        self,
        *,
        prompt: str,
        model: str = DEFAULT_OPTIMIZER_MODEL,
        mode: str = "single",
        count: int = 1,
        size: str = "",
        template_text: str = "",
        context: str = "",
        has_images: bool = False,
    ) -> dict[str, Any]:
        original_prompt = _clean(prompt)
        if not original_prompt:
            raise ValueError("prompt is required")
        try:
            safe_count = max(1, min(20, int(count or 1)))
        except (TypeError, ValueError):
            safe_count = 1
        normalized_model = _clean(model, DEFAULT_OPTIMIZER_MODEL) or DEFAULT_OPTIMIZER_MODEL
        heuristic_questions = _heuristic_clarification(
            original_prompt,
            _clean(mode, "single") or "single",
            safe_count,
            has_images,
        )
        if heuristic_questions and not _clean(context):
            return {
                "original_prompt": original_prompt,
                "final_prompt": "",
                "model": normalized_model,
                "mode": _clean(mode, "single") or "single",
                "count": safe_count,
                "size": _clean(size),
                "needs_clarification": True,
                "clarification_questions": heuristic_questions[:3],
                "style_consistency_notes": [],
                "sequence_plan": [],
                "safety_notes": [],
                "raw_response": "",
            }
        messages = [
            {"role": "system", "content": _system_prompt()},
            {
                "role": "user",
                "content": _user_prompt(
                    prompt=original_prompt,
                    mode=_clean(mode, "single") or "single",
                    count=safe_count,
                    size=_clean(size),
                    template_text=_clean(template_text),
                    context=_clean(context),
                ),
            },
        ]
        raw_text = self.text_generator(messages, normalized_model)
        parsed = _extract_json_object(raw_text) or {}
        final_prompt = _clean(parsed.get("final_prompt")) or _clean(parsed.get("prompt")) or _clean(raw_text) or original_prompt
        clarification_questions = _normalize_list(parsed.get("clarification_questions"))[:3]
        needs_clarification = bool(parsed.get("needs_clarification")) and bool(clarification_questions)
        return {
            "original_prompt": original_prompt,
            "final_prompt": final_prompt,
            "model": normalized_model,
            "mode": _clean(mode, "single") or "single",
            "count": safe_count,
            "size": _clean(size),
            "needs_clarification": needs_clarification,
            "clarification_questions": clarification_questions,
            "style_consistency_notes": _normalize_list(parsed.get("style_consistency_notes")),
            "sequence_plan": _normalize_list(parsed.get("sequence_plan")),
            "safety_notes": _normalize_list(parsed.get("safety_notes")),
            "raw_response": raw_text,
        }

    @staticmethod
    def _generate_text(messages: list[dict[str, str]], model: str) -> str:
        agent_config = config.agent_service
        if agent_config.get("provider") == "openai_compatible":
            return AgentPromptService._generate_external_text(messages, model, agent_config)
        return collect_text(text_backend(), ConversationRequest(model=model, messages=messages))

    @staticmethod
    def _generate_external_text(messages: list[dict[str, str]], model: str, agent_config: dict[str, object]) -> str:
        base_url = _clean(agent_config.get("base_url")).rstrip("/")
        api_key = _clean(agent_config.get("api_key"))
        configured_model = _clean(agent_config.get("model"), DEFAULT_OPTIMIZER_MODEL) or DEFAULT_OPTIMIZER_MODEL
        target_model = configured_model if configured_model != DEFAULT_OPTIMIZER_MODEL else (_clean(model) or DEFAULT_OPTIMIZER_MODEL)
        if not base_url or not api_key or not target_model or target_model == DEFAULT_OPTIMIZER_MODEL:
            raise RuntimeError("agent service config is incomplete")
        response = requests.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": target_model, "messages": messages, "temperature": 0.2},
            timeout=90,
            **proxy_settings.build_session_kwargs(),
        )
        response.raise_for_status()
        data = response.json()
        return _clean(data["choices"][0]["message"]["content"])


agent_prompt_service = AgentPromptService()
