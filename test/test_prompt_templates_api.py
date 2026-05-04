from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.prompt_templates as prompt_templates_module


AUTH_HEADERS = {"Authorization": "Bearer chatgpt2api"}
TEST_IDENTITY = {"id": "admin", "name": "管理员", "role": "admin"}


class FakePromptTemplateService:
    def __init__(self):
        self.items = {}
        self.github_calls = []

    def list_templates(self, _identity, **kwargs):
        query = kwargs.get("query", "")
        items = list(self.items.values())
        if query:
            items = [item for item in items if query.lower() in item["title"].lower()]
        return {"items": items}

    def create_template(self, _identity, data):
        item = {
            "id": "template-1",
            "scope": "user",
            "title": data["title"],
            "template_text": data["template_text"],
            "variables": [{"name": "subject", "label": "subject", "default": ""}],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "version": 1,
        }
        self.items[item["id"]] = item
        return item

    def get_template(self, _identity, template_id):
        return self.items.get(template_id)

    def update_template(self, _identity, template_id, data):
        item = self.items.get(template_id)
        if item is None:
            return None
        item.update(data)
        return item

    def delete_template(self, _identity, template_id):
        return self.items.pop(template_id, None) is not None

    def delete_templates(self, _identity, template_ids):
        deleted = 0
        missing_ids = []
        for template_id in template_ids:
            if self.items.pop(template_id, None) is None:
                missing_ids.append(template_id)
            else:
                deleted += 1
        return {"deleted": deleted, "missing_ids": missing_ids}

    def render_template(self, _identity, **kwargs):
        return {
            "template_id": kwargs.get("template_id") or None,
            "rendered_prompt": "Create a cat.",
            "missing_variables": [],
            "variables": [{"name": "subject", "label": "subject", "default": ""}],
        }

    def import_markdown(self, _identity, **_kwargs):
        item = {
            "id": "imported-1",
            "scope": "imported",
            "title": "Imported",
            "template_text": "Create a poster.",
            "variables": [],
        }
        self.items[item["id"]] = item
        return {"items": [item], "imported": 1, "skipped": 0, "candidates": 1}

    def import_github(self, _identity, **kwargs):
        self.github_calls.append(kwargs)
        return {"items": [], "imported": 0, "skipped": 0, "candidates": 0, "errors": []}


class PromptTemplatesApiTests(unittest.TestCase):
    def setUp(self):
        self.fake_service = FakePromptTemplateService()
        self.identity_patcher = mock.patch.object(prompt_templates_module, "require_identity", return_value=TEST_IDENTITY)
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        self.service_patcher = mock.patch.object(prompt_templates_module, "prompt_template_service", self.fake_service)
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)
        app = FastAPI()
        app.include_router(prompt_templates_module.create_router())
        self.client = TestClient(app)

    def test_create_list_get_update_and_delete_template(self):
        create_response = self.client.post(
            "/api/prompt-templates",
            headers=AUTH_HEADERS,
            json={"title": "Poster", "template_text": "Create {{subject}}."},
        )

        self.assertEqual(create_response.status_code, 200, create_response.text)
        self.assertEqual(create_response.json()["id"], "template-1")

        list_response = self.client.get("/api/prompt-templates?query=poster", headers=AUTH_HEADERS)
        self.assertEqual(list_response.status_code, 200, list_response.text)
        self.assertEqual(len(list_response.json()["items"]), 1)

        get_response = self.client.get("/api/prompt-templates/template-1", headers=AUTH_HEADERS)
        self.assertEqual(get_response.status_code, 200, get_response.text)

        update_response = self.client.put(
            "/api/prompt-templates/template-1",
            headers=AUTH_HEADERS,
            json={"title": "Updated"},
        )
        self.assertEqual(update_response.status_code, 200, update_response.text)
        self.assertEqual(update_response.json()["title"], "Updated")

        delete_response = self.client.delete("/api/prompt-templates/template-1", headers=AUTH_HEADERS)
        self.assertEqual(delete_response.status_code, 200, delete_response.text)
        self.assertEqual(delete_response.json(), {"deleted": True})

    def test_bulk_delete_templates(self):
        self.fake_service.items = {
            "template-1": {"id": "template-1", "scope": "user", "title": "One", "template_text": "Create one.", "variables": []},
            "template-2": {"id": "template-2", "scope": "imported", "title": "Two", "template_text": "Create two.", "variables": []},
        }

        response = self.client.post(
            "/api/prompt-templates/delete",
            headers=AUTH_HEADERS,
            json={"ids": ["template-1", "template-2", "missing"]},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"deleted": 2, "missing_ids": ["missing"]})
        self.assertEqual(self.fake_service.items, {})

    def test_render_markdown_import_and_github_import(self):
        render_response = self.client.post(
            "/api/prompt-templates/render",
            headers=AUTH_HEADERS,
            json={"template_text": "Create {{subject}}.", "variables": {"subject": "cat"}},
        )
        self.assertEqual(render_response.status_code, 200, render_response.text)
        self.assertEqual(render_response.json()["rendered_prompt"], "Create a cat.")

        markdown_response = self.client.post(
            "/api/prompt-templates/import/markdown",
            headers=AUTH_HEADERS,
            json={"markdown": "### Prompt\n\n```\nCreate a cinematic poster.\n```"},
        )
        self.assertEqual(markdown_response.status_code, 200, markdown_response.text)
        self.assertEqual(markdown_response.json()["imported"], 1)

        github_response = self.client.post(
            "/api/prompt-templates/import/github",
            headers=AUTH_HEADERS,
            json={
                "repo_url": "https://github.com/YouMind-OpenLab/awesome-gpt-image-2",
                "paths": ["README.md"],
            },
        )
        self.assertEqual(github_response.status_code, 200, github_response.text)
        self.assertEqual(self.fake_service.github_calls[0]["paths"], ["README.md"])

    def test_missing_template_returns_404(self):
        response = self.client.get("/api/prompt-templates/missing", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
