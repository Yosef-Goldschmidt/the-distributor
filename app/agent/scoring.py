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

# Rated by the LLM. Company relationship and deadline urgency are computed from
# source data in code.
LLM_DIMENSIONS = [
    "thematic_fit",
    "genre_fit",
    "lineup_similarity",
    "strategic_value",
]

PREMIERE_PENALTY: dict[str, int] = {"high": 15, "medium": 7, "low": 0, "none": 0}

TIER_STRATEGIC_CAP: dict[str, float] = {"A": 5.0, "B+": 4.5, "B": 4.0, "C": 3.0}

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


def company_relationship_rating(
    history_rows: list[dict[str, Any]], current_year: int
) -> tuple[float, str, dict[str, Any]]:
    """Compute relationship strength from observed screenings, recency and awards.

    This dimension is factual company memory, so it is more defensible and less
    expensive to compute it in code than to ask the LLM to estimate it.
    """

    if not history_rows:
        return 0.0, "No prior company relationship is recorded.", {
            "screenings": 0, "latest_year": None, "award_count": 0,
        }

    screenings = 0
    years: list[int] = []
    award_count = 0
    for row in history_rows:
        row_screenings = row.get("screenings")
        if row_screenings is None and row.get("film_title"):
            row_screenings = 1
        try:
            screenings += max(0, int(row_screenings or 0))
        except (TypeError, ValueError):
            pass
        row_years = row.get("years") or ([row.get("year")] if row.get("year") else [])
        for value in row_years:
            try:
                years.append(int(value))
            except (TypeError, ValueError):
                continue
        awards = row.get("awards") or []
        award_count += len(awards)
        if not awards and (row.get("result") == "awarded" or row.get("award")):
            award_count += 1

    if screenings <= 0:
        base = 1.0
    elif screenings == 1:
        base = 1.5
    elif screenings <= 3:
        base = 2.5
    elif screenings <= 7:
        base = 3.5
    else:
        base = 4.0

    latest_year = max(years) if years else None
    recency_bonus = 0.0
    if latest_year is not None:
        if latest_year >= current_year - 3:
            recency_bonus = 0.5
        elif latest_year >= current_year - 6:
            recency_bonus = 0.25
    award_bonus = 0.75 if award_count else 0.0
    rating = round(min(5.0, base + recency_bonus + award_bonus), 1)

    parts = [f"{screenings} recorded screening(s)"]
    if latest_year:
        parts.append(f"latest in {latest_year}")
    if award_count:
        parts.append(f"{award_count} recorded award(s)")
    return rating, ", ".join(parts) + ".", {
        "screenings": screenings,
        "latest_year": latest_year,
        "award_count": award_count,
    }


def apply_rating_guardrails(
    ratings: dict[str, Any],
    evidence: dict[str, Any],
    candidate: dict[str, Any],
    relationship: tuple[float, str, dict[str, Any]],
) -> tuple[dict[str, float], dict[str, str], dict[str, Any]]:
    """Clamp LLM judgements to constraints established by source confidence."""

    guarded = {dimension: _rating(ratings.get(dimension)) for dimension in LLM_DIMENSIONS}
    grounded = {key: str(value) for key, value in evidence.items() if value is not None}
    adjustments: list[str] = []

    for dimension in LLM_DIMENSIONS:
        if not grounded.get(dimension, "").strip():
            guarded[dimension] = 0.0
            grounded[dimension] = "No grounded evidence was supplied; the rating was set to 0/5."
            adjustments.append(f"{dimension} reset because evidence was missing")

    confidence = (candidate.get("identity_confidence") or "low").lower()
    if confidence == "low" and guarded["lineup_similarity"] > 2.0:
        guarded["lineup_similarity"] = 2.0
        grounded["lineup_similarity"] = (
            "Capped at 2/5 because the festival identity is low-confidence and no verified "
            "selection history supports a stronger claim."
        )
        adjustments.append("lineup_similarity capped for low-confidence identity")

    tier = (candidate.get("tier") or "C").upper()
    cap = TIER_STRATEGIC_CAP.get(tier, 3.0)
    if guarded["strategic_value"] > cap:
        guarded["strategic_value"] = cap
        grounded["strategic_value"] = (
            f"Capped at {cap}/5 by the deterministic strategic ceiling for tier {tier}."
        )
        adjustments.append(f"strategic_value capped for tier {tier}")

    relation_rating, relation_evidence, relation_facts = relationship
    guarded["company_relationship"] = relation_rating
    grounded["company_relationship"] = relation_evidence
    return guarded, grounded, {
        "adjustments": adjustments,
        "company_relationship_facts": relation_facts,
    }


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
    if deadline_status == "upcoming":
        days_until_open = (candidate.get("deadline") or {}).get("days_until_open")
        if days_until_open is None or days_until_open > 42:
            return "prioritize_next"
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
