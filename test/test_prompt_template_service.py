from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.prompt_template_service import PromptTemplateService


OWNER = {"id": "owner-1", "name": "Owner", "role": "admin"}
OTHER_OWNER = {"id": "owner-2", "name": "Other", "role": "user"}


SAMPLE_MARKDOWN = """
# Prompt Gallery

## E-commerce Cases

### Case 160: E-commerce Main Image - 9-Panel Product TVC Storyboard

| Output |
| :----: |
| <img src="https://example.test/output.jpg" width="300" alt="Output image"> |

**Prompt:**

```
Using the provided reference image, transform the single casual product photo into a polished e-commerce TVC storyboard board for a {argument name="video duration" default="15-second"} ad in a {argument name="aspect ratio" default="9:16"} vertical format. Add the overall header text and a product subtitle naming it {argument name="product name" default="青花瓷烟灰缸"}. Use premium, realistic commercial photography throughout.
```

### Case 161: Too short

```
tiny
```
"""


class PromptTemplateServiceTests(unittest.TestCase):
    def make_service(self, path: Path, fetcher=None) -> PromptTemplateService:
        return PromptTemplateService(path, url_fetcher=fetcher)

    def test_create_render_update_and_delete_template(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "prompt_templates.json")
            item = service.create_template(
                OWNER,
                {
                    "title": "Product poster",
                    "category": "Poster",
                    "tags": ["ad", "product"],
                    "template_text": "Create a premium poster for {{product}} in [style] style.",
                },
            )

            self.assertEqual(item["title"], "Product poster")
            self.assertEqual([variable["name"] for variable in item["variables"]], ["product", "style"])

            rendered = service.render_template(
                OWNER,
                template_id=item["id"],
                variables={"product": "coffee", "style": "cinematic"},
            )
            self.assertEqual(rendered["rendered_prompt"], "Create a premium poster for coffee in cinematic style.")
            self.assertEqual(rendered["missing_variables"], [])

            updated = service.update_template(OWNER, item["id"], {"title": "Updated", "template_text": "Render {{subject}}."})
            self.assertIsNotNone(updated)
            self.assertEqual(updated["title"], "Updated")
            self.assertEqual(updated["version"], 2)

            self.assertTrue(service.delete_template(OWNER, item["id"]))
            self.assertIsNone(service.get_template(OWNER, item["id"]))

    def test_render_reports_missing_variables_and_uses_defaults(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "prompt_templates.json")
            rendered = service.render_template(
                OWNER,
                template_text='A quote card with {argument name="quote" default="Stay hungry"} by {{author}}.',
                variables={},
            )

            self.assertEqual(rendered["rendered_prompt"], "A quote card with Stay hungry by {{author}}.")
            self.assertEqual(rendered["missing_variables"], ["author"])

    def test_import_markdown_extracts_prompt_blocks_and_variables(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "prompt_templates.json")
            result = service.import_markdown(
                OWNER,
                markdown=SAMPLE_MARKDOWN,
                source_url="https://raw.githubusercontent.com/example/repo/main/README.md",
                source_repo="https://github.com/example/repo",
                source_path="README.md",
                tags=["github"],
                language="en",
            )

            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["skipped"], 0)
            item = result["items"][0]
            self.assertEqual(item["scope"], "imported")
            self.assertEqual(item["category"], "E-commerce Cases")
            self.assertIn("{{video_duration}}", item["template_text"])
            self.assertIn("{{aspect_ratio}}", item["template_text"])
            self.assertEqual(item["preview_image_url"], "https://example.test/output.jpg")

            duplicate = service.import_markdown(OWNER, markdown=SAMPLE_MARKDOWN)
            self.assertEqual(duplicate["imported"], 0)
            self.assertEqual(duplicate["skipped"], 1)

    def test_import_github_fetches_readme_and_tries_main_then_master(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            fetched_urls: list[str] = []

            def fetcher(url: str) -> str:
                fetched_urls.append(url)
                if "/main/" in url:
                    raise OSError("not found")
                return SAMPLE_MARKDOWN

            service = self.make_service(Path(tmp_dir) / "prompt_templates.json", fetcher)
            result = service.import_github(
                OWNER,
                repo_url="https://github.com/example/repo",
                paths=["README.md"],
                tags=["remote"],
            )

            self.assertEqual(result["imported"], 1)
            self.assertEqual(result["errors"], [])
            self.assertIn("https://raw.githubusercontent.com/example/repo/main/README.md", fetched_urls)
            self.assertIn("https://raw.githubusercontent.com/example/repo/master/README.md", fetched_urls)
            self.assertEqual(result["items"][0]["source_path"], "README.md")

    def test_owner_isolation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "prompt_templates.json")
            item = service.create_template(OWNER, {"title": "Private", "template_text": "Create {{subject}}."})

            self.assertEqual(service.list_templates(OWNER)["items"][0]["id"], item["id"])
            self.assertEqual(service.list_templates(OTHER_OWNER)["items"], [])
            self.assertIsNone(service.get_template(OTHER_OWNER, item["id"]))
            self.assertFalse(service.delete_template(OTHER_OWNER, item["id"]))

    def test_imported_templates_can_be_updated_and_deleted_in_bulk(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "prompt_templates.json")
            imported = service.import_markdown(OWNER, markdown=SAMPLE_MARKDOWN)["items"][0]
            user_item = service.create_template(OWNER, {"title": "Manual", "template_text": "Create a cinematic {{subject}} poster."})
            other_item = service.create_template(OTHER_OWNER, {"title": "Other", "template_text": "Create a clean studio {{subject}} portrait."})

            updated = service.update_template(
                OWNER,
                imported["id"],
                {"title": "Edited import", "template_text": "Render a premium {{product}} campaign image."},
            )

            self.assertIsNotNone(updated)
            self.assertEqual(updated["title"], "Edited import")
            self.assertEqual([variable["name"] for variable in updated["variables"]], ["product"])

            result = service.delete_templates(OWNER, [imported["id"], user_item["id"], other_item["id"], "missing"])

            self.assertEqual(result["deleted"], 2)
            self.assertEqual(set(result["missing_ids"]), {"missing", other_item["id"]})
            self.assertEqual(service.list_templates(OWNER)["items"], [])
            self.assertEqual(service.list_templates(OTHER_OWNER)["items"][0]["id"], other_item["id"])


if __name__ == "__main__":
    unittest.main()
