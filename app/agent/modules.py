"""The individual agent modules. Names here must match the architecture diagram."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from typing import Any

from app import config
from app.agent import domain, prompts, scoring
from app.llm import LLMClient
from app.stores import corpus, pinecone_store, supabase_store


class Trace:
    """Ordered record of every module invocation, exposed as `steps` in the API."""

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


def _is_premiere_state_missing(value: Any) -> bool:
    text = re.sub(r"[^a-z0-9]+", " ", str(value).casefold()).strip()
    return bool(
        re.search(
            r"\b(?:premiere(?:\s+screening)?|screening(?:\s+premiere)?)\s+status\b"
            r"|\b(?:premiere|screening)\s+history\b"
            r"|\bprior\s+public\s+screenings?\b"
            r"|\b(?:whether|if|where|when)\b.{0,50}\b(?:screened|premiered)\b"
            r"|\b(?:world|international|territorial|remaining)\s+premiere\s+"
            r"(?:availability|rights|status)\b",
            text,
        )
    )


# --------------------------------------------------------------------- Planner
def planner(trace: Trace, user_prompt: str) -> dict[str, Any]:
    """Build the complete domain workflow without spending an LLM call.

    Festival strategy always needs the same evidence chain.  A generative plan
    added cost and could omit required modules, so the planner now makes the
    invariant workflow explicit and auditable.
    """

    goals = {
        "FilmAnalyzer": "Extract only festival-relevant film facts and unknowns.",
        "CompanyMemory": "Load prior relationships before candidate generation.",
        "FestivalSearch": "Build a hybrid semantic, relationship-aware candidate pool.",
        "RiskChecker": "Compute deadline, format and premiere constraints in code.",
        "MatchScorer": "Rate creative fit and combine it with deterministic evidence.",
        "RoadmapBuilder": "Select grounded evidence and unresolved facts for the deterministic roadmap.",
    }
    plan = {
        "objective": _truncate(user_prompt.strip(), 500),
        "tasks": [{"module": module, "goal": goals[module]} for module in prompts.TASK_CATALOG],
        "assumptions": [
            "Projected dates are planning estimates until checked against official festival rules.",
            "Ambiguous premiere shorthand is treated conservatively and surfaced as uncertainty.",
        ],
        "planning_policy": "complete evidence chain; no optional module may be omitted",
    }
    trace.add(
        "Planner",
        {"user_request": _truncate(user_prompt, 1000), "policy": "deterministic_domain_plan"},
        plan,
    )
    return plan


# ---------------------------------------------------------------- FilmAnalyzer
def film_analyzer(llm: LLMClient, trace: Trace, user_prompt: str) -> dict[str, Any]:
    user = json.dumps({"film_description": user_prompt}, ensure_ascii=False)
    profile = llm.complete_json(
        prompts.FILM_ANALYZER, user, max_tokens=2500, module="FilmAnalyzer"
    )
    valid_formats = {
        "feature_fiction", "feature_doc", "short_fiction", "short_doc", "animation", "experimental",
    }
    valid_premiere_statuses = {
        "world_premiere_available", "international_premiere_available", "already_premiered", "unknown",
    }
    adjustments = []
    contradictions = []
    input_evidence = domain.analyse_critical_input(user_prompt)
    missing_info = profile.get("missing_info")
    if not isinstance(missing_info, list):
        missing_info = []
        adjustments.append("missing_info normalized to a list")
    for field in ("genres", "themes", "festival_angles", "premiere_history"):
        if not isinstance(profile.get(field), list):
            profile[field] = []
            adjustments.append(f"{field} normalized to a list")
    profile["premiere_history"] = [
        row for row in profile["premiere_history"] if isinstance(row, dict)
    ]
    if profile.get("format") not in valid_formats:
        profile["format"] = None
        if "format" not in missing_info:
            missing_info.append("format")
        adjustments.append("unsupported format replaced with null")
    if profile.get("premiere_status") not in valid_premiere_statuses:
        profile["premiere_status"] = "unknown"
        adjustments.append("unsupported premiere_status replaced with unknown")
    if profile["premiere_history"] and profile.get("premiere_status") == "world_premiere_available":
        profile["premiere_status"] = "already_premiered"
        adjustments.append("premiere history overrides world_premiere_available")
    if profile["premiere_history"] and profile.get("premiere_status") == "international_premiere_available":
        film_country = re.sub(r"[^a-z0-9]+", " ", (profile.get("country") or "").lower()).strip()
        screened_abroad = any(
            re.sub(r"[^a-z0-9]+", " ", (row.get("country") or "").lower()).strip()
            not in {"", film_country}
            for row in profile["premiere_history"]
        )
        if screened_abroad:
            profile["premiere_status"] = "already_premiered"
            adjustments.append("international screening history overrides international_premiere_available")

    def add_missing(value: str) -> None:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

        def category(item: str) -> str:
            if _is_premiere_state_missing(item):
                return "premiere"
            if re.search(r"\bruntime\b", item):
                return "runtime"
            if re.search(r"\bformat\b", item):
                return "format"
            if re.search(r"\b(?:completion|release|picture lock|final cut)\b", item):
                return "completion_release"
            return item

        new_category = category(normalized)
        for index, item in enumerate(missing_info):
            existing = re.sub(r"[^a-z0-9]+", " ", str(item).lower()).strip()
            if category(existing) == new_category:
                if len(value) > len(str(item)):
                    missing_info[index] = value
                return
        missing_info.append(value)

    def clear_premiere_missing() -> None:
        before = len(missing_info)
        missing_info[:] = [
            item
            for item in missing_info
            if not _is_premiere_state_missing(item)
        ]
        if len(missing_info) != before:
            adjustments.append(
                "premiere clarification removed because the request supplied explicit film-history evidence"
            )

    runtime_evidence = input_evidence["runtime"]
    if runtime_evidence["contradictory"]:
        profile["runtime_minutes"] = None
        add_missing(
            "runtime (conflicting values in the request; clarification required)"
        )
        contradictions.append(
            {"field": "runtime_minutes", "values": runtime_evidence["values"]}
        )
        adjustments.append("conflicting runtime values replaced with null")

    format_evidence = input_evidence["format"]
    if format_evidence["contradictory"]:
        profile["format"] = None
        add_missing(
            "format (conflicting explicit labels in the request; clarification required)"
        )
        contradictions.append(
            {"field": "format", "values": format_evidence["explicit_labels"]}
        )
        adjustments.append("conflicting explicit format labels replaced with null")

    completion_evidence = input_evidence["completion_release"]
    if completion_evidence["contradictory"]:
        add_missing(
            "completion/release state (conflicting statements; clarification required)"
        )
        contradictions.append(
            {
                "field": "completion_release_state",
                "states": {
                    key: completion_evidence[key]
                    for key in ("incomplete", "complete", "unreleased", "released")
                },
            }
        )
        adjustments.append("conflicting completion/release statements surfaced")

    premiere_evidence = input_evidence["premiere"]
    brief_mentions_premiere_facts = bool(
        re.search(
            r"\b(?:premiere(?:d|s)?|screen(?:ed|ing|ings)?|"
            r"show(?:n|ing|ings)?|exhibit(?:ed|ion|ions)?|"
            r"release(?:d)?|online|stream(?:ed|ing)?|youtube|vimeo|ticketed)\b",
            user_prompt,
            flags=re.I,
        )
    )
    analyzer_marks_premiere_state_missing = any(
        _is_premiere_state_missing(item) for item in missing_info
    )
    analyzer_premiere_facts_are_grounded = (
        brief_mentions_premiere_facts
        and (
            bool(profile["premiere_history"])
            or (
                profile.get("premiere_status") != "unknown"
                and not analyzer_marks_premiere_state_missing
            )
        )
    )
    film_country_text = str(profile.get("country") or "").strip()
    if (
        premiere_evidence["positive_screening"]
        and not premiere_evidence["home_country_screening"]
        and film_country_text
        and re.search(
            rf"\b(?:public(?:ly)?\s+)?screen(?:ed|ing)\b.{{0,50}}\bin\s+{re.escape(film_country_text)}\b"
            rf"|\bin\s+{re.escape(film_country_text)}\b.{{0,50}}\bpublic\s+screen(?:ed|ing)\b",
            user_prompt,
            flags=re.I,
        )
    ):
        premiere_evidence["home_country_screening"] = True
    if premiere_evidence["contradictory"]:
        profile["premiere_status"] = "unknown"
        add_missing(
            "premiere/screening status (conflicting statements; clarification required)"
        )
        contradictions.append(
            {
                "field": "premiere_status",
                "states": ["explicitly_unscreened", "screening_or_premiere_reported"],
            }
        )
        adjustments.append(
            "conflicting premiere/screening statements forced premiere_status to unknown"
        )
    elif (
        premiere_evidence["explicitly_unknown"]
        or (
            not premiere_evidence["evidence_present"]
            and not analyzer_premiere_facts_are_grounded
        )
    ):
        if profile.get("premiere_status") != "unknown":
            adjustments.append(
                "premiere_status forced to unknown because the request supplied no positive premiere evidence"
            )
        if profile["premiere_history"]:
            profile["premiere_history"] = []
            adjustments.append(
                "unsupported premiere_history removed because the request supplied no screening evidence"
            )
        profile["premiere_status"] = "unknown"
        add_missing(
            "premiere status (high-impact: confirm whether and where the film has screened publicly)"
        )
    elif not premiere_evidence["evidence_present"]:
        adjustments.append(
            "grounded FilmAnalyzer premiere state preserved when the conservative phrase parser had no exact match"
        )
    elif premiere_evidence["international_premiere_available"]:
        if profile.get("premiere_status") != "international_premiere_available":
            profile["premiere_status"] = "international_premiere_available"
            adjustments.append(
                "explicit international-premiere evidence established international_premiere_available"
            )
        clear_premiere_missing()
    elif premiere_evidence["positive_screening"]:
        derived_status = (
            "international_premiere_available"
            if premiere_evidence["home_country_screening"]
            and premiere_evidence["single_public_screening"]
            else "already_premiered"
        )
        if profile.get("premiere_status") != derived_status:
            profile["premiere_status"] = derived_status
            adjustments.append(
                f"explicit screening/premiere evidence forced premiere_status to {derived_status}"
            )
        history_row = {
            "festival": (
                "Public online availability"
                if premiere_evidence["public_online_availability"]
                else "User-reported public screening"
            ),
            "country": (
                profile.get("country")
                if premiere_evidence["home_country_screening"]
                else None
            ),
            "date": premiere_evidence["screening_date"],
            "access": "public",
            "event_kind": (
                "online_availability"
                if premiere_evidence["public_online_availability"]
                else "screening"
            ),
            "source": "explicit_user_brief",
            "home_country": premiere_evidence["home_country_screening"],
        }
        if (
            premiere_evidence["home_country_screening"]
            and premiere_evidence["single_public_screening"]
        ):
            existing = profile["premiere_history"][0] if profile["premiere_history"] else {}
            profile["premiere_history"] = [
                {
                    **existing,
                    **history_row,
                    "date": premiere_evidence["screening_date"] or existing.get("date"),
                }
            ]
            adjustments.append(
                "explicit single home-country screening normalized in premiere_history"
            )
        elif premiere_evidence["public_online_availability"]:
            if not any(
                str(row.get("event_kind") or "").casefold()
                == "online_availability"
                for row in profile["premiere_history"]
            ):
                profile["premiere_history"].append(history_row)
                adjustments.append(
                    "explicit public online availability added to premiere_history"
                )
        elif not profile["premiere_history"]:
            profile["premiere_history"] = [history_row]
            adjustments.append(
                "explicit public exhibition evidence added to premiere_history"
            )
        if (
            premiere_evidence["public_online_availability"]
            or premiere_evidence["home_country_screening"]
        ):
            clear_premiere_missing()
    elif premiere_evidence["explicitly_unscreened"]:
        if profile.get("premiere_status") != "world_premiere_available":
            profile["premiere_status"] = "world_premiere_available"
            adjustments.append(
                "explicit no-screening evidence established world_premiere_available"
            )
        if profile["premiere_history"]:
            profile["premiere_history"] = []
            adjustments.append(
                "premiere_history removed because the request explicitly says the film is unscreened"
            )
        clear_premiere_missing()

    profile["_film_history_evidence"] = {
        "explicit_no_public_screenings": premiere_evidence["explicitly_unscreened"],
        "single_home_country_screening": bool(
            premiere_evidence["positive_screening"]
            and premiere_evidence["home_country_screening"]
            and premiere_evidence["single_public_screening"]
        ),
        "public_online_availability": premiere_evidence["public_online_availability"],
    }

    profile["_audience_evidence"] = input_evidence["youth_audience"]
    profile["_semantic_evidence"] = input_evidence["semantic_attributes"]
    if (
        not input_evidence["youth_audience"]["established"]
        and re.search(
            r"\b(?:children|kids|teens?|teenagers|youth|young audiences?|family audiences?)\b",
            str(profile.get("target_audience") or ""),
            flags=re.I,
        )
    ):
        profile["target_audience"] = "Not established from the supplied information."
        adjustments.append(
            "unsupported youth target-audience inference removed; protagonist age and family theme are insufficient"
        )
    unsupported_authorship = {
        "women_authorship": re.compile(
            r"\b(?:women|woman|female)[- ]?(?:directed|authored|led|filmmakers?|directors?|authorship)\b",
            re.I,
        ),
        "indigenous_authorship": re.compile(
            r"\bindigenous[- ]?(?:directed|authored|led|filmmakers?|directors?|authorship)\b",
            re.I,
        ),
    }
    for attribute, pattern in unsupported_authorship.items():
        if input_evidence["semantic_attributes"][attribute]["established"]:
            continue
        for field in ("themes", "festival_angles"):
            kept = [item for item in profile[field] if not pattern.search(str(item))]
            if len(kept) != len(profile[field]):
                profile[field] = kept
                adjustments.append(
                    f"unsupported {attribute} inference removed from {field}"
                )
        if pattern.search(str(profile.get("director_profile") or "")):
            profile["director_profile"] = "Not established from the supplied information."
            adjustments.append(
                f"unsupported {attribute} inference removed from director_profile"
            )
    profile["missing_info"] = missing_info
    profile["_validation"] = {"valid": not adjustments, "adjustments": adjustments}
    if contradictions:
        profile["_validation"]["contradictions"] = contradictions
    trace.add(
        "FilmAnalyzer",
        {
            "operation": "deterministic_schema_and_premiere_history_validation",
            "allowed_formats": sorted(valid_formats),
            "allowed_premiere_statuses": sorted(valid_premiere_statuses),
        },
        profile["_validation"],
    )
    return profile


# --------------------------------------------------------------- FestivalSearch
def _entity_key(festival: dict[str, Any]) -> str:
    website = re.sub(r"^https?://(www\.)?|/+$", "", festival.get("website") or "", flags=re.I)
    if website:
        return f"website:{website.lower()}"
    name = re.sub(r"[^a-z0-9]+", "", (festival.get("name") or "").lower())
    return f"name:{name}"


def _history_by_festival(memory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in memory.get("history", []):
        festival_id = row.get("festival_id")
        if festival_id:
            grouped.setdefault(festival_id, []).append(row)
    return grouped


def festival_search(
    trace: Trace,
    profile: dict[str, Any],
    memory: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build a semantic pool, then reserve relevant relationship and prestige slots."""

    query_text = profile.get("search_query") or profile.get("logline") or ""
    if not query_text:
        query_text = " ".join(profile.get("themes", []) or [])

    pool = config.CANDIDATE_POOL_SIZE
    matches, backend, fallback_reason = pinecone_store.search(
        query_text, top_k=pool * 3, trace_callback=trace.add
    )
    lexical_matches = corpus.lexical_search(query_text, top_k=len(corpus.load_festivals()))
    semantic_scores = dict(matches) if backend.startswith("pinecone:") else {}
    lexical_scores = dict(lexical_matches)
    history_by_festival = _history_by_festival(memory)

    # A prior relationship is useful only when the festival is also relevant to
    # this film. This prevents company memory from becoming a popularity prior.
    relationship_ids = [
        festival_id
        for festival_id in history_by_festival
        if lexical_scores.get(festival_id, 0.0) >= 0.08 or festival_id in semantic_scores
    ]
    ordered_ids = [festival_id for festival_id, _ in matches]
    ordered_ids.extend(
        sorted(
            (festival_id for festival_id in relationship_ids if festival_id not in semantic_scores),
            key=lambda festival_id: lexical_scores.get(festival_id, 0.0),
            reverse=True,
        )
    )

    film_format = (profile.get("format") or "").strip()
    facts, facts_source = supabase_store.get_festivals(ordered_ids)

    ranked: list[dict[str, Any]] = []
    for festival in facts:
        record = domain.normalise_festival_facts(festival)
        festival_id = record["id"]
        sources = []
        if festival_id in semantic_scores:
            sources.append("semantic")
        if lexical_scores.get(festival_id, 0.0) > 0:
            sources.append("lexical")
        if festival_id in history_by_festival:
            sources.append("company_memory")
        semantic = semantic_scores.get(festival_id, 0.0)
        lexical = lexical_scores.get(festival_id, 0.0)
        record["semantic_score"] = round(semantic, 4)
        record["lexical_score"] = round(lexical, 4)
        record["retrieval_score"] = round(max(semantic, lexical * 0.85), 4)
        record["retrieval_sources"] = sources
        record["retrieval_backend"] = backend
        record["retrieval_fallback_reason"] = fallback_reason
        record["facts_source"] = facts_source
        relationship = scoring.company_relationship_rating(
            history_by_festival.get(festival_id, []), datetime.now(timezone.utc).year
        )
        record["relationship_strength"] = relationship[0]
        accepts = record.get("accepts") or []
        record["format_eligible"] = not film_format or not accepts or film_format in accepts
        ranked.append(record)

    ranked.sort(
        key=lambda c: (
            c["format_eligible"], c["retrieval_score"], c["relationship_strength"]
        ),
        reverse=True,
    )

    relationship_reserve = min(2, max(1, pool // 6))
    prestige_reserve = min(3, max(1, pool // 4))
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    selected_entities: set[str] = set()

    def add(records: list[dict[str, Any]], limit: int) -> None:
        if limit <= 0 or len(selected) >= pool:
            return
        added = 0
        for record in records:
            entity = _entity_key(record)
            if record["id"] in selected_ids or entity in selected_entities:
                continue
            selected.append(record)
            selected_ids.add(record["id"])
            selected_entities.add(entity)
            added += 1
            if added >= limit or len(selected) >= pool:
                return

    relationship_ranked = sorted(
        (
            c for c in ranked
            if c["format_eligible"]
            and "company_memory" in c["retrieval_sources"]
            and c["retrieval_score"] > 0
        ),
        key=lambda c: (c["relationship_strength"], c["retrieval_score"]),
        reverse=True,
    )
    prestige_ranked = [
        c for c in ranked
        if c["format_eligible"] and (c.get("tier") or "").upper() in {"A", "B+"}
    ]
    add(relationship_ranked, relationship_reserve)
    add(prestige_ranked, prestige_reserve)
    add(ranked, pool - len(selected))
    candidates = sorted(
        selected,
        key=lambda c: (c["format_eligible"], c["retrieval_score"], c["relationship_strength"]),
        reverse=True,
    )

    trace.add(
        "FestivalSearch",
        {
            "query": _truncate(query_text, 600),
            "top_k": pool * 3,
            "format_filter": film_format or None,
            "vector_backend": backend,
            "fallback_reason": fallback_reason,
            "facts_source": facts_source,
            "selection_policy": "semantic + lexical relevance with relationship and prestige reserves",
            "relationship_reserve": relationship_reserve,
            "prestige_reserve": prestige_reserve,
            "entity_deduplication": "website_then_normalized_name",
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
                    "retrieval_sources": c["retrieval_sources"],
                    "retrieval_backend": c["retrieval_backend"],
                    "facts_source": c["facts_source"],
                    "relationship_strength": c["relationship_strength"],
                    "format_eligible": c["format_eligible"],
                    "data_quality_adjustments": c.get("data_quality_adjustments", []),
                }
                for c in candidates
            ],
        },
    )
    return candidates


# ---------------------------------------------------------------- CompanyMemory
def company_memory(trace: Trace) -> dict[str, Any]:
    memory, source = supabase_store.get_company_memory()
    history_by_festival = _history_by_festival(memory)
    summaries = []
    current_year = datetime.now(timezone.utc).year
    for festival_id, rows in history_by_festival.items():
        rating, evidence, facts = scoring.company_relationship_rating(rows, current_year)
        summaries.append({"festival_id": festival_id, "rating": rating, "evidence": evidence, **facts})
    summaries.sort(key=lambda row: row["rating"], reverse=True)
    trace.add(
        "CompanyMemory",
        {"company_id": config.COMPANY_ID, "scope": "full_history", "source": source},
        {
            "company": memory.get("company", {}).get("name"),
            "history_rows": len(memory.get("history", [])),
            "festival_relationships": len(history_by_festival),
            "strongest_relationships": summaries[:8],
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
) -> dict[str, dict[str, Any]]:
    history_by_festival = _history_by_festival(memory)
    current_year = datetime.now(timezone.utc).year
    relationship_summaries = {}
    for candidate in candidates:
        rating, evidence, _ = scoring.company_relationship_rating(
            history_by_festival.get(candidate["id"], []), current_year
        )
        relationship_summaries[candidate["id"]] = {"rating": rating, "evidence": evidence}

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
                "company_relationship": relationship_summaries[c["id"]],
            }
            for c in candidates
        ],
    }

    known_ids = {candidate["id"] for candidate in candidates}
    for repair_round in range(2):
        user = json.dumps(payload, ensure_ascii=False)
        result = llm.complete_json(
            prompts.MATCH_SCORER, user, max_tokens=6000, module="MatchScorer"
        )
        rows = result.get("scores") if isinstance(result.get("scores"), list) else []
        sanitized: dict[str, dict[str, Any]] = {}
        unknown_ids = []
        duplicate_ids = []
        invalid_rows = 0
        invalid_score_rows: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                invalid_rows += 1
                continue
            festival_id = row["id"]
            if festival_id not in known_ids:
                unknown_ids.append(festival_id)
                continue
            if festival_id in sanitized:
                duplicate_ids.append(festival_id)
                continue
            ratings = row.get("ratings")
            evidence = row.get("evidence")
            defects = []
            if not isinstance(ratings, dict) or not isinstance(evidence, dict):
                defects.append("ratings_and_evidence_must_be_objects")
            else:
                for dimension in scoring.LLM_DIMENSIONS:
                    try:
                        float(ratings.get(dimension))
                    except (TypeError, ValueError):
                        defects.append(f"{dimension}:non_numeric_rating")
                    if not str(evidence.get(dimension) or "").strip():
                        defects.append(f"{dimension}:missing_evidence")
            if defects:
                invalid_score_rows.append({"id": festival_id, "defects": defects})
                continue
            sanitized[festival_id] = row
        validation = {
            "missing_ids": sorted(known_ids - set(sanitized)),
            "unknown_ids": unknown_ids,
            "duplicate_ids": duplicate_ids,
            "invalid_rows": invalid_rows,
            "invalid_score_rows": invalid_score_rows,
        }
        if not any(validation.values()):
            sanitized["_validation"] = validation
            return sanitized
        trace.add(
            "MatchScorer",
            {
                "operation": "deterministic_llm_output_validation",
                "repair_round": repair_round,
            },
            validation,
        )
        if repair_round == 0:
            payload["repair_instructions"] = (
                "Return one valid score row for every supplied festival id and no other ids. "
                f"Correct these defects: {json.dumps(validation, ensure_ascii=False)}"
            )
    raise ValueError("MatchScorer returned invalid structured output after one targeted repair.")


