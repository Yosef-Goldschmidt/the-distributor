"""Local festival corpus: the ground truth that backs every retrieval path."""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from typing import Any

from app import config

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "film", "films", "for",
    "from", "has", "have", "in", "is", "it", "its", "of", "on", "or", "that", "the",
    "their", "this", "to", "with", "who", "what", "about", "into", "over",
}


@lru_cache(maxsize=1)
def load_festivals() -> list[dict[str, Any]]:
    path = config.DATA_DIR / "festivals.json"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


@lru_cache(maxsize=1)
def festivals_by_id() -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in load_festivals() if f.get("id")}


def embedding_text(festival: dict[str, Any]) -> str:
    """The festival 'identity' text that gets embedded for semantic matching."""

    location = ", ".join(
        part for part in [festival.get("city"), festival.get("country"), festival.get("region")] if part
    )
    parts = [
        festival.get("name", ""),
        location,
        f"Tier {festival.get('tier', '')} {festival.get('category', '')} festival held in {festival.get('month') or 'an unspecified month'}.",
        festival.get("focus") or "",
        "Themes: " + ", ".join(festival.get("themes", []) or []),
        "Accepts: " + ", ".join(festival.get("accepts", []) or []),
        "Notable past selections: " + ", ".join(festival.get("notable_past_selections", []) or []),
        "Award patterns: " + (festival.get("award_patterns") or ""),
        "Strategic value: " + (festival.get("strategic_value") or ""),
        festival.get("notes") or "",
    ]
    return "\n".join(part for part in parts if part and part.strip())


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_]+", (text or "").lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


@lru_cache(maxsize=1)
def _lexical_index() -> tuple[dict[str, float], list[tuple[str, dict[str, int]]]]:
    """Build a small in-process TF-IDF index over the corpus."""

    docs: list[tuple[str, dict[str, int]]] = []
    doc_freq: dict[str, int] = {}
    for festival in load_festivals():
        counts: dict[str, int] = {}
        for token in _tokenize(embedding_text(festival)):
            counts[token] = counts.get(token, 0) + 1
        docs.append((festival["id"], counts))
        for token in counts:
            doc_freq[token] = doc_freq.get(token, 0) + 1

    total = max(len(docs), 1)
    idf = {token: math.log(1 + total / (1 + freq)) for token, freq in doc_freq.items()}
    return idf, docs


def lexical_search(query: str, top_k: int) -> list[tuple[str, float]]:
    """Deterministic offline retrieval used when Pinecone is not configured."""

    idf, docs = _lexical_index()
    query_tokens = _tokenize(query)
    if not query_tokens:
        return [(fid, 0.0) for fid, _ in docs[:top_k]]

    query_counts: dict[str, int] = {}
    for token in query_tokens:
        query_counts[token] = query_counts.get(token, 0) + 1

    scored: list[tuple[str, float]] = []
    for festival_id, counts in docs:
        doc_norm = math.sqrt(sum((c * idf.get(t, 0.0)) ** 2 for t, c in counts.items())) or 1.0
        dot = sum(
            (counts.get(token, 0) * idf.get(token, 0.0)) * (count * idf.get(token, 0.0))
            for token, count in query_counts.items()
        )
        scored.append((festival_id, dot / doc_norm))

    scored.sort(key=lambda item: item[1], reverse=True)
    top = scored[:top_k]
    best = top[0][1] if top and top[0][1] > 0 else 1.0
    return [(fid, round(score / best, 4)) for fid, score in top]


def compact_for_prompt(festival: dict[str, Any]) -> dict[str, Any]:
    """Trimmed festival record for LLM prompts — keeps context (and cost) small."""

    return {
        "id": festival.get("id"),
        "name": festival.get("name"),
        "country": festival.get("country"),
        "tier": festival.get("tier"),
        "category": festival.get("category"),
        "month": festival.get("month"),
        "deadline_month": festival.get("typical_deadline_month"),
        "last_recorded_deadline": festival.get("final_deadline"),
        "premiere_requirement": festival.get("premiere_requirement"),
        "premiere_requirement_raw": festival.get("premiere_requirement_raw"),
        "accepts": festival.get("accepts"),
        "themes": festival.get("themes"),
        "focus": festival.get("focus"),
        "award_patterns": festival.get("award_patterns"),
        "notable_past_selections": (festival.get("notable_past_selections") or [])[:4],
        "submission_fee": festival.get("submission_fee"),
        "notes": festival.get("notes"),
        "identity_confidence": festival.get("identity_confidence"),
    }
