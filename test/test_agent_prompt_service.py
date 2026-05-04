from __future__ import annotations

import json
import unittest
from unittest import mock

from services.agent_prompt_service import AgentPromptService, DEFAULT_AGENT_SYSTEM_PROMPT


class AgentPromptServiceTests(unittest.TestCase):
    def test_optimize_prompt_parses_json_response(self):
        captured = {}

        def generator(messages, model):
            captured["messages"] = messages
            captured["model"] = model
            return json.dumps(
                {
                    "final_prompt": "Cinematic poster of Nanjing skyline, premium travel campaign.",
                    "style_consistency_notes": ["Keep warm city lights"],
                    "sequence_plan": ["Frame 1 wide view", "Frame 2 close-up"],
                    "safety_notes": ["No unsafe content"],
                },
                ensure_ascii=False,
            )

        service = AgentPromptService(generator)
        result = service.optimize_prompt(prompt="生成两张连续的南京城市宣传海报，第一张展示城市天际线，第二张展示夜景灯光", model="auto", mode="sequence", count=2, size="16:9")

        self.assertEqual(result["final_prompt"], "Cinematic poster of Nanjing skyline, premium travel campaign.")
        self.assertEqual(result["style_consistency_notes"], ["Keep warm city lights"])
        self.assertEqual(result["sequence_plan"], ["Frame 1 wide view", "Frame 2 close-up"])
        self.assertEqual(captured["model"], "auto")
        self.assertEqual(captured["messages"][0]["role"], "system")
        self.assertIn("目标图片数量: 2", captured["messages"][1]["content"])

    def test_optimize_prompt_parses_fenced_json(self):
        service = AgentPromptService(
            lambda _messages, _model: '```json\n{"final_prompt":"Create a clean product render.","safety_notes":["ok"]}\n```'
        )

        result = service.optimize_prompt(prompt="生成一张白底电商产品主图，突出金属水杯质感")

        self.assertEqual(result["final_prompt"], "Create a clean product render.")
        self.assertEqual(result["safety_notes"], ["ok"])

    def test_optimize_prompt_falls_back_to_plain_text(self):
        service = AgentPromptService(lambda _messages, _model: "Create a polished portrait with soft studio light.")

        result = service.optimize_prompt(prompt="生成一张职场头像，柔和棚拍灯光，干净背景")

        self.assertEqual(result["final_prompt"], "Create a polished portrait with soft studio light.")
        self.assertEqual(result["original_prompt"], "生成一张职场头像，柔和棚拍灯光，干净背景")

    def test_empty_prompt_is_rejected(self):
        service = AgentPromptService(lambda _messages, _model: "{}")

        with self.assertRaises(ValueError):
            service.optimize_prompt(prompt="  ")

    def test_external_agent_service_uses_openai_compatible_config(self):
        captured = {}
        fake_response = mock.Mock()
        fake_response.json.return_value = {"choices": [{"message": {"content": '{"final_prompt":"External optimized."}'}}]}

        with mock.patch("services.agent_prompt_service.config") as fake_config:
            fake_config.agent_service = {
                "provider": "openai_compatible",
                "base_url": "https://agent.example.test",
                "api_key": "sk-test",
                "model": "gpt-4.1-mini",
                "prompt": "Custom agent prompt",
            }
            with mock.patch("services.agent_prompt_service.proxy_settings.build_session_kwargs", return_value={}):
                with mock.patch("services.agent_prompt_service.requests.post", return_value=fake_response) as post:
                    service = AgentPromptService()
                    result = service.optimize_prompt(prompt="生成一张产品海报，白底，突出质感")

        self.assertEqual(result["final_prompt"], "External optimized.")
        post.assert_called_once()
        kwargs = post.call_args.kwargs
        captured["json"] = kwargs["json"]
        self.assertEqual(post.call_args.args[0], "https://agent.example.test/v1/chat/completions")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(captured["json"]["model"], "gpt-4.1-mini")
        self.assertEqual(captured["json"]["messages"][0]["content"], "Custom agent prompt")

    def test_default_agent_prompt_is_used_without_custom_config(self):
        with mock.patch("services.agent_prompt_service.config") as fake_config:
            fake_config.agent_service = {"provider": "internal", "prompt": ""}
            service = AgentPromptService(lambda messages, _model: json.dumps({"final_prompt": messages[0]["content"]}))
            result = service.optimize_prompt(prompt="生成一张干净的产品海报")

        self.assertEqual(result["final_prompt"], DEFAULT_AGENT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
