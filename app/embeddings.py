"""Embedding provider abstraction.

EMBED_PROVIDER=llm       -> LLMod.ai embedding model (OpenAI-compatible /embeddings)
EMBED_PROVIDER=pinecone  -> Pinecone hosted inference

Both return plain float vectors, so the seeding script and the query path share
one implementation and can never disagree about dimensionality.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from app import config


class EmbeddingError(RuntimeError):
    pass


TraceCallback = Callable[[str, Any, Any], None]


def _trace_prompt(
    texts: list[str], input_type: str, provider: str, model: str
) -> dict[str, Any]:
    return {
        "operation": "embedding_request",
        "input": list(texts),
        "provider": {
            "kind": "embedding",
            "service": provider,
            "model": model,
            "attempt": 1,
            "input_type": input_type,
        },
    }


def _embed_llm(
    texts: list[str], input_type: str, trace_callback: TraceCallback | None
) -> list[list[float]]:
    if not (config.LLM_API_KEY and config.LLM_EMBED_MODEL):
        raise EmbeddingError("LLM_API_KEY and LLM_EMBED_MODEL must be set for EMBED_PROVIDER=llm.")

    url = f"{config.LLM_BASE_URL.rstrip('/')}/embeddings"
    prompt = _trace_prompt(texts, input_type, "llmod.ai", config.LLM_EMBED_MODEL)
    try:
        with httpx.Client(timeout=config.EMBED_TIMEOUT_SECONDS) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {config.LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": config.LLM_EMBED_MODEL, "input": texts},
            )
    except httpx.HTTPError as exc:
        if trace_callback:
            trace_callback("FestivalSearch", prompt, {"error": f"Embedding transport error: {type(exc).__name__}"})
        raise EmbeddingError(f"Could not reach the embedding provider: {exc}") from exc

    if not response.is_success:
        traced = {
            "error": f"Embedding provider returned {response.status_code}",
            "body": response.text[:300],
        }
        if trace_callback:
            trace_callback("FestivalSearch", prompt, traced)
        raise EmbeddingError(
            f"Embedding provider returned {response.status_code}: {response.text[:300]}"
        )
    try:
        body = response.json()
    except ValueError as exc:
        if trace_callback:
            trace_callback("FestivalSearch", prompt, {"error": "Embedding provider returned non-JSON content."})
        raise EmbeddingError("Embedding provider returned non-JSON content.") from exc

    if not isinstance(body, dict):
        if trace_callback:
            trace_callback(
                "FestivalSearch", prompt,
                {"error": "Embedding provider returned JSON that is not an object."},
            )
        raise EmbeddingError("Embedding provider returned JSON that is not an object.")

    raw_data = body.get("data", [])
    if not isinstance(raw_data, list) or any(not isinstance(item, dict) for item in raw_data):
        if trace_callback:
            trace_callback(
                "FestivalSearch", prompt,
                {"error": "Embedding provider returned malformed data items."},
            )
        raise EmbeddingError("Embedding provider returned malformed data items.")
    data = sorted(raw_data, key=lambda item: item.get("index", 0))
    if len(data) != len(texts):
        if trace_callback:
            trace_callback(
                "FestivalSearch",
                prompt,
                {"error": f"Expected {len(texts)} embeddings, received {len(data)}."},
            )
        raise EmbeddingError(f"Expected {len(texts)} embeddings, received {len(data)}.")
    try:
        vectors = [item["embedding"] for item in data]
    except (KeyError, TypeError) as exc:
        if trace_callback:
            trace_callback(
                "FestivalSearch", prompt,
                {"error": "Embedding provider returned malformed vector items."},
            )
        raise EmbeddingError("Embedding provider returned malformed vector items.") from exc
    if trace_callback:
        trace_callback(
            "FestivalSearch",
            prompt,
            {
                "status": "ok",
                "vectors": len(vectors),
                "dimension": len(vectors[0]) if vectors else 0,
            },
        )
    return vectors


def _embed_pinecone(
    texts: list[str], input_type: str, trace_callback: TraceCallback | None
) -> list[list[float]]:
    from pinecone import Pinecone

    prompt = _trace_prompt(texts, input_type, "pinecone", config.PINECONE_EMBED_MODEL)
    try:
        client = Pinecone(
            api_key=config.PINECONE_API_KEY,
            timeout=config.PINECONE_TIMEOUT_SECONDS,
        )
        result = client.inference.embed(
            model=config.PINECONE_EMBED_MODEL,
            inputs=texts,
            parameters={"input_type": input_type, "truncate": "END"},
        )
        vectors = [item["values"] for item in result.data]
    except Exception as exc:  # noqa: BLE001 - normalize provider SDK failures
        if trace_callback:
            trace_callback(
                "FestivalSearch", prompt,
                {"error": f"Pinecone embedding error: {type(exc).__name__}"},
            )
        raise EmbeddingError(f"Pinecone embedding failed: {type(exc).__name__}") from exc
    if trace_callback:
        trace_callback(
            "FestivalSearch",
            prompt,
            {
                "status": "ok",
                "vectors": len(vectors),
                "dimension": len(vectors[0]) if vectors else 0,
            },
        )
    return vectors


def embed(
    texts: list[str],
    *,
    input_type: str = "passage",
    trace_callback: TraceCallback | None = None,
) -> list[list[float]]:
    if not texts:
        return []
    if config.EMBED_PROVIDER == "pinecone":
        return _embed_pinecone(texts, input_type, trace_callback)
    return _embed_llm(texts, input_type, trace_callback)


def dimension() -> int:
    """Probe the provider once to learn the vector size (used at index creation)."""

    return len(embed(["dimension probe"])[0])
