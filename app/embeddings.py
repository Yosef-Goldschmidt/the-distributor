"""Embedding provider abstraction.

EMBED_PROVIDER=llm       -> LLMod.ai embedding model (OpenAI-compatible /embeddings)
EMBED_PROVIDER=pinecone  -> Pinecone hosted inference

Both return plain float vectors, so the seeding script and the query path share
one implementation and can never disagree about dimensionality.
"""

from __future__ import annotations

import httpx

from app import config


class EmbeddingError(RuntimeError):
    pass


def _embed_llm(texts: list[str]) -> list[list[float]]:
    if not (config.LLM_API_KEY and config.LLM_EMBED_MODEL):
        raise EmbeddingError("LLM_API_KEY and LLM_EMBED_MODEL must be set for EMBED_PROVIDER=llm.")

    url = f"{config.LLM_BASE_URL.rstrip('/')}/embeddings"
    try:
        with httpx.Client(timeout=config.LLM_TIMEOUT_SECONDS) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {config.LLM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": config.LLM_EMBED_MODEL, "input": texts},
            )
            response.raise_for_status()
            body = response.json()
    except httpx.HTTPStatusError as exc:
        raise EmbeddingError(
            f"Embedding provider returned {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"Could not reach the embedding provider: {exc}") from exc

    data = sorted(body.get("data", []), key=lambda item: item.get("index", 0))
    if len(data) != len(texts):
        raise EmbeddingError(f"Expected {len(texts)} embeddings, received {len(data)}.")
    return [item["embedding"] for item in data]


def _embed_pinecone(texts: list[str], input_type: str) -> list[list[float]]:
    from pinecone import Pinecone

    client = Pinecone(api_key=config.PINECONE_API_KEY)
    result = client.inference.embed(
        model=config.PINECONE_EMBED_MODEL,
        inputs=texts,
        parameters={"input_type": input_type, "truncate": "END"},
    )
    return [item["values"] for item in result.data]


def embed(texts: list[str], *, input_type: str = "passage") -> list[list[float]]:
    if not texts:
        return []
    if config.EMBED_PROVIDER == "pinecone":
        return _embed_pinecone(texts, input_type)
    return _embed_llm(texts)


def dimension() -> int:
    """Probe the provider once to learn the vector size (used at index creation)."""

    return len(embed(["dimension probe"])[0])
