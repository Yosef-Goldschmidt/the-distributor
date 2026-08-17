"""Minimal OpenAI-compatible chat client for the LLMod.ai provider."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app import config


class LLMError(RuntimeError):
    pass


class LLMClient:
    """Thin wrapper around POST {base_url}/chat/completions.

    Tracks token usage per run so the GUI can surface budget consumption.
    """

    def __init__(self) -> None:
        self.enabled = config.llm_enabled()
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = True,
    ) -> str:
        if not self.enabled:
            raise LLMError(
                "LLM_API_KEY is not configured. Set LLM_API_KEY, LLM_BASE_URL and "
                "LLM_MODEL (LLMod.ai credentials) in the environment."
            )

        payload: dict[str, Any] = {
            "model": config.LLM_MODEL,
            "temperature": temperature,
            "max_tokens": max_tokens or config.LLM_MAX_OUTPUT_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS) as client:
                response = client.post(url, headers=headers, json=payload)
                if response.status_code == 400 and json_mode:
                    # Some deployments reject response_format; retry without it.
                    payload.pop("response_format", None)
                    response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(
                f"LLM provider returned {exc.response.status_code}: {exc.response.text[:400]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach the LLM provider: {exc}") from exc

        usage = body.get("usage") or {}
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)

        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {json.dumps(body)[:400]}") from exc

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
