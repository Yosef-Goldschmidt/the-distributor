"""Pinecone-backed semantic retrieval over the festival corpus."""

from __future__ import annotations

from typing import Any

from app import config, embeddings
from app.stores import corpus

_client: Any | None = None


def _pinecone():
    global _client
    if _client is None:
        from pinecone import Pinecone

        _client = Pinecone(api_key=config.PINECONE_API_KEY)
    return _client


def search(query_text: str, top_k: int) -> tuple[list[tuple[str, float]], str]:
    """Return [(festival_id, score)] plus the retrieval backend actually used."""

    if not (config.pinecone_enabled() and config.embeddings_enabled()):
        return corpus.lexical_search(query_text, top_k), "local_tfidf_fallback"

    try:
        vector = embeddings.embed([query_text], input_type="query")[0]
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
            return corpus.lexical_search(query_text, top_k), "local_tfidf_fallback"
        return matches, f"pinecone:{config.PINECONE_INDEX}/{config.PINECONE_NAMESPACE}"
    except Exception:  # noqa: BLE001 - retrieval must never break a run
        return corpus.lexical_search(query_text, top_k), "local_tfidf_fallback"
