from __future__ import annotations

import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.agent as agent_module


AUTH_HEADERS = {"Authorization": "Bearer chatgpt2api"}
TEST_IDENTITY = {"id": "admin", "name": "管理员", "role": "admin"}


class FakeAgentPromptService:
    def __init__(self):
        self.calls = []
        self.fail = False

    def optimize_prompt(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("upstream unavailable")
        if kwargs.get("prompt") == "不清楚":
            return {
                "original_prompt": kwargs["prompt"],
                "final_prompt": "",
                "model": kwargs["model"],
                "mode": kwargs["mode"],
                "count": kwargs["count"],
                "size": kwargs["size"],
                "needs_clarification": True,
                "clarification_questions": ["你想生成什么主体？"],
                "style_consistency_notes": [],
                "sequence_plan": [],
                "safety_notes": [],
                "raw_response": "",
            }
        return {
            "original_prompt": kwargs["prompt"],
            "final_prompt": "Optimized prompt",
            "model": kwargs["model"],
            "mode": kwargs["mode"],
            "count": kwargs["count"],
            "size": kwargs["size"],
            "needs_clarification": False,
            "clarification_questions": [],
            "style_consistency_notes": [],
            "sequence_plan": [],
            "safety_notes": [],
            "raw_response": "{}",
        }


class AgentApiTests(unittest.TestCase):
    def setUp(self):
        self.fake_service = FakeAgentPromptService()
        self.identity_patcher = mock.patch.object(agent_module, "require_identity", return_value=TEST_IDENTITY)
        self.identity_patcher.start()
        self.addCleanup(self.identity_patcher.stop)
        self.service_patcher = mock.patch.object(agent_module, "agent_prompt_service", self.fake_service)
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)
        self.task_patcher = mock.patch.object(agent_module, "image_task_service")
        self.fake_task_service = self.task_patcher.start()
        self.addCleanup(self.task_patcher.stop)
        self.fake_task_service.submit_generation.side_effect = lambda _identity, **kwargs: {
            "id": kwargs["client_task_id"],
            "status": "queued",
            "mode": "generate",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }
        self.fake_task_service.submit_edit.side_effect = lambda _identity, **kwargs: {
            "id": kwargs["client_task_id"],
            "status": "queued",
            "mode": "edit",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }
        app = FastAPI()
        app.include_router(agent_module.create_router())
        self.client = TestClient(app)

    def test_prompt_optimize(self):
        response = self.client.post(
            "/api/agent/prompt-optimize",
            headers=AUTH_HEADERS,
            json={"prompt": "南京城市宣传海报", "mode": "sequence", "count": 2, "size": "16:9"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["final_prompt"], "Optimized prompt")
        self.assertEqual(self.fake_service.calls[0]["mode"], "sequence")
        self.assertEqual(self.fake_service.calls[0]["count"], 2)

    def test_prompt_optimize_reports_upstream_error(self):
        self.fake_service.fail = True

        response = self.client.post(
            "/api/agent/prompt-optimize",
            headers=AUTH_HEADERS,
            json={"prompt": "南京城市宣传海报"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["error"], "upstream unavailable")

    def test_image_run_can_ask_for_clarification(self):
        response = self.client.post(
            "/api/agent/image-run",
            headers=AUTH_HEADERS,
            data={"prompt": "不清楚", "run_id": "run-1", "count": "2"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "needs_clarification")
        self.assertEqual(payload["questions"], ["你想生成什么主体？"])
        self.assertFalse(self.fake_task_service.submit_generation.called)

    def test_image_run_creates_generation_tasks(self):
        response = self.client.post(
            "/api/agent/image-run",
            headers=AUTH_HEADERS,
            data={"prompt": "南京海报", "run_id": "run-2", "count": "2", "size": "16:9"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["prompt"], "Optimized prompt")
        self.assertEqual([task["id"] for task in payload["tasks"]], ["agent-run-2-1", "agent-run-2-2"])
        self.assertEqual(self.fake_task_service.submit_generation.call_count, 2)

    def test_image_run_creates_edit_tasks_when_images_are_uploaded(self):
        response = self.client.post(
            "/api/agent/image-run",
            headers=AUTH_HEADERS,
            data={"prompt": "把参考图改成海报", "run_id": "run-3", "count": "1"},
            files=[("image", ("one.png", b"one", "image/png"))],
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["mode"], "edit")
        self.assertEqual(self.fake_task_service.submit_edit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