# ------------------------------------------------------------------ RiskChecker
def risk_checker(
    trace: Trace,
    profile: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    current_date = datetime.now(timezone.utc).date()
    assessments = {
        candidate["id"]: domain.assess_candidate(profile, candidate, current_date)
        for candidate in candidates
    }
    trace.add(
        "RiskChecker",
        {
            "operation": "deterministic_domain_validation",
            "date": current_date.isoformat(),
            "rules": ["exact_deadline_precedence", "explicit_projection", "format_eligibility", "premiere_scope"],
        },
        {
            "assessed": len(assessments),
            "closed": sum(a["deadline_status"] == "closed" for a in assessments.values()),
            "ineligible": sum(not a["eligible"] for a in assessments.values()),
            "uncertain": sum(bool(a["uncertainties"]) for a in assessments.values()),
            "risks": list(assessments.values()),
        },
    )
    return assessments


# ------------------------------------------------- deterministic score assembly
def assemble(
    candidates: list[dict[str, Any]],
    scores: dict[str, dict[str, Any]],
    risks: dict[str, dict[str, Any]],
    profile: dict[str, Any],
    memory: dict[str, Any],
    trace: Trace,
) -> list[dict[str, Any]]:
    """Apply the weighted formula in code — the LLM never invents the number."""

    now = datetime.now(timezone.utc).date()
    history_by_festival = _history_by_festival(memory)
    llm_validation = scores.get("_validation", {})
    assembled: list[dict[str, Any]] = []

    for candidate in candidates:
        scored = scores.get(candidate["id"], {})
        risk = risks.get(candidate["id"], {})
        relationship = scoring.company_relationship_rating(
            history_by_festival.get(candidate["id"], []), now.year
        )
        ratings, evidence, guardrail_meta = scoring.apply_rating_guardrails(
            dict(scored.get("ratings", {}) or {}),
            dict(scored.get("evidence", {}) or {}),
            candidate,
            relationship,
            profile,
        )
        deadline = risk.get("deadline", {}) or {}
        ratings["deadline_urgency"] = deadline.get("urgency", 2.0)
        evidence["deadline_urgency"] = deadline.get(
            "reason", "Deadline evidence is unavailable."
        )

        computed = scoring.compute_score(ratings, risk.get("premiere_risk"))

        record = {
            "id": candidate["id"],
            "name": candidate.get("name"),
            "country": candidate.get("country"),
            "tier": candidate.get("tier"),
            "city": candidate.get("city"),
            "region": candidate.get("region"),
            "month": candidate.get("month"),
            "deadline_month": candidate.get("typical_deadline_month"),
            "deadline": deadline,
            "premiere_requirement": candidate.get("premiere_requirement"),
            "premiere_requirement_raw": candidate.get("premiere_requirement_raw"),
            "premiere_constraint": risk.get("premiere_constraint", {}),
            "runtime_constraint": risk.get("runtime_constraint"),
            "runtime_warning": risk.get("runtime_warning"),
            "eligibility_issue": risk.get("eligibility_issue"),
            "website": candidate.get("website"),
            "identity_confidence": candidate.get("identity_confidence", "low"),
            "retrieval_score": candidate.get("retrieval_score", 0.0),
            "retrieval_sources": candidate.get("retrieval_sources", []),
            "retrieval_backend": candidate.get("retrieval_backend"),
            "retrieval_fallback_reason": candidate.get("retrieval_fallback_reason"),
            "facts_source": candidate.get("facts_source"),
            "ratings": ratings,
            "evidence": evidence,
            "headline": scored.get("headline"),
            "premiere_risk": risk.get("premiere_risk", "none"),
            "premiere_opportunity": bool(risk.get("premiere_opportunity")),
            "deadline_status": risk.get("deadline_status", "open"),
            "eligible": risk.get("eligible", True),
            "risk_note": risk.get("risk_note"),
            "uncertainties": risk.get("uncertainties", []),
            "validation": guardrail_meta,
            "provenance": {
                "festival_facts": candidate.get("id"),
                "retrieval": candidate.get("retrieval_sources", []),
                "retrieval_backend": candidate.get("retrieval_backend"),
                "festival_facts_source": candidate.get("facts_source"),
                "creative_fit": "MatchScorer LLM",
                "deadline_and_premiere": "RiskChecker deterministic rules",
                "company_relationship": "CompanyMemory records",
                "data_quality_adjustments": candidate.get("data_quality_adjustments", []),
            },
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
            "deadline_urgency": f"computed from structured dates against {now.isoformat()}",
            "guardrails": [
                "rating_clamp",
                "identity_confidence_cap",
                "tier_strategic_cap",
                "youth_audience_evidence_cap",
            ],
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
            "llm_output_validation": llm_validation,
        },
    )
    return ranked


