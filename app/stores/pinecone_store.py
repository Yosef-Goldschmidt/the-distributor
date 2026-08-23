"""Pinecone-backed semantic retrieval over the festival corpus."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app import config, embeddings
from app.stores import corpus

_client: Any | None = None


def _pinecone():
    global _client
    if _client is None:
        from pinecone import Pinecone

        _client = Pinecone(
            api_key=config.PINECONE_API_KEY,
            timeout=config.PINECONE_TIMEOUT_SECONDS,
        )
    return _client


def search(
    query_text: str,
    top_k: int,
    *,
    trace_callback: Callable[[str, Any, Any], None] | None = None,
) -> tuple[list[tuple[str, float]], str, str | None]:
    """Return matches, actual backend and an explicit fallback reason."""

    if not (config.pinecone_enabled() and config.embeddings_enabled()):
        return (
            corpus.lexical_search(query_text, top_k),
            "local_tfidf_fallback",
            "vector_retrieval_not_configured",
        )

    try:
        vector = embeddings.embed(
            [query_text], input_type="query", trace_callback=trace_callback
        )[0]
        index = _pinecone().Index(config.PINECONE_INDEX)
        response = index.query(
            vector=vector,
            top_k=top_k,
            namespace=config.PINECONE_NAMESPACE,
            include_metadata=False,
        )
        matches = [
            (match["id"], round(float(match.get("score") or 0.0), 4))
            for match in response.get("matches", [])
        ]
        known = corpus.festivals_by_id()
        matches = [match for match in matches if match[0] in known]
        if not matches:
            return (
                corpus.lexical_search(query_text, top_k),
                "local_tfidf_fallback",
                "vector_query_returned_no_known_matches",
            )
        return (
            matches,
            f"pinecone:{config.PINECONE_INDEX}/{config.PINECONE_NAMESPACE}",
            None,
        )
    except Exception as exc:  # noqa: BLE001 - retrieval must never break a run
        return (
            corpus.lexical_search(query_text, top_k),
            "local_tfidf_fallback",
            f"vector_retrieval_error:{type(exc).__name__}",
        )
