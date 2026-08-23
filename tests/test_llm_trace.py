"""Every provider attempt must be visible in the public execution trace."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app import embeddings  # noqa: E402
from app.llm import LLMClient, LLMError  # noqa: E402


class FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        self.is_success = 200 <= status_code < 300

    def json(self):  # noqa: ANN201
        return self._body

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise AssertionError("The test only raises on a final unexpected response.")


class FakeClient:
    responses: list[FakeResponse] = []

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.timeout = kwargs.get("timeout")

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *args) -> None:  # noqa: ANN002
        return None

    def post(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self.responses.pop(0)


class FailingClient(FakeClient):
    def post(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise httpx.ConnectError("simulated network failure")


class LLMTraceTest(unittest.TestCase):
    def test_successful_call_emits_parsed_trace(self) -> None:
        FakeClient.responses = [
            FakeResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                },
            )
        ]
        steps = []
        with patch.object(config, "llm_enabled", return_value=True), patch(
            "app.llm.httpx.Client", FakeClient
        ):
            client = LLMClient(trace_callback=lambda module, prompt, response: steps.append(
                {"module": module, "prompt": prompt, "response": response}
            ))
            result = client.complete_json("system", "{}", module="FilmAnalyzer")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.usage["calls"], 1)
        self.assertEqual(client.usage["attempts"], 1)
        self.assertEqual(steps[0]["module"], "FilmAnalyzer")
        self.assertEqual(steps[0]["response"], {"ok": True})

    def test_provider_fallback_emits_each_actual_attempt_without_secret(self) -> None:
        FakeClient.responses = [
            FakeResponse(400, {"error": "unsupported reasoning_effort"}),
            FakeResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                },
            ),
        ]
        steps = []
        with patch.object(config, "llm_enabled", return_value=True), patch.object(
            config, "LLM_API_KEY", "test-secret"
        ), patch("app.llm.httpx.Client", FakeClient):
            client = LLMClient(trace_callback=lambda module, prompt, response: steps.append(
                {"module": module, "prompt": prompt, "response": response}
            ))
            result = client.complete_json("system", "{}", module="MatchScorer")

        self.assertEqual(result, {"ok": True})
        self.assertEqual(client.usage["calls"], 1)
        self.assertEqual(client.usage["attempts"], 2)
        self.assertEqual(len(steps), 2)
        self.assertIn("error", steps[0]["response"])
        self.assertEqual(steps[1]["response"], {"ok": True})
        self.assertNotIn("test-secret", json.dumps(steps))
        self.assertEqual(steps[0]["prompt"]["provider"]["kind"], "chat")
        self.assertIsNotNone(steps[0]["prompt"]["provider"]["reasoning_effort_sent"])
        self.assertIsNone(steps[1]["prompt"]["provider"]["reasoning_effort_sent"])

    def test_empty_content_retry_emits_both_attempts_in_order(self) -> None:
        FakeClient.responses = [
            FakeResponse(
                200,
                {
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 100},
                },
            ),
            FakeResponse(
                200,
                {
                    "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                },
            ),
        ]
        steps = []
        with patch.object(config, "llm_enabled", return_value=True), patch(
            "app.llm.httpx.Client", FakeClient
        ):
            client = LLMClient(trace_callback=lambda module, prompt, response: steps.append(
                {"module": module, "prompt": prompt, "response": response}
            ))
            result = client.complete_json("system", "{}", module="RoadmapBuilder", max_tokens=100)

        self.assertEqual(result, {"ok": True})
        self.assertEqual([step["prompt"]["provider"]["attempt"] for step in steps], [1, 2])
        self.assertEqual(steps[0]["response"]["error"], "empty_content")
        self.assertEqual(steps[1]["response"], {"ok": True})

    def test_embedding_model_attempt_is_traced_without_vector_payload(self) -> None:
        FakeClient.responses = [
            FakeResponse(200, {"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})
        ]
        steps = []
        with patch.object(config, "LLM_API_KEY", "test-secret"), patch.object(
            config, "LLM_EMBED_MODEL", "test-embedding"
        ), patch("app.embeddings.httpx.Client", FakeClient):
            vectors = embeddings.embed(
                ["festival query"], input_type="query",
                trace_callback=lambda module, prompt, response: steps.append(
                    {"module": module, "prompt": prompt, "response": response}
                ),
            )

        self.assertEqual(vectors, [[0.1, 0.2, 0.3]])
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["module"], "FestivalSearch")
        self.assertEqual(steps[0]["prompt"]["provider"]["kind"], "embedding")
        self.assertEqual(steps[0]["response"], {"status": "ok", "vectors": 1, "dimension": 3})
        self.assertNotIn("0.1", json.dumps(steps))

    def test_chat_transport_error_attempt_is_traced_before_raising(self) -> None:
        steps = []
        with patch.object(config, "llm_enabled", return_value=True), patch(
            "app.llm.httpx.Client", FailingClient
        ):
            client = LLMClient(trace_callback=lambda module, prompt, response: steps.append(
                {"module": module, "prompt": prompt, "response": response}
            ))
            with self.assertRaisesRegex(LLMError, "Could not reach"):
                client.complete_json("system", "{}", module="FilmAnalyzer")

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["prompt"]["provider"]["attempt"], 1)
        self.assertIn("Could not reach", steps[0]["response"]["error"])

    def test_embedding_error_attempt_is_traced_before_fallback(self) -> None:
        FakeClient.responses = [FakeResponse(503, {"error": "unavailable"})]
        steps = []
        with patch.object(config, "LLM_API_KEY", "test-secret"), patch.object(
            config, "LLM_EMBED_MODEL", "test-embedding"
        ), patch("app.embeddings.httpx.Client", FakeClient):
            with self.assertRaises(embeddings.EmbeddingError):
                embeddings.embed(
                    ["festival query"], input_type="query",
                    trace_callback=lambda module, prompt, response: steps.append(
                        {"module": module, "prompt": prompt, "response": response}
                    ),
                )

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["prompt"]["provider"]["kind"], "embedding")
        self.assertIn("503", steps[0]["response"]["error"])

    def test_successful_chat_with_non_object_json_is_still_traced(self) -> None:
        FakeClient.responses = [FakeResponse(200, [{"unexpected": "array"}])]
        steps = []
        with patch.object(config, "llm_enabled", return_value=True), patch(
            "app.llm.httpx.Client", FakeClient
        ):
            client = LLMClient(trace_callback=lambda module, prompt, response: steps.append(
                {"module": module, "prompt": prompt, "response": response}
            ))
            with self.assertRaisesRegex(LLMError, "not an object"):
                client.complete_json("system", "{}", module="FilmAnalyzer")

        self.assertEqual(len(steps), 1)
        self.assertIn("not an object", steps[0]["response"]["error"])

    def test_successful_embedding_with_malformed_vector_is_still_traced(self) -> None:
        FakeClient.responses = [FakeResponse(200, {"data": [{"index": 0}]})]
        steps = []
        with patch.object(config, "LLM_API_KEY", "test-secret"), patch.object(
            config, "LLM_EMBED_MODEL", "test-embedding"
        ), patch("app.embeddings.httpx.Client", FakeClient):
            with self.assertRaisesRegex(embeddings.EmbeddingError, "malformed vector"):
                embeddings.embed(
                    ["festival query"], input_type="query",
                    trace_callback=lambda module, prompt, response: steps.append(
                        {"module": module, "prompt": prompt, "response": response}
                    ),
                )

        self.assertEqual(len(steps), 1)
        self.assertIn("malformed vector", steps[0]["response"]["error"])


if __name__ == "__main__":
    unittest.main()