# --------------------------------------------------------------- RoadmapBuilder
ROADMAP_BUCKETS = ["submit_first", "prioritize_next", "leverage", "hold_avoid"]


def premiere_sequence_role(record: dict[str, Any]) -> dict[str, str]:
    """Make the selected launch target the first public festival screening."""

    post = record.get("post_target_compatibility", {}) or {}
    pre = record.get("pre_target_compatibility", {}) or {}
    post_status = post.get("status")
    pre_status = pre.get("status")
    if "target" in {post_status, pre_status}:
        return {"status": "target", "reason": "This is the intended premiere target."}
    if "not_applicable" in {post_status, pre_status}:
        return {"status": "not_applicable", "reason": "No premiere target is selected."}
    if post_status == "backup_only":
        return {
            "status": "alternative_only",
            "reason": (
                f"{post.get('reason')} Because the selected target is the intended first "
                "public screening, this festival is a mutually exclusive premiere path."
            ),
        }
    if post_status == "verify" or pre_status == "verify_before_target":
        return {
            "status": "verify",
            "reason": "The ordering cannot be established until the official premiere rules are verified.",
        }
    reason = "The selected premiere target is the intended first public festival screening."
    if pre_status == "must_follow_target" and pre.get("reason"):
        reason = f"{reason} {pre['reason']}"
    return {"status": "must_follow_target", "reason": reason}


