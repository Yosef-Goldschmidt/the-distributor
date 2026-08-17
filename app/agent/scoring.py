"""Deterministic match scoring.

The LLM rates each dimension 0-5 with evidence; this module owns the weights and
the arithmetic so scores stay reproducible and auditable.
"""

from __future__ import annotations

from datetime import date
from typing import Any

WEIGHTS: dict[str, int] = {
    "thematic_fit": 25,
    "genre_fit": 15,
    "lineup_similarity": 20,
    "company_relationship": 15,
    "strategic_value": 15,
    "deadline_urgency": 10,
}

# Rated by the LLM; deadline_urgency is computed in code from the calendar.
LLM_DIMENSIONS = [
    "thematic_fit",
    "genre_fit",
    "lineup_similarity",
    "company_relationship",
    "strategic_value",
]

PREMIERE_PENALTY: dict[str, int] = {"high": 15, "medium": 7, "low": 0, "none": 0}

DIMENSION_LABELS: dict[str, str] = {
    "thematic_fit": "Thematic fit",
    "genre_fit": "Genre fit",
    "lineup_similarity": "Past lineup / winner similarity",
    "company_relationship": "Company relationship history",
    "strategic_value": "Strategic value",
    "deadline_urgency": "Deadline urgency",
}

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def months_until_deadline(deadline_month: str | None, today: date) -> int | None:
    """Months from today to the next occurrence of a recurring deadline month."""

    if not deadline_month or deadline_month not in MONTHS:
        return None
    target = MONTHS.index(deadline_month) + 1
    delta = (target - today.month) % 12
    return delta


def deadline_urgency(deadline_month: str | None, today: date) -> tuple[float, str]:
    """Rate 0-5 how urgent a recurring annual deadline is, and say why.

    Computed in code so MatchScorer and RiskChecker can never disagree.
    """

    delta = months_until_deadline(deadline_month, today)
    if delta is None:
        return 2.0, "Deadline month unknown; treated as neutral."
    if delta == 0:
        return 5.0, f"Deadline falls this month ({deadline_month}) — act now."
    if delta == 1:
        return 5.0, f"Deadline next month ({deadline_month}) — act now."
    if delta == 2:
        return 4.0, f"Deadline in about {delta} months ({deadline_month})."
    if delta <= 4:
        return 3.0, f"Deadline in about {delta} months ({deadline_month})."
    if delta <= 7:
        return 2.0, f"Deadline in about {delta} months ({deadline_month}) — plan ahead."
    return 1.0, f"Deadline about {delta} months away ({deadline_month}) — next cycle."


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
    """Map a scored candidate onto the roadmap buckets from the pitch deck.

    Leverage is reserved for strong existing relationships that are NOT the
    film's premiere play — a top-scoring festival always belongs in Submit First.
    """

    score = candidate.get("score", 0)
    premiere_risk = (candidate.get("premiere_risk") or "none").lower()
    deadline_status = (candidate.get("deadline_status") or "open").lower()
    eligible = candidate.get("eligible", True)
    opportunity = bool(candidate.get("premiere_opportunity"))
    relationship = _rating(candidate.get("ratings", {}).get("company_relationship"))
    tier = (candidate.get("tier") or "C").upper()

    if not eligible or score < 45:
        return "hold_avoid"
    # A festival the film can still world-premiere at is an opportunity, so it is
    # never demoted for premiere risk alone.
    if premiere_risk == "high" and not opportunity:
        return "hold_avoid"
    if deadline_status == "closed":
        # A real fit whose window has passed is a next-cycle target, not a reject.
        return "hold_avoid" if score < 70 else "prioritize_next"
    if score >= 72 or (tier in {"A", "B+"} and score >= 65):
        return "submit_first"
    if relationship >= 4 and score >= 55:
        return "leverage"
    return "prioritize_next"


def rank(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(candidates, key=lambda c: (c.get("score", 0), c.get("retrieval_score", 0)), reverse=True)


def weights_documentation() -> list[dict[str, Any]]:
    return [
        {"dimension": key, "label": DIMENSION_LABELS[key], "weight": value}
        for key, value in WEIGHTS.items()
    ]
