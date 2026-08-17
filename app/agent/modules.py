"""The individual agent modules. Names here must match the architecture diagram."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any

from app import config
from app.agent import prompts, scoring
from app.llm import LLMClient
from app.stores import corpus, pinecone_store, supabase_store


class Trace:
    """Ordered record of every module invocation, exposed as `steps` in the API.

    Thread-safe because MatchScorer and RiskChecker run concurrently.
    """

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def add(self, module: str, prompt: Any, response: Any) -> None:
        with self._lock:
            self.steps.append({"module": module, "prompt": prompt, "response": response})


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _truncate(text: Any, limit: int) -> Any:
    if not isinstance(text, str) or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------- Planner
def planner(llm: LLMClient, trace: Trace, user_prompt: str) -> dict[str, Any]:
    system = prompts.PLANNER.format(catalog=", ".join(prompts.TASK_CATALOG))
    user = json.dumps({"user_request": user_prompt, "today": today()}, ensure_ascii=False)
    plan = llm.complete_json(system, user, max_tokens=2500)

    tasks = [t for t in plan.get("tasks", []) if t.get("module") in prompts.TASK_CATALOG]
    ordered = [
        t
        for module in prompts.TASK_CATALOG
        for t in tasks
        if t.get("module") == module
    ]
    seen: set[str] = set()
    plan["tasks"] = [
        t for t in ordered if not (t["module"] in seen or seen.add(t["module"]))
    ] or [{"module": m, "goal": "default plan"} for m in prompts.TASK_CATALOG]

    trace.add("Planner", {"system": system, "user": user}, plan)
    return plan


# ---------------------------------------------------------------- FilmAnalyzer
def film_analyzer(llm: LLMClient, trace: Trace, user_prompt: str) -> dict[str, Any]:
    user = json.dumps({"film_description": user_prompt}, ensure_ascii=False)
    profile = llm.complete_json(prompts.FILM_ANALYZER, user, max_tokens=3000)
    trace.add("FilmAnalyzer", {"system": prompts.FILM_ANALYZER, "user": user}, profile)
    return profile


# --------------------------------------------------------------- FestivalSearch
def festival_search(trace: Trace, profile: dict[str, Any]) -> list[dict[str, Any]]:
    query_text = profile.get("search_query") or profile.get("logline") or ""
    if not query_text:
        query_text = " ".join(profile.get("themes", []) or [])

    pool = config.CANDIDATE_POOL_SIZE
    matches, backend = pinecone_store.search(query_text, top_k=pool * 2)

    film_format = (profile.get("format") or "").strip()
    facts, facts_source = supabase_store.get_festivals([fid for fid, _ in matches])
    retrieval_scores = dict(matches)

    eligible_first: list[dict[str, Any]] = []
    others: list[dict[str, Any]] = []
    for festival in facts:
        record = dict(festival)
        record["retrieval_score"] = retrieval_scores.get(record["id"], 0.0)
        accepts = record.get("accepts") or []
        (eligible_first if not film_format or film_format in accepts else others).append(record)

    ranked = eligible_first + others
    reserved = min(4, max(0, pool // 4))
    candidates = ranked[: pool - reserved]
    chosen = {c["id"] for c in candidates}
    # Keep a few slots for the highest-tier venues a distributor must always weigh,
    # so a single dominant keyword cannot crowd out the prestige options.
    top_tier = [
        c for c in ranked
        if c["id"] not in chosen and (c.get("tier") or "").upper() in {"A", "B+"}
    ][:reserved]
    candidates = candidates + top_tier
    if len(candidates) < pool:
        filler = [c for c in ranked if c["id"] not in {x["id"] for x in candidates}]
        candidates += filler[: pool - len(candidates)]

    trace.add(
        "FestivalSearch",
        {
            "query": _truncate(query_text, 600),
            "top_k": pool * 2,
            "format_filter": film_format or None,
            "vector_backend": backend,
            "facts_source": facts_source,
            "reserved_top_tier_slots": reserved,
        },
        {
            "returned": len(candidates),
            "festivals": [
                {
                    "id": c["id"],
                    "name": c.get("name"),
                    "tier": c.get("tier"),
                    "country": c.get("country"),
                    "retrieval_score": c["retrieval_score"],
                }
                for c in candidates
            ],
        },
    )
    return candidates


# ---------------------------------------------------------------- CompanyMemory
def company_memory(trace: Trace, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    festival_ids = [c["id"] for c in candidates]
    memory, source = supabase_store.get_company_memory(festival_ids)
    trace.add(
        "CompanyMemory",
        {"company_id": config.COMPANY_ID, "festival_ids": festival_ids, "source": source},
        {
            "company": memory.get("company", {}).get("name"),
            "matched_history_rows": len(memory.get("history", [])),
            "history": memory.get("history", []),
        },
    )
    return memory


# ------------------------------------------------------------------ MatchScorer
def match_scorer(
    llm: LLMClient,
    trace: Trace,
    profile: dict[str, Any],
    candidates: list[dict[str, Any]],
    memory: dict[str, Any],
    revision_instructions: str | None = None,
) -> dict[str, dict[str, Any]]:
    history_by_festival: dict[str, list[dict[str, Any]]] = {}
    for row in memory.get("history", []):
        history_by_festival.setdefault(row.get("festival_id"), []).append(
            {
                "screenings": row.get("screenings"),
                "years": row.get("years"),
                "films": (row.get("films") or [])[:6],
                "awards": [
                    f"{award.get('award')} ({award.get('film')}, {award.get('year')})"
                    for award in (row.get("awards") or [])[:3]
                ],
                "result": row.get("result"),
            }
        )

    payload = {
        "today": today(),
        "tier_meaning": {
            "A": "top-tier launch platform",
            "B+": "strong international festival",
            "B": "solid international slot",
            "C": "niche or regional slot",
        },
        "film": {
            "title": profile.get("title"),
            "logline": _truncate(profile.get("logline"), 400),
            "format": profile.get("format"),
            "genres": profile.get("genres"),
            "themes": profile.get("themes"),
            "country": profile.get("country"),
            "language": profile.get("language"),
            "director_profile": _truncate(profile.get("director_profile"), 200),
            "premiere_status": profile.get("premiere_status"),
            "festival_angles": profile.get("festival_angles"),
        },
        "company": {
            "name": memory.get("company", {}).get("name"),
            "profile": _truncate(memory.get("company", {}).get("profile"), 300),
        },
        "candidates": [
            {
                **corpus.compact_for_prompt(c),
                "focus": _truncate(c.get("focus"), 240),
                "notes": _truncate(c.get("notes"), 160),
                "company_history": history_by_festival.get(c["id"], []),
            }
            for c in candidates
        ],
    }
    if revision_instructions:
        payload["revision_instructions"] = revision_instructions

    user = json.dumps(payload, ensure_ascii=False)
    result = llm.complete_json(prompts.MATCH_SCORER, user, max_tokens=9000)
    trace.add("MatchScorer", {"system": prompts.MATCH_SCORER, "user": user}, result)

    return {row["id"]: row for row in result.get("scores", []) if row.get("id")}


# ------------------------------------------------------------------ RiskChecker
def risk_checker(
    llm: LLMClient,
    trace: Trace,
    profile: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    payload = {
        "today": today(),
        "film": {
            "format": profile.get("format"),
            "premiere_status": profile.get("premiere_status"),
            "country": profile.get("country"),
        },
        "candidates": [
            {
                "id": c["id"],
                "name": c.get("name"),
                "month": c.get("month"),
                "deadline_month": c.get("typical_deadline_month"),
                "last_recorded_deadline": c.get("final_deadline"),
                "submission_open": c.get("submission_open"),
                "premiere_requirement": c.get("premiere_requirement"),
                "premiere_requirement_raw": c.get("premiere_requirement_raw"),
                "premiere_territory": c.get("premiere_territory"),
                "accepts": c.get("accepts"),
                "notes": _truncate(c.get("notes"), 160),
            }
            for c in candidates
        ],
    }
    user = json.dumps(payload, ensure_ascii=False)
    result = llm.complete_json(prompts.RISK_CHECKER, user, max_tokens=5000)
    trace.add("RiskChecker", {"system": prompts.RISK_CHECKER, "user": user}, result)

    return {row["id"]: row for row in result.get("risks", []) if row.get("id")}


# ------------------------------------------------- deterministic score assembly
def assemble(
    candidates: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
    risks: dict[str, dict[str, Any]],
    trace: Trace,
) -> list[dict[str, Any]]:
    """Apply the weighted formula in code — the LLM never invents the number."""

    now = datetime.now(timezone.utc).date()
    assembled: list[dict[str, Any]] = []

    for candidate in candidates:
        scored = scores.get(candidate["id"], {})
        risk = risks.get(candidate["id"], {})
        ratings = dict(scored.get("ratings", {}) or {})
        evidence = dict(scored.get("evidence", {}) or {})

        urgency, urgency_reason = scoring.deadline_urgency(
            candidate.get("typical_deadline_month"), now
        )
        ratings["deadline_urgency"] = urgency
        evidence["deadline_urgency"] = urgency_reason

        computed = scoring.compute_score(ratings, risk.get("premiere_risk"))

        record = {
            "id": candidate["id"],
            "name": candidate.get("name"),
            "country": candidate.get("country"),
            "tier": candidate.get("tier"),
            "month": candidate.get("month"),
            "deadline_month": candidate.get("typical_deadline_month"),
            "premiere_requirement": candidate.get("premiere_requirement"),
            "website": candidate.get("website"),
            "retrieval_score": candidate.get("retrieval_score", 0.0),
            "ratings": ratings,
            "evidence": evidence,
            "headline": scored.get("headline"),
            "premiere_risk": risk.get("premiere_risk", "none"),
            "premiere_opportunity": bool(risk.get("premiere_opportunity")),
            "deadline_status": risk.get("deadline_status", "open"),
            "eligible": risk.get("eligible", True),
            "risk_note": risk.get("risk_note"),
            **computed,
        }
        record["bucket"] = scoring.assign_bucket(record)
        assembled.append(record)

    ranked = scoring.rank(assembled)
    trace.add(
        "MatchScorer",
        {
            "operation": "deterministic_weighted_score",
            "weights": scoring.WEIGHTS,
            "premiere_penalty_table": scoring.PREMIERE_PENALTY,
            "deadline_urgency": f"computed in code against {now.isoformat()}",
        },
        {
            "scored": len(ranked),
            "ranking": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "score": r["score"],
                    "base_score": r["base_score"],
                    "premiere_penalty": r["premiere_penalty"],
                    "premiere_opportunity": r["premiere_opportunity"],
                    "bucket": r["bucket"],
                }
                for r in ranked
            ],
        },
    )
    return ranked


# --------------------------------------------------------------- RoadmapBuilder
def roadmap_builder(
    llm: LLMClient,
    trace: Trace,
    profile: dict[str, Any],
    ranked: list[dict[str, Any]],
    memory: dict[str, Any],
    revision_instructions: str | None = None,
) -> dict[str, Any]:
    payload = {
        "today": today(),
        "film": {
            "title": profile.get("title"),
            "logline": _truncate(profile.get("logline"), 300),
            "premiere_status": profile.get("premiere_status"),
            "missing_info": profile.get("missing_info"),
        },
        "company": memory.get("company", {}).get("name"),
        "festivals": [
            {
                "id": r["id"],
                "name": r["name"],
                "score": r["score"],
                "bucket": r["bucket"],
                "deadline_month": r["deadline_month"],
                "premiere_requirement": r["premiere_requirement"],
                "premiere_risk": r["premiere_risk"],
                "premiere_opportunity": r["premiere_opportunity"],
                "deadline_status": r["deadline_status"],
                "risk_note": _truncate(r.get("risk_note"), 160),
                "evidence": r.get("evidence"),
            }
            for r in ranked
        ],
    }
    if revision_instructions:
        payload["revision_instructions"] = revision_instructions

    user = json.dumps(payload, ensure_ascii=False)
    roadmap = llm.complete_json(prompts.ROADMAP_BUILDER, user, max_tokens=7000)
    trace.add("RoadmapBuilder", {"system": prompts.ROADMAP_BUILDER, "user": user}, roadmap)
    return roadmap


# ------------------------------------------------------------------- Replanner
def replanner(
    llm: LLMClient,
    trace: Trace,
    objective: str,
    ranked: list[dict[str, Any]],
    roadmap: dict[str, Any],
) -> dict[str, Any]:
    buckets: dict[str, int] = {}
    for record in ranked:
        buckets[record["bucket"]] = buckets.get(record["bucket"], 0) + 1

    payload = {
        "objective": objective,
        "bucket_counts": buckets,
        "top_scores": [{"name": r["name"], "score": r["score"]} for r in ranked[:8]],
        "roadmap_headline": roadmap.get("headline"),
        "strategy_summary": _truncate(roadmap.get("strategy_summary"), 500),
        "open_questions": roadmap.get("open_questions"),
    }
    user = json.dumps(payload, ensure_ascii=False)
    decision = llm.complete_json(prompts.REPLANNER, user, max_tokens=2000)
    trace.add("Replanner", {"system": prompts.REPLANNER, "user": user}, decision)
    return decision