def apply_premiere_strategy(
    profile: dict[str, Any], ranked: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Choose one coherent premiere target and annotate every downstream option."""

    premiere_status = profile.get("premiere_status")
    if premiere_status not in {"world_premiere_available", "international_premiere_available"}:
        target = None
    else:
        scope_priority = {"world": 4, "international": 3, "continental": 2, "territorial": 1}
        tier_bonus = {"A": 12, "B+": 8, "B": 4, "C": 0}
        options = [
            record for record in ranked
            if record.get("eligible")
            and record.get("deadline_status") != "closed"
            and record.get("premiere_opportunity")
            and record.get("premiere_constraint", {}).get("scope") in scope_priority
            and record.get("bucket") in {"submit_first", "prioritize_next"}
        ]
        def launch_utility(record: dict[str, Any]) -> float:
            return (
                float(record.get("score", 0))
                + tier_bonus.get((record.get("tier") or "C").upper(), 0)
                + float(record.get("ratings", {}).get("strategic_value", 0)) * 2
                + scope_priority.get(record.get("premiere_constraint", {}).get("scope"), 0)
            )

        options.sort(key=launch_utility, reverse=True)
        chosen = options[0] if options else None
        target = None if not chosen else {
            "id": chosen["id"],
            "name": chosen["name"],
            "country": chosen.get("country"),
            "city": chosen.get("city"),
            "region": chosen.get("region"),
            "scope": chosen.get("premiere_constraint", {}).get("scope"),
            "selection_score": round(launch_utility(chosen), 1),
            "reason": (
                f"Best launch utility among viable premiere opportunities: score "
                f"{chosen.get('score')}/100, tier {chosen.get('tier')}, and strategic-fit "
                f"rating {chosen.get('ratings', {}).get('strategic_value', 0)}/5."
            ),
        }

    for record in ranked:
        record["post_target_compatibility"] = domain.post_target_compatibility(
            record, target, profile.get("country")
        )
        record["pre_target_compatibility"] = domain.pre_target_compatibility(
            record, chosen if target else None, profile.get("country")
        )
        record["premiere_sequence"] = premiere_sequence_role(record)
    return target


def roadmap_builder(
    llm: LLMClient,
    trace: Trace,
    profile: dict[str, Any],
    ranked: list[dict[str, Any]],
    memory: dict[str, Any],
    recommended_target: dict[str, Any] | None,
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
        "recommended_premiere_target": recommended_target,
        "festivals": [
            {
                "id": r["id"],
                "name": r["name"],
                "score": r["score"],
                "bucket": r["bucket"],
                "deadline": {
                    key: (r.get("deadline") or {}).get(key)
                    for key in (
                        "status", "next_deadline", "next_submission_open",
                        "confidence", "is_projection", "recorded_cycle_closed",
                    )
                },
                "premiere_constraint": {
                    key: (r.get("premiere_constraint") or {}).get(key)
                    for key in ("scope", "territory", "confidence")
                },
                "premiere_risk": r["premiere_risk"],
                "eligible": r.get("eligible"),
                "eligibility_issue": r.get("eligibility_issue"),
                "post_target_compatibility": {
                    "status": (r.get("post_target_compatibility") or {}).get("status")
                },
                "pre_target_compatibility": {
                    "status": (r.get("pre_target_compatibility") or {}).get("status")
                },
                "premiere_sequence": {
                    "status": (r.get("premiere_sequence") or {}).get("status")
                },
                "deadline_status": r["deadline_status"],
                "uncertainty_flags": {
                    "deadline": (r.get("deadline") or {}).get("confidence") != "high",
                    "premiere": (r.get("premiere_constraint") or {}).get("confidence") != "high",
                },
                "evidence": r.get("evidence"),
            }
            for r in ranked
        ],
    }
    if revision_instructions:
        payload["revision_instructions"] = revision_instructions

    user = json.dumps(payload, ensure_ascii=False)
    roadmap = llm.complete_json(
        prompts.ROADMAP_BUILDER, user, max_tokens=2500, module="RoadmapBuilder"
    )
    return roadmap


# ------------------------------------------------------------------- Replanner
def replanner(
    trace: Trace,
    ranked: list[dict[str, Any]],
    roadmap: dict[str, Any],
    recommended_target: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate structural and cross-festival invariants before publication."""

    records = {record["id"]: record for record in ranked}
    expected = {festival_id: record["bucket"] for festival_id, record in records.items()}
    occurrences: dict[str, list[str]] = {}
    unknown: list[str] = []
    malformed_entries = 0
    invalid_evidence_references: list[dict[str, Any]] = []
    allowed_evidence = set(scoring.WEIGHTS)
    raw_buckets = roadmap.get("buckets")
    if not isinstance(raw_buckets, dict):
        raw_buckets = {}
        malformed_entries += 1
    for bucket in ROADMAP_BUCKETS:
        entries = raw_buckets.get(bucket, [])
        if not isinstance(entries, list):
            malformed_entries += 1
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                malformed_entries += 1
                continue
            festival_id = entry.get("id")
            if festival_id not in expected:
                unknown.append(str(festival_id))
            else:
                occurrences.setdefault(festival_id, []).append(bucket)
                references = entry.get("evidence_dimensions")
                if (
                    not isinstance(references, list)
                    or not 1 <= len(references) <= 2
                    or len(set(references)) != len(references)
                    or any(reference not in allowed_evidence for reference in references)
                ):
                    invalid_evidence_references.append(
                        {"id": festival_id, "evidence_dimensions": references}
                    )
                else:
                    record = records[festival_id]
                    evidence = record.get("evidence", {}) or {}
                    ratings = record.get("ratings", {}) or {}
                    ungrounded = [
                        reference for reference in references
                        if not str(evidence.get(reference) or "").strip()
                        or str(evidence.get(reference)).startswith("No grounded evidence")
                    ]
                    weak_positive_explanation = (
                        expected[festival_id] in {"submit_first", "leverage"}
                        and max(float(ratings.get(reference, 0) or 0) for reference in references) < 3
                    )
                    if ungrounded or weak_positive_explanation:
                        invalid_evidence_references.append(
                            {
                                "id": festival_id,
                                "evidence_dimensions": references,
                                "ungrounded": ungrounded,
                                "weak_positive_explanation": weak_positive_explanation,
                            }
                        )

    missing = [festival_id for festival_id in expected if festival_id not in occurrences]
    duplicates = [festival_id for festival_id, seen in occurrences.items() if len(seen) > 1]
    wrong_bucket = [
        {"id": festival_id, "expected": expected[festival_id], "actual": seen[0]}
        for festival_id, seen in occurrences.items()
        if seen and seen[0] != expected[festival_id]
    ]
    expected_target = recommended_target.get("id") if recommended_target else None
    raw_target = roadmap.get("premiere_target")
    actual_target = raw_target.get("id") if isinstance(raw_target, dict) else None
    if raw_target is not None and not isinstance(raw_target, dict):
        malformed_entries += 1
    target_mismatch = actual_target != expected_target
    defects = {
        "missing_ids": missing,
        "duplicate_ids": duplicates,
        "unknown_ids": unknown,
        "wrong_buckets": wrong_bucket,
        "premiere_target_mismatch": (
            {"expected": expected_target, "actual": actual_target} if target_mismatch else None
        ),
        "malformed_entries": malformed_entries,
        "invalid_evidence_references": invalid_evidence_references,
    }
    valid = not any([
        missing, duplicates, unknown, wrong_bucket, target_mismatch,
        malformed_entries, invalid_evidence_references,
    ])
    instructions = None if valid else (
        "Return every supplied id exactly once in its assigned bucket, invent no ids, "
        f"and use premiere target {expected_target!r} exactly. Defects: {json.dumps(defects)}"
    )
    decision = {
        "decision": "accept" if valid else "revise",
        "reason": "All roadmap invariants hold." if valid else "Roadmap invariants failed.",
        "defects": defects,
        "revision_instructions": instructions,
    }
    trace.add(
        "Replanner",
        {
            "operation": "deterministic_roadmap_validation",
            "invariants": [
                "complete", "unique", "known_ids", "assigned_bucket",
                "grounded_evidence_references", "single_premiere_target",
            ],
        },
        decision,
    )
    return decision


def _grounded_why(
    record: dict[str, Any], preferred_dimensions: list[str] | None = None
) -> str:
    evidence = record.get("evidence", {}) or {}
    ratings = record.get("ratings", {}) or {}
    dimensions = [
        "thematic_fit", "genre_fit", "lineup_similarity", "company_relationship",
        "strategic_value", "deadline_urgency",
    ]
    dimensions.sort(key=lambda dimension: float(ratings.get(dimension, 0) or 0), reverse=True)
    preferred = [
        dimension for dimension in (preferred_dimensions or []) if dimension in dimensions
    ]
    dimensions = preferred + [dimension for dimension in dimensions if dimension not in preferred]
    phrases = []
    for dimension in dimensions:
        value = str(evidence.get(dimension) or "").strip().rstrip(".")
        if value and not value.startswith("No grounded evidence"):
            phrases.append(value)
        if len(phrases) == 2:
            break
    return "; ".join(phrases) + "." if phrases else "The computed score supports this placement."


def _evidence_dimensions(
    record: dict[str, Any], preferred_dimensions: Any
) -> list[str]:
    allowed = list(scoring.WEIGHTS)
    if isinstance(preferred_dimensions, list):
        evidence = record.get("evidence", {}) or {}
        preferred = [
            dimension for dimension in preferred_dimensions
            if dimension in allowed
            and str(evidence.get(dimension) or "").strip()
            and not str(evidence.get(dimension)).startswith("No grounded evidence")
        ]
        if (
            record.get("bucket") in {"submit_first", "leverage"}
            and preferred
            and max(float((record.get("ratings") or {}).get(dimension, 0) or 0) for dimension in preferred) < 3
        ):
            preferred = []
        if preferred:
            return preferred[:2]
    evidence = record.get("evidence", {}) or {}
    ratings = record.get("ratings", {}) or {}
    available = [
        dimension for dimension in allowed
        if evidence.get(dimension) and not str(evidence[dimension]).startswith("No grounded evidence")
    ]
    available.sort(key=lambda dimension: float(ratings.get(dimension, 0) or 0), reverse=True)
    return (available or ["deadline_urgency"])[:2]


_QUESTION_STOP_WORDS = {
    "and", "are", "confirm", "current", "film", "for", "from", "information",
    "missing", "official", "provide", "site", "that", "the", "this", "what",
    "with", "your",
}


def _question_terms(value: Any) -> set[str]:
    """Return stable content terms for conservative open-question deduplication."""

    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", str(value).lower()):
        if token in _QUESTION_STOP_WORDS or len(token) < 4:
            continue
        if token.endswith("ies") and len(token) > 5:
            token = token[:-3] + "y"
        elif token.endswith("ed") and len(token) > 5:
            token = token[:-2]
        elif token.endswith("s") and not token.endswith(("ss", "us")) and len(token) > 4:
            token = token[:-1]
        terms.add(token)
    return terms


def _duplicates_question(question: str, existing: list[str]) -> bool:
    candidate = _question_terms(question)
    if not candidate:
        return question in existing
    for current in existing:
        terms = _question_terms(current)
        overlap = len(candidate & terms)
        smaller = min(len(candidate), len(terms)) if terms else 0
        if smaller == 1 and overlap == 1:
            return True
        if overlap >= 2 and overlap / smaller >= 0.5:
            return True
    return False


def _counted(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _constrained_action(record: dict[str, Any]) -> str:
    sequence = record.get("premiere_sequence") or premiere_sequence_role(record)
    sequence_status = sequence.get("status")
    deadline = record.get("deadline", {}) or {}
    next_deadline = deadline.get("next_deadline")

    if not record.get("eligible", True):
        base = "Do not submit unless the recorded eligibility conflict is resolved."
    elif float(record.get("score", 0) or 0) < 45:
        base = "Do not prioritize this submission; the evidence-supported score is below the minimum threshold."
    elif record.get("premiere_risk") == "high" and not record.get("premiere_opportunity"):
        base = "Hold this submission because the required premiere appears unavailable."
    elif sequence_status == "target":
        base = f"Submit as the premiere target{f' before {next_deadline}' if next_deadline else ''}."
    elif sequence_status == "alternative_only":
        if record.get("deadline_status") == "closed":
            base = (
                f"Keep as an alternative premiere path only for the next projected cycle"
                f"{f' around {next_deadline}' if next_deadline else ''}; do not screen here "
                "within the selected target sequence."
            )
        elif record.get("bucket") == "submit_first":
            base = (
                "Submit in the first wave as an alternative premiere path; do not accept "
                "a screening there unless the selected target is abandoned."
            )
        else:
            base = (
                "Plan the next submission wave as an alternative premiere path; do not "
                "accept a screening there unless the selected target is abandoned."
            )
    elif sequence_status == "verify":
        if record.get("deadline_status") == "closed":
            base = "Verify the official premiere rule before preparing for the next cycle."
        else:
            base = "Verify the official premiere rule before committing or submitting."
    elif record.get("bucket") == "hold_avoid" and record.get("deadline_status") in {"closed", "upcoming"}:
        base = f"Hold and reassess for the next cycle{f' around {next_deadline}' if next_deadline else ''}."
    elif record.get("deadline_status") == "closed":
        base = f"Prepare for the next projected cycle{f' around {next_deadline}' if next_deadline else ''}."
    elif record.get("bucket") == "hold_avoid":
        base = "Do not prioritize this submission unless its fit or eligibility evidence changes."
    elif record.get("bucket") == "leverage":
        base = f"Use the recorded company relationship and prepare the submission{f' before {next_deadline}' if next_deadline else ''}."
    elif record.get("bucket") == "submit_first":
        base = f"Prepare and submit in the first wave{f' before {next_deadline}' if next_deadline else ''}."
    else:
        base = f"Plan the next submission wave{f' before {next_deadline}' if next_deadline else ''}."

    if (
        sequence_status == "must_follow_target"
        and record.get("eligible", True)
        and float(record.get("score", 0) or 0) >= 45
    ):
        base = (
            f"{base.rstrip('.')}. Do not accept a screening "
            "before the selected premiere target."
        )
    elif (
        sequence_status == "verify"
        and record.get("eligible", True)
        and float(record.get("score", 0) or 0) >= 45
    ):
        base = (
            f"{base.rstrip('.')}. Verify that an earlier screening would preserve the "
            "selected premiere target before accepting it."
        )

    if deadline.get("is_projection"):
        base = f"{base.rstrip('.')}. Verify the projected date against the official call."
    return base


def normalise_roadmap(
    roadmap: dict[str, Any],
    ranked: list[dict[str, Any]],
    recommended_target: dict[str, Any] | None,
    profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make the published roadmap satisfy the validated business invariants."""

    existing: dict[str, dict[str, Any]] = {}
    raw_buckets = roadmap.get("buckets") if isinstance(roadmap.get("buckets"), dict) else {}
    for entries in raw_buckets.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            festival_id = entry.get("id")
            if festival_id and festival_id not in existing:
                existing[festival_id] = entry

    buckets = {bucket: [] for bucket in ROADMAP_BUCKETS}
    for record in ranked:
        entry = existing.get(record["id"], {})
        evidence_dimensions = _evidence_dimensions(
            record, entry.get("evidence_dimensions")
        )
        buckets[record["bucket"]].append(
            {
                "id": record["id"],
                "evidence_dimensions": evidence_dimensions,
                "why": _grounded_why(record, evidence_dimensions),
                "action": _constrained_action(record),
            }
        )

    dated = [
        record for record in ranked
        if record.get("bucket") != "hold_avoid" and record.get("deadline", {}).get("next_deadline")
    ]
    dated.sort(key=lambda record: record["deadline"]["next_deadline"])
    calendar = [
        {
            "month": record["deadline"]["next_deadline"][:7],
            "action": f"{record['name']}: {_constrained_action(record)}",
        }
        for record in dated
    ]

    raw_questions = roadmap.get("open_questions")
    llm_questions = [str(value) for value in raw_questions if value] if isinstance(raw_questions, list) else []
    open_questions = []
    missing_items = (profile or {}).get("missing_info", []) or []
    critical_pattern = re.compile(
        r"\b(?:runtime|format|completion|release|picture lock|final cut)\b",
        flags=re.I,
    )
    missing_items = sorted(
        enumerate(missing_items),
        key=lambda item: (
            not (
                _is_premiere_state_missing(item[1])
                or bool(critical_pattern.search(str(item[1])))
            ),
            item[0],
        ),
    )
    missing_items = [item for _, item in missing_items]
    for missing in missing_items[:4]:
        missing_text = str(missing).strip()
        if _is_premiere_state_missing(missing_text):
            question = (
                "High-impact: confirm every prior public screening and the film's remaining "
                f"premiere status ({missing_text})."
            )
        else:
            question = f"Provide or confirm the missing film information: {missing_text}."
        if question not in open_questions:
            open_questions.append(question)
    if len(missing_items) > 4:
        remaining = len(missing_items) - 4
        open_questions.append(
            f"Provide or confirm the remaining {_counted(remaining, 'missing film field')} "
            "listed in the analysis trace."
        )
    missing_questions = list(open_questions)
    uncertain = [record for record in ranked if record.get("uncertainties")]
    for record in uncertain[:2]:
        open_questions.append(
            f"Confirm current deadline and premiere rules for {record['name']} on its official site."
        )
    if len(uncertain) > 2:
        remaining = len(uncertain) - 2
        open_questions.append(
            f"Confirm current official rules for the remaining "
            f"{_counted(remaining, 'uncertain candidate')}."
        )
    for question in llm_questions:
        if question not in open_questions and not _duplicates_question(
            question, missing_questions
        ):
            open_questions.append(question)

    next_actions = [
        "Verify current deadlines and premiere rules on each shortlisted festival's official site.",
        "Prepare one master submission package for the highest-ranked viable festivals.",
        "Contact recorded company relationships only where the film fit and roadmap bucket justify it.",
    ]

    def names(records: list[dict[str, Any]], limit: int = 4) -> str:
        chosen = [record["name"] for record in records[:limit]]
        remainder = len(records) - len(chosen)
        return ", ".join(chosen) + (f", plus {remainder} more" if remainder else "")

    target_name = (recommended_target or {}).get("name")
    target_scope = (recommended_target or {}).get("scope")
    must_follow_target = [
        record for record in ranked
        if record.get("bucket") != "hold_avoid"
        and record.get("premiere_sequence", {}).get("status") == "must_follow_target"
    ]
    backups = [
        record for record in ranked
        if record.get("premiere_sequence", {}).get("status") == "alternative_only"
    ]
    closed = [record for record in ranked if record.get("deadline_status") == "closed"]
    uncertain_count = sum(bool(record.get("uncertainties")) for record in ranked)
    if recommended_target:
        submit_count = sum(
            record.get("bucket") == "submit_first"
            and record.get("premiere_sequence", {}).get("status") != "alternative_only"
            for record in ranked
        )
    else:
        submit_count = sum(record.get("bucket") == "submit_first" for record in ranked)

    if target_name:
        target_record = next(
            (record for record in ranked if record.get("id") == recommended_target.get("id")),
            {},
        )
        if target_record.get("bucket") == "prioritize_next":
            headline = (
                f"Next-cycle premiere target: {target_name}; "
                f"{_counted(submit_count, 'currently actionable first-wave candidate')} and "
                f"{_counted(len(backups), 'mutually exclusive premiere alternative')}"
            )
        else:
            headline = (
                f"Premiere target: {target_name}; "
                f"{_counted(submit_count, 'first-wave candidate')} and "
                f"{_counted(len(backups), 'mutually exclusive premiere alternative')}"
            )
        summary_parts = [
            f"Use {target_name} as the intended first public festival screening; its "
            f"recorded premiere scope is {target_scope or 'unknown'} and must be verified."
        ]
    else:
        headline = (
            f"Evidence-grounded roadmap with "
            f"{_counted(submit_count, 'first-wave candidate')}"
        )
        premiere_status = (profile or {}).get("premiere_status")
        if premiere_status == "already_premiered":
            summary_parts = [
                "No new premiere target is selected because the film's world premiere is already consumed; "
                "the roadmap uses only remaining territorial eligibility."
            ]
        elif premiere_status == "unknown":
            summary_parts = [
                "No premiere target is selected until the film's screening history and "
                "remaining premiere status are confirmed."
            ]
        else:
            summary_parts = [
                "No defensible premiere target can be selected from the retrieved candidates "
                "until the missing eligibility evidence is resolved."
            ]
    if must_follow_target:
        summary_parts.append(
            f"Submit to {names(must_follow_target)} when their deadlines require it, but "
            "accept screenings there only after the selected premiere target."
        )
    if backups:
        backup_phrase = "as an alternative only" if len(backups) == 1 else "as alternatives only"
        summary_parts.append(
            f"Keep {names(backups)} {backup_phrase}; under the current source interpretation they cannot follow the target."
        )
    if closed:
        summary_parts.append(
            f"Move {names(closed)} to their next projected submission cycles."
        )
    if uncertain_count:
        summary_parts.append(
            f"Verify official current rules for "
            f"{_counted(uncertain_count, 'candidate')} before committing."
        )

    normalized = dict(roadmap)
    normalized["headline"] = headline
    normalized["strategy_summary"] = " ".join(summary_parts)
    normalized["premiere_target"] = recommended_target
    normalized["buckets"] = buckets
    normalized["calendar"] = calendar
    normalized["next_actions"] = next_actions
    normalized["open_questions"] = open_questions[:10]
    return normalized
