"""Minimal OpenAI-compatible chat client for the LLMod.ai provider.

Written defensively because the gateway fronts several model families: reasoning
models (gpt-5-*) reject a custom temperature and spend part of the output budget
on hidden reasoning tokens, while older deployments expect `max_tokens` rather
than `max_completion_tokens`.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app import config


class LLMError(RuntimeError):
    pass


def _extract(body: dict[str, Any]) -> tuple[str, str | None]:
    """Return (content, finish_reason) from a chat-completions payload."""

    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Unexpected LLM response shape: {json.dumps(body)[:400]}") from exc
    message = choice.get("message") or {}
    return (message.get("content") or ""), choice.get("finish_reason")


class LLMClient:
    """Thin wrapper around POST {base_url}/chat/completions.

    Tracks token usage per run so the GUI can surface budget consumption.
    """

    def __init__(self) -> None:
        self.enabled = config.llm_enabled()
        self.usage = {
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
        }

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = True,
    ) -> str:
        if not self.enabled:
            raise LLMError(
                "LLM_API_KEY is not configured. Set LLM_API_KEY, LLM_BASE_URL and "
                "LLM_MODEL (LLMod.ai credentials) in the environment."
            )

        budget = max_tokens or config.LLM_MAX_OUTPUT_TOKENS
        payload: dict[str, Any] = {
            "model": config.LLM_MODEL,
            "max_completion_tokens": budget,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        # Reasoning models accept only temperature=1, so a value is sent solely
        # when LLM_TEMPERATURE is set explicitly.
        chosen_temperature = temperature if temperature is not None else config.LLM_TEMPERATURE
        if chosen_temperature is not None:
            payload["temperature"] = chosen_temperature
        if config.LLM_REASONING_EFFORT:
            payload["reasoning_effort"] = config.LLM_REASONING_EFFORT
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.LLM_API_KEY}",
            "Content-Type": "application/json",
        }
        # Optional params some deployments reject; dropped one at a time on 400.
        fallbacks = ["temperature", "reasoning_effort", "response_format", "max_completion_tokens"]

        def call() -> dict[str, Any]:
            try:
                with httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS) as client:
                    response = client.post(url, headers=headers, json=payload)
                    while response.status_code == 400 and fallbacks:
                        param = fallbacks.pop(0)
                        if param == "max_completion_tokens" and param in payload:
                            payload["max_tokens"] = payload.pop(param)
                        elif param in payload:
                            del payload[param]
                        else:
                            continue
                        response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    return response.json()
            except httpx.HTTPStatusError as exc:
                raise LLMError(
                    f"LLM provider returned {exc.response.status_code}: {exc.response.text[:400]}"
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"Could not reach the LLM provider: {exc}") from exc

        body = call()
        self._record(body)
        content, finish_reason = _extract(body)

        # A reasoning model can burn the whole budget on hidden reasoning tokens and
        # return empty content; retry once with a larger ceiling.
        if not content.strip():
            key = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
            raised = min(int(payload.get(key) or budget) * 3, config.LLM_MAX_OUTPUT_TOKENS_CEILING)
            if raised > int(payload.get(key) or budget):
                payload[key] = raised
                body = call()
                self._record(body)
                content, finish_reason = _extract(body)

        if not content.strip():
            raise LLMError(
                f"The model returned no content (finish_reason={finish_reason}). "
                "The output budget is likely being consumed by reasoning tokens — raise "
                "LLM_MAX_OUTPUT_TOKENS or set LLM_REASONING_EFFORT=low."
            )
        return content

    def _record(self, body: dict[str, Any]) -> None:
        usage = body.get("usage") or {}
        details = usage.get("completion_tokens_details") or {}
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        self.usage["reasoning_tokens"] += int(details.get("reasoning_tokens") or 0)

    def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:
        raw = self.complete(system, user, **kwargs)
        return parse_json_object(raw)


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object out of a model response, tolerating fences/prose."""

    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError(f"Model did not return JSON: {text[:300]}")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"Model returned malformed JSON: {text[:300]}") from exc

    if not isinstance(parsed, dict):
        raise LLMError("Model returned JSON that is not an object.")
    return parsed
