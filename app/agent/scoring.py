"""Deterministic match scoring.

The LLM rates each dimension 0-5 with evidence; this module owns the weights and
the arithmetic so scores stay reproducible and auditable.
"""

from __future__ import annotations

from typing import Any

WEIGHTS: dict[str, int] = {
    "thematic_fit": 25,
    "genre_fit": 15,
    "lineup_similarity": 20,
    "company_relationship": 15,
    "strategic_value": 15,
    "deadline_urgency": 10,
}

PREMIERE_PENALTY: dict[str, int] = {"high": 15, "medium": 7, "low": 0, "none": 0}

DIMENSION_LABELS: dict[str, str] = {
    "thematic_fit": "Thematic fit",
    "genre_fit": "Genre fit",
    "lineup_similarity": "Past lineup / winner similarity",
    "company_relationship": "Company relationship history",
    "strategic_value": "Strategic value",
    "deadline_urgency": "Deadline urgency",
}


def _rating(value: Any) -> float:
    try:
        return max(0.0, min(5.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def compute_score(ratings: dict[str, Any], premiere_risk: str | None) -> dict[str, Any]:
    """Weighted 0-100 score from 0-5 dimension ratings, minus a premiere penalty."""

    breakdown: dict[str, dict[str, float]] = {}
    base = 0.0
    for dimension, weight in WEIGHTS.items():
        rating = _rating(ratings.get(dimension))
        points = (rating / 5.0) * weight
        base += points
        breakdown[dimension] = {
            "rating": round(rating, 1),
            "weight": weight,
            "points": round(points, 1),
        }

    penalty = PREMIERE_PENALTY.get((premiere_risk or "none").lower(), 0)
    total = max(0, min(100, round(base - penalty)))
    return {
        "score": total,
        "base_score": round(base, 1),
        "premiere_penalty": penalty,
        "breakdown": breakdown,
    }


def assign_bucket(candidate: dict[str, Any]) -> str:
    """Map a scored candidate onto the roadmap buckets from the pitch deck."""

    score = candidate.get("score", 0)
    premiere_risk = (candidate.get("premiere_risk") or "none").lower()
    deadline_status = (candidate.get("deadline_status") or "open").lower()
    eligible = candidate.get("eligible", True)
    relationship = _rating(
        candidate.get("ratings", {}).get("company_relationship")
    )
    tier = (candidate.get("tier") or "C").upper()
    premiere_requirement = (candidate.get("premiere_requirement") or "none").lower()

    if not eligible or premiere_risk == "high" or deadline_status == "closed" or score < 45:
        return "hold_avoid"
    if relationship >= 4 and score >= 55:
        return "leverage"
    if score >= 72 and (tier == "A" or premiere_requirement in {"world", "international"}):
        return "submit_first"
    return "prioritize_next"


def rank(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(candidates, key=lambda c: (c.get("score", 0), c.get("retrieval_score", 0)), reverse=True)


def weights_documentation() -> list[dict[str, Any]]:
    return [
        {"dimension": key, "label": DIMENSION_LABELS[key], "weight": value}
        for key, value in WEIGHTS.items()
    ]
