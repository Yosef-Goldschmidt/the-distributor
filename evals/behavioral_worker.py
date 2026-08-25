"""Behavioral evaluation worker loaded against an immutable project snapshot.

This file intentionally lives outside the production packages. The runner places
the committed repository snapshot first on ``sys.path`` so all evaluated behavior
comes from the recorded git commit, not from later working-tree edits.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx


UNKNOWN_INPUT = (
    "I have a 75-minute documentary from Israel about a divorced father rebuilding "
    "his relationship with his teenage daughter. The film is nearly finished. I want "
    "to know which festivals I should target."
)
ALREADY_PREMIERED_INPUT = (
    "I have an Israeli feature documentary. The film premiered publicly at Docaviv "
    "in May 2026. Build a festival strategy."
)
CONTRADICTORY_INPUT = (
    "Runtime: 82 minutes. Never publicly screened. Final runtime: 96 minutes. "
    "Premiered last month in Jerusalem. Build a festival strategy."
)


def scenario(
    name: str,
    status: str,
    reason: str,
    *,
    test_input: Any,
    expected: list[str],
    observed: Any,
    diagnosis: dict[str, Any] | None = None,
    baseline_blocker: bool = False,
    later_100: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "reason": reason,
        "input": test_input,
        "expected_invariants": expected,
        "observed": observed,
        "diagnosis": diagnosis or {},
        "baseline_blocker": baseline_blocker,
        "later_100": later_100,
    }


class StaticLLM:
    """Return one deterministic FilmAnalyzer payload without pretending it is live."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def complete_json(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return deepcopy(self.payload)


def film_profile(**overrides: Any) -> dict[str, Any]:
    profile = {
        "title": None,
        "logline": "An intimate family documentary.",
        "format": "feature_doc",
        "genres": ["documentary"],
        "themes": ["family", "parenthood"],
        "country": "Israel",
        "language": None,
        "runtime_minutes": 75,
        "director_profile": None,
        "premiere_status": "unknown",
        "premiere_history": [],
        "target_audience": "adult documentary audiences",
        "festival_angles": ["intimate family portrait"],
        "missing_info": ["premiere status"],
        "search_query": "intimate adult documentary family parenthood relationship",
    }
    profile.update(overrides)
    return profile


def unknown_premiere_case(modules: Any) -> dict[str, Any]:
    trace = modules.Trace()
    adversarial_profile = film_profile(
        premiere_status="world_premiere_available",
        premiere_history=[{"festival": "Invented", "country": "Israel"}],
        target_audience="Youth and family audiences",
        missing_info=[],
    )
    profile = modules.film_analyzer(StaticLLM(adversarial_profile), trace, UNKNOWN_INPUT)
    target = modules.apply_premiere_strategy(profile, [])
    roadmap = modules.normalise_roadmap(
        {"buckets": {}, "open_questions": []}, [], target, profile
    )
    question_text = " ".join(roadmap["open_questions"]).lower()
    downstream_ok = (
        profile["premiere_status"] == "unknown"
        and target is None
        and "premiere" in question_text
        and "no premiere target is selected" in roadmap["strategy_summary"].lower()
    )
    return scenario(
        "Unknown premiere",
        "PASS" if downstream_ok else "FAIL",
        (
            "The exact input stays unknown even when an adversarial FilmAnalyzer payload claims world-premiere availability; no target is selected and the missing fact is high-impact."
            if downstream_ok
            else "The deterministic downstream policy manufactured certainty from unknown premiere data."
        ),
        test_input=UNKNOWN_INPUT,
        expected=[
            "premiere_status remains unknown",
            "no confirmed premiere target",
            "strategy is preliminary and premiere status is surfaced",
        ],
        observed={
            "profile_premiere_status": profile["premiere_status"],
            "premiere_target": target,
            "strategy_summary": roadmap["strategy_summary"],
            "open_questions": roadmap["open_questions"],
            "adversarial_model_claim": "world_premiere_available",
            "coverage": "adversarial FilmAnalyzer payload + exact input + real deterministic downstream modules",
        },
        diagnosis={
            "input_evidence_guard": "PASS" if downstream_ok else "FAIL",
            "premiere_logic": "PASS" if downstream_ok else "FAIL",
            "roadmap_presentation": "PASS" if downstream_ok else "FAIL",
        },
        baseline_blocker=not downstream_ok,
    )


def already_premiered_case(modules: Any, domain: Any, festivals: list[dict[str, Any]]) -> dict[str, Any]:
    strict = next(
        festival
        for festival in festivals
        if domain.premiere_constraint(festival)["scope"] == "world"
        and "feature_doc" in (festival.get("accepts") or [])
    )
    payload = film_profile(
        premiere_status="already_premiered",
        premiere_history=[
            {"festival": "Docaviv", "country": "Israel", "date": "2026-05"}
        ],
        missing_info=[],
    )
    trace = modules.Trace()
    profile = modules.film_analyzer(StaticLLM(payload), trace, ALREADY_PREMIERED_INPUT)
    assessment = domain.assess_premiere(profile, strict)
    target = modules.apply_premiere_strategy(profile, [])
    ok = (
        profile["premiere_status"] == "already_premiered"
        and not assessment["eligible"]
        and assessment["premiere_risk"] == "high"
        and target is None
    )
    return scenario(
        "Already premiered",
        "PASS" if ok else "FAIL",
        (
            "Explicit public-premiere evidence forces post-premiere behavior and removes strict world-premiere paths."
            if ok
            else "A strict world-premiere path remained viable after an explicit public premiere."
        ),
        test_input=ALREADY_PREMIERED_INPUT,
        expected=[
            "world premiere unavailable",
            "strict world-premiere options ineligible",
            "no strategy relies on preserving world premiere",
        ],
        observed={
            "profile_premiere_status": profile["premiere_status"],
            "strict_world_festival": strict["id"],
            "strict_world_assessment": assessment,
            "premiere_target": target,
            "coverage": "mocked FilmAnalyzer payload + real deterministic downstream modules",
        },
        diagnosis={
            "input_evidence_guard": "PASS" if ok else "FAIL",
            "premiere_logic": "PASS" if ok else "FAIL",
        },
        baseline_blocker=not ok,
    )


def contradictory_case(modules: Any) -> dict[str, Any]:
    # The adversarial model output silently selects the later statements. A
    # deterministic validation layer should still refuse to treat critical
    # contradictions as certain, but it currently sees only the selected JSON.
    payload = film_profile(
        runtime_minutes=96,
        premiere_status="already_premiered",
        premiere_history=[
            {"festival": None, "country": "Israel", "date": "last month"}
        ],
        missing_info=[],
    )
    trace = modules.Trace()
    profile = modules.film_analyzer(StaticLLM(payload), trace, CONTRADICTORY_INPUT)
    adjustments = profile.get("_validation", {}).get("adjustments", [])
    contradiction_terms = " ".join(profile.get("missing_info", []) + adjustments).lower()
    surfaced = "contradict" in contradiction_terms or (
        "runtime" in contradiction_terms and "premiere" in contradiction_terms
    )
    return scenario(
        "Contradictory critical facts",
        "PASS" if surfaced else "FAIL",
        (
            "Critical contradictions are surfaced."
            if surfaced
            else "If FilmAnalyzer silently resolves conflicting runtime and screening facts, no deterministic guard detects the loss of uncertainty."
        ),
        test_input=CONTRADICTORY_INPUT,
        expected=[
            "do not silently select one runtime",
            "do not silently select one premiere fact",
            "require clarification before treating critical state as certain",
        ],
        observed={
            "accepted_runtime_minutes": profile["runtime_minutes"],
            "accepted_premiere_status": profile["premiere_status"],
            "accepted_premiere_history": profile["premiere_history"],
            "missing_info": profile["missing_info"],
            "validation_adjustments": adjustments,
            "contradiction_surfaced": surfaced,
        },
        diagnosis={
            "root_layer": "deterministic FilmAnalyzer output validation",
            "root_cause": (
                "The validator receives only normalized JSON and does not compare critical fields "
                "against conflicting spans in the original request."
            ),
        },
        baseline_blocker=not surfaced,
    )


def hard_mismatch_case(domain: Any) -> dict[str, Any]:
    short_only = {
        "id": "fixture-short-only",
        "name": "Fixture Short Only",
        "accepts": ["short_fiction", "animation"],
        "premiere_requirement": "none",
        "premiere_requirement_raw": "No requirement",
        "focus": "Short works in a descriptive 5-20-minute range.",
        "notes": "",
    }
    feature_range = {
        "id": "fixture-feature-range",
        "name": "Fixture Feature Range",
        "accepts": ["feature_fiction"],
        "premiere_requirement": "none",
        "premiere_requirement_raw": "No requirement",
        "focus": "Feature films in a descriptive 70-130-minute range.",
        "notes": "",
    }
    cases = {
        "very_short": domain.assess_premiere(
            film_profile(format="short_fiction", runtime_minutes=3), short_only
        ),
        "normal_feature_wrong_format": domain.assess_premiere(
            film_profile(format="feature_fiction", runtime_minutes=100), short_only
        ),
        "very_long_feature": domain.assess_premiere(
            film_profile(format="feature_fiction", runtime_minutes=240), feature_range
        ),
        "animation": domain.assess_premiere(
            film_profile(format="animation", runtime_minutes=12), short_only
        ),
        "experimental_wrong_format": domain.assess_premiere(
            film_profile(format="experimental", runtime_minutes=12), short_only
        ),
    }
    format_ok = (
        not cases["normal_feature_wrong_format"]["eligible"]
        and not cases["experimental_wrong_format"]["eligible"]
        and cases["animation"]["eligible"]
    )
    runtime_is_warning = (
        cases["very_short"]["eligible"]
        and bool(cases["very_short"]["runtime_warning"])
        and cases["very_long_feature"]["eligible"]
        and bool(cases["very_long_feature"]["runtime_warning"])
    )
    status = "WARN" if format_ok and runtime_is_warning else "FAIL"
    return scenario(
        "Hard format and runtime mismatch",
        status,
        (
            "Structured format incompatibility dominates as required. Runtime ranges are only descriptive text in the corpus, so the system correctly warns but cannot enforce them as hard rules without authoritative data."
            if status == "WARN"
            else "At least one structured hard-format incompatibility did not dominate thematic fit."
        ),
        test_input={
            "formats": ["very short", "normal feature", "very long feature", "animation", "experimental"],
            "fixtures": "synthetic structured eligibility records, evaluation-only",
        },
        expected=[
            "structured hard incompatibility dominates",
            "descriptive runtime language is not overstated as an official rule",
        ],
        observed=cases,
        diagnosis={
            "format_logic": "PASS" if format_ok else "FAIL",
            "runtime_logic": "WARN: corpus lacks authoritative structured runtime limits",
        },
        later_100=True,
    )


def retrieve(
    modules: Any,
    corpus: Any,
    query: str,
    memory: dict[str, Any] | None = None,
) -> tuple[list[tuple[str, float]], list[dict[str, Any]], Any]:
    lexical = corpus.lexical_search(query, top_k=36)
    trace = modules.Trace()
    candidates = modules.festival_search(
        trace,
        film_profile(search_query=query),
        memory or {"company": {}, "history": []},
    )
    return lexical, candidates, trace.steps


def retrieval_case(
    modules: Any,
    corpus: Any,
    festivals: list[dict[str, Any]],
    *,
    name: str,
    query: str,
    expected_ids: set[str],
    expected_name_fragment: str | None = None,
    minimum_expected: int = 1,
    later_100: bool = False,
) -> dict[str, Any]:
    corpus_ids = {festival["id"] for festival in festivals}
    discovered_ids = set(expected_ids & corpus_ids)
    if expected_name_fragment:
        discovered_ids.update(
            festival["id"]
            for festival in festivals
            if expected_name_fragment.lower() in festival.get("name", "").lower()
        )
    lexical, candidates, trace_steps = retrieve(modules, corpus, query)
    lexical_ids = [festival_id for festival_id, _ in lexical]
    candidate_ids = [candidate["id"] for candidate in candidates]
    expected_in_lexical = [festival_id for festival_id in lexical_ids if festival_id in discovered_ids]
    expected_in_candidates = [
        festival_id for festival_id in candidate_ids if festival_id in discovered_ids
    ]

    if not discovered_ids:
        status = "FAIL"
        layer = "corpus/data problem"
        explanation = "The expected specialist is absent from the committed corpus; retrieval cannot recover a missing entity."
    elif len(expected_in_lexical) < minimum_expected:
        status = "FAIL"
        layer = "retrieval recall problem"
        explanation = "The specialist exists in the corpus but is absent from the broad top-36 lexical retrieval pool."
    elif len(expected_in_candidates) < minimum_expected:
        status = "FAIL"
        layer = "retrieval recall problem"
        explanation = "The specialist is broadly retrieved but is dropped before the 12-candidate scoring pool."
    else:
        status = "PASS"
        layer = "candidate recall passed"
        explanation = "The relevant specialist exists and survives into the 12-candidate scoring pool."

    score_lookup = dict(lexical)
    fallback_step = next(
        (
            step
            for step in trace_steps
            if step.get("module") == "FestivalSearch"
            and isinstance(step.get("response"), dict)
            and "festivals" in step["response"]
        ),
        {},
    )
    return scenario(
        name,
        status,
        explanation,
        test_input=query,
        expected=[
            "specialist exists in corpus when claimed",
            "specialist survives broad retrieval",
            "specialist survives the candidate-pool cutoff",
            "scoring and roadmap diagnoses remain separate",
        ],
        observed={
            "expected_ids_found_in_corpus": sorted(discovered_ids),
            "expected_ids_in_lexical_top_36": expected_in_lexical,
            "expected_ids_in_candidate_pool": expected_in_candidates,
            "expected_lexical_scores": {
                festival_id: score_lookup.get(festival_id)
                for festival_id in sorted(discovered_ids)
            },
            "lexical_top_12": lexical[:12],
            "candidate_ids": candidate_ids,
            "candidate_details": fallback_step.get("response", {}).get("festivals", []),
        },
        diagnosis={
            "data_corpus": "FAIL" if not discovered_ids else "PASS",
            "retrieval_recall": (
                "NOT ASSESSABLE" if not discovered_ids else (
                    "PASS" if len(expected_in_candidates) >= minimum_expected else "FAIL"
                )
            ),
            "scoring_ranking": "NOT RUN (requires MatchScorer judgment)",
            "roadmap_presentation": "NOT RUN (requires ranked strategy)",
            "root_layer": layer,
        },
        baseline_blocker=False,
        later_100=later_100 or status == "FAIL",
    )


def youth_over_inference_case(modules: Any, corpus: Any, scoring: Any) -> dict[str, Any]:
    query = (
        "75 minute feature documentary from Israel about a divorced father rebuilding his "
        "relationship with his teenage daughter; adult family drama documentary audience"
    )
    lexical, candidates, _ = retrieve(modules, corpus, query)
    youth_ids = {
        "giffoni-film-festival-gff",
        "zlin-iff",
        "vienna-international-children-s-ff",
        "kineko-international-children-s-film-festival",
        "doxs-documentaries-for-children-and-young-people",
        "montreal-international-children-s-film-festival",
    }
    top_12 = lexical[:12]
    lexical_youth = [(festival_id, score) for festival_id, score in top_12 if festival_id in youth_ids]
    candidate_youth = [
        {
            "id": candidate["id"],
            "retrieval_score": candidate["retrieval_score"],
            "rank": index,
        }
        for index, candidate in enumerate(candidates, 1)
        if candidate["id"] in youth_ids
    ]
    youth_candidate = next(
        candidate for candidate in candidates if candidate["id"] in youth_ids
    )
    adversarial_profile = film_profile(
        target_audience="Youth and family audiences",
        missing_info=[],
    )
    adult_profile = modules.film_analyzer(
        StaticLLM(adversarial_profile), modules.Trace(), UNKNOWN_INPUT
    )
    high_ratings = {dimension: 5 for dimension in scoring.LLM_DIMENSIONS}
    high_evidence = {
        dimension: "Teenage protagonist was treated as youth-audience evidence."
        for dimension in scoring.LLM_DIMENSIONS
    }
    guarded, _, guard_meta = scoring.apply_rating_guardrails(
        high_ratings,
        high_evidence,
        youth_candidate,
        (0.0, "No prior company relationship is recorded.", {}),
        adult_profile,
    )
    guarded["deadline_urgency"] = 5
    guarded_score = scoring.compute_score(guarded, "none")["score"]

    genuine_profile = modules.film_analyzer(
        StaticLLM(
            film_profile(
                format="animation",
                target_audience="Children and family audiences",
                search_query="animated feature for children and family audiences",
            )
        ),
        modules.Trace(),
        "An animated feature made for children and family audiences.",
    )
    genuine, _, genuine_meta = scoring.apply_rating_guardrails(
        high_ratings,
        high_evidence,
        youth_candidate,
        (0.0, "No prior company relationship is recorded.", {}),
        genuine_profile,
    )
    adult_capped = guard_meta["audience_guardrail"]["cap_applied"] and guarded_score <= 55
    genuine_preserved = (
        not genuine_meta["audience_guardrail"]["cap_applied"]
        and genuine["thematic_fit"] == 5
        and genuine["genre_fit"] == 5
    )
    ok = adult_capped and genuine_preserved
    return scenario(
        "Youth-audience over-inference",
        "PASS" if ok else "FAIL",
        (
            "Youth specialists may remain retrieval candidates, but adversarial 5/5 creative ratings are capped without explicit youth-audience evidence; a genuine children's animation is not capped."
            if ok
            else "The audience guardrail either allowed an unsupported strong youth fit or suppressed a genuine youth-facing animation."
        ),
        test_input=query,
        expected=[
            "teenage protagonist does not imply youth target audience",
            "no extreme youth-specialist score without audience/programming evidence",
        ],
        observed={
            "youth_specialists_in_lexical_top_12": lexical_youth,
            "youth_specialists_in_candidate_pool": candidate_youth,
            "adversarial_candidate": youth_candidate["id"],
            "adult_profile_audience_evidence": adult_profile["_audience_evidence"],
            "adult_guarded_ratings": guarded,
            "adult_guarded_score_with_max_deadline_urgency": guarded_score,
            "adult_guardrail": guard_meta["audience_guardrail"],
            "genuine_youth_profile_evidence": genuine_profile["_audience_evidence"],
            "genuine_youth_ratings": genuine,
            "genuine_youth_guardrail": genuine_meta["audience_guardrail"],
        },
        diagnosis={
            "data_corpus": "PASS: youth identities are explicit",
            "retrieval_recall": "WARN: protagonist age can still retrieve youth specialists",
            "scoring_ranking": "PASS" if adult_capped else "FAIL",
            "genuine_youth_control": "PASS" if genuine_preserved else "FAIL",
            "roadmap_presentation": "PASS: capped score cannot become an extreme youth fit",
        },
        baseline_blocker=not ok,
        later_100=False,
    )


def preference_case(modules: Any, corpus: Any, domain: Any, scoring: Any) -> dict[str, Any]:
    general_query = "feature documentary identity community human rights personal portrait"
    lgbtq_query = general_query + " LGBTQ queer community audience queer identity pride"
    _, general, _ = retrieve(modules, corpus, general_query)
    _, preferred, _ = retrieve(modules, corpus, lgbtq_query)
    focus_ids = {
        "side-by-side-lgbt-iff",
        "wicked-queer-the-boston-lgbt-film-festival",
        "festival-mix-mexico",
    }
    general_positions = {
        candidate["id"]: index
        for index, candidate in enumerate(general, 1)
        if candidate["id"] in focus_ids
    }
    preferred_positions = {
        candidate["id"]: index
        for index, candidate in enumerate(preferred, 1)
        if candidate["id"] in focus_ids
    }

    incompatible = {
        "id": "fixture-preference-incompatible",
        "accepts": ["short_fiction"],
        "premiere_requirement": "none",
        "premiere_requirement_raw": "No requirement",
        "focus": "prestige",
        "notes": "",
    }
    risk = domain.assess_premiere(
        film_profile(format="feature_doc", premiere_status="world_premiere_available"),
        incompatible,
    )
    bucket = scoring.assign_bucket(
        {
            "score": 100,
            "tier": "A",
            "deadline_status": "open",
            "eligible": risk["eligible"],
            "premiere_risk": risk["premiere_risk"],
            "premiere_opportunity": risk["premiere_opportunity"],
            "ratings": {"company_relationship": 5},
        }
    )
    retrieval_changed = bool(preferred_positions) and preferred_positions != general_positions
    hard_reality_wins = not risk["eligible"] and bucket == "hold_avoid"
    return scenario(
        "User preference versus domain reality",
        "WARN" if retrieval_changed and hard_reality_wins else "FAIL",
        (
            "A compatible LGBTQ preference materially changes retrieval, and deterministic eligibility still overrides an impossible prestige request. Preference preservation remains implicit in FilmAnalyzer.search_query rather than an explicit scored field."
            if retrieval_changed and hard_reality_wins
            else "Either a legitimate preference did not affect retrieval or a hard incompatibility failed to override prestige preference."
        ),
        test_input={
            "restrictive": "I only want Cannes and Venice. Recommend nothing else.",
            "legitimate": "I care more about LGBTQ-focused festivals than prestige.",
        },
        expected=[
            "preference cannot override clear incompatibility",
            "compatible strategic preference materially affects ranking",
        ],
        observed={
            "general_lgbtq_positions": general_positions,
            "preferred_lgbtq_positions": preferred_positions,
            "incompatible_preference_eligible": risk["eligible"],
            "incompatible_preference_bucket_at_score_100": bucket,
            "final_scoring_preference_effect": "NOT RUN live",
        },
        diagnosis={
            "retrieval": "PASS" if retrieval_changed else "FAIL",
            "hard_domain_rules": "PASS" if hard_reality_wins else "FAIL",
            "scoring_ranking": "WARN: preference is represented only indirectly through generated search_query",
        },
        baseline_blocker=not hard_reality_wins,
        later_100=True,
    )


def no_good_candidates_case(modules: Any) -> dict[str, Any]:
    ranked = []
    for index, score_value in enumerate((44, 38, 21), 1):
        record = {
            "id": f"fixture-poor-{index}",
            "name": f"Fixture Poor {index}",
            "score": score_value,
            "bucket": "hold_avoid",
            "eligible": True,
            "premiere_risk": "none",
            "premiere_opportunity": False,
            "deadline_status": "open",
            "deadline": {"next_deadline": None, "is_projection": False},
            "ratings": {
                "thematic_fit": 1,
                "genre_fit": 1,
                "lineup_similarity": 1,
                "company_relationship": 0,
                "strategic_value": 1,
                "deadline_urgency": 2,
            },
            "evidence": {
                "thematic_fit": "Weak thematic overlap.",
                "genre_fit": "Weak genre overlap.",
                "lineup_similarity": "Weak lineup evidence.",
                "company_relationship": "No relationship.",
                "strategic_value": "Low strategic value.",
                "deadline_urgency": "Deadline unknown.",
            },
            "uncertainties": [],
        }
        ranked.append(record)
    target = modules.apply_premiere_strategy(
        film_profile(premiere_status="world_premiere_available"), ranked
    )
    roadmap = modules.normalise_roadmap(
        {"buckets": {}, "open_questions": []}, ranked, target, film_profile()
    )
    submit_count = len(roadmap["buckets"]["submit_first"])
    ok = target is None and submit_count == 0 and "0 first-wave" in roadmap["headline"]
    return scenario(
        "No good candidates",
        "PASS" if ok else "FAIL",
        (
            "The roadmap can return zero first-wave candidates and no premiere target."
            if ok
            else "The highest poor-fit candidate was promoted despite remaining below the minimum threshold."
        ),
        test_input="Evaluation-only ranked candidates with scores 44, 38 and 21.",
        expected=[
            "no strong target is allowed",
            "highest-in-a-bad-set is not described as excellent",
        ],
        observed={
            "premiere_target": target,
            "headline": roadmap["headline"],
            "bucket_counts": {key: len(value) for key, value in roadmap["buckets"].items()},
            "actions": [
                entry["action"]
                for entry in roadmap["buckets"]["hold_avoid"]
            ],
        },
        baseline_blocker=not ok,
    )


def deadlines_case(domain: Any) -> dict[str, Any]:
    today = date(2026, 8, 25)
    fixtures = {
        "tomorrow": {"final_deadline": "2026-08-26"},
        "yesterday": {"final_deadline": "2026-08-24"},
        "month_only": {"typical_deadline_month": "September"},
        "stale_2022": {"final_deadline": "2022-11-15"},
        "invalid_equal_open_close": {
            "submission_open": "2026-09-01",
            "final_deadline": "2026-09-01",
        },
    }
    observed = {
        name: domain.assess_deadline(record, today)
        for name, record in fixtures.items()
    }
    checks = {
        "tomorrow": (
            observed["tomorrow"]["status"] == "closing_soon"
            and not observed["tomorrow"]["is_projection"]
            and observed["tomorrow"]["confidence"] == "high"
        ),
        "yesterday": (
            observed["yesterday"]["recorded_cycle_closed"]
            and observed["yesterday"]["is_projection"]
            and observed["yesterday"]["status"] == "upcoming"
        ),
        "month_only": (
            observed["month_only"]["basis"] == "typical_month_only"
            and observed["month_only"]["next_deadline"] is None
            and observed["month_only"]["confidence"] == "low"
        ),
        "stale_2022": (
            observed["stale_2022"]["is_projection"]
            and observed["stale_2022"]["confidence"] == "low"
        ),
        "invalid_equal_open_close": (
            observed["invalid_equal_open_close"]["confidence"] == "low"
            and bool(observed["invalid_equal_open_close"]["cycle_anomalies"])
        ),
    }
    return scenario(
        "Deadline edge cases",
        "PASS" if all(checks.values()) else "FAIL",
        (
            "Exact, expired, month-only, stale and malformed deadline states preserve projection and confidence semantics."
            if all(checks.values())
            else "At least one deadline edge manufactured actionable or high-confidence timing."
        ),
        test_input=fixtures,
        expected=[
            "expired recorded cycle is not currently actionable",
            "projections are explicit",
            "stale/month-only data has low confidence",
            "invalid cycle ordering is anomalous and low-confidence",
        ],
        observed={"checks": checks, "assessments": observed},
        diagnosis={"root_layer": "deterministic deadline semantics"},
        baseline_blocker=not all(checks.values()),
    )


def company_memory_case(scoring: Any) -> dict[str, Any]:
    creative = {
        "thematic_fit": 4,
        "genre_fit": 4,
        "lineup_similarity": 3,
        "strategic_value": 3,
    }
    evidence = {dimension: f"Grounded {dimension}." for dimension in creative}
    candidate = {"tier": "B", "identity_confidence": "high"}
    empty_relationship = scoring.company_relationship_rating([], 2026)
    strong_relationship = scoring.company_relationship_rating(
        [{"screenings": 12, "years": [2024, 2026], "awards": ["Jury Prize"]}],
        2026,
    )
    empty_ratings, _, _ = scoring.apply_rating_guardrails(
        creative, evidence, candidate, empty_relationship
    )
    strong_ratings, _, _ = scoring.apply_rating_guardrails(
        creative, evidence, candidate, strong_relationship
    )
    empty_ratings["deadline_urgency"] = 2
    strong_ratings["deadline_urgency"] = 2
    empty_score = scoring.compute_score(empty_ratings, "none")["score"]
    strong_score = scoring.compute_score(strong_ratings, "none")["score"]
    creative_same = all(
        empty_ratings[key] == strong_ratings[key] for key in scoring.LLM_DIMENSIONS
    )
    bounded_delta = 0 < strong_score - empty_score <= scoring.WEIGHTS["company_relationship"]
    ok = creative_same and bounded_delta and (
        strong_ratings["company_relationship"] > empty_ratings["company_relationship"]
    )
    return scenario(
        "CompanyMemory counterfactual",
        "PASS" if ok else "FAIL",
        (
            "Company history changes only the relationship dimension and produces a bounded score delta without altering creative fit."
            if ok
            else "Company history was cosmetic, altered creative fit, or overwhelmed the decision score."
        ),
        test_input={
            "A": "Meridian-like strong history",
            "B": "same film and festival with no history",
        },
        expected=[
            "creative fit remains the same",
            "company relationship changes",
            "score/rank influence is bounded and explainable",
        ],
        observed={
            "empty_relationship_rating": empty_ratings["company_relationship"],
            "strong_relationship_rating": strong_ratings["company_relationship"],
            "empty_score": empty_score,
            "strong_score": strong_score,
            "score_delta": strong_score - empty_score,
            "creative_ratings_empty": {
                key: empty_ratings[key] for key in scoring.LLM_DIMENSIONS
            },
            "creative_ratings_strong": {
                key: strong_ratings[key] for key in scoring.LLM_DIMENSIONS
            },
        },
        baseline_blocker=not ok,
    )


def fallback_case(
    modules: Any,
    corpus: Any,
    pinecone_store: Any,
    supabase_store: Any,
    config: Any,
) -> dict[str, Any]:
    query = "religion documentary faith interreligious dialogue"

    class BrokenIndex:
        def query(self, **_kwargs: Any) -> Any:
            raise TimeoutError("simulated vector timeout")

    class BrokenPinecone:
        def Index(self, *_args: Any, **_kwargs: Any) -> BrokenIndex:
            return BrokenIndex()

    class BrokenTable:
        def select(self, *_args: Any, **_kwargs: Any) -> "BrokenTable":
            return self

        def in_(self, *_args: Any, **_kwargs: Any) -> "BrokenTable":
            return self

        def execute(self) -> Any:
            raise TimeoutError("simulated Supabase timeout")

    class BrokenSupabase:
        def table(self, *_args: Any, **_kwargs: Any) -> BrokenTable:
            return BrokenTable()

    embedding_steps: list[dict[str, Any]] = []

    def fake_embed(
        _texts: list[str],
        *,
        input_type: str,
        trace_callback: Any = None,
    ) -> list[list[float]]:
        if trace_callback:
            trace_callback(
                "FestivalSearch",
                {"provider": {"kind": "embedding", "attempt": 1}, "input_type": input_type},
                {"status": "ok", "vectors": 1, "dimension": 2},
            )
        embedding_steps.append({"attempt": 1, "status": "ok"})
        return [[0.1, 0.2]]

    with patch.object(config, "pinecone_enabled", return_value=True), patch.object(
        config, "embeddings_enabled", return_value=True
    ), patch.object(pinecone_store, "_pinecone", return_value=BrokenPinecone()), patch.object(
        pinecone_store.embeddings, "embed", side_effect=fake_embed
    ):
        trace = modules.Trace()
        matches, backend, reason = pinecone_store.search(
            query, top_k=12, trace_callback=trace.add
        )

    def failing_embed(
        _texts: list[str],
        *,
        input_type: str,
        trace_callback: Any = None,
    ) -> list[list[float]]:
        if trace_callback:
            trace_callback(
                "FestivalSearch",
                {"provider": {"kind": "embedding", "attempt": 1}, "input_type": input_type},
                {"error": "simulated embedding timeout"},
            )
        raise TimeoutError("simulated embedding timeout")

    with patch.object(config, "pinecone_enabled", return_value=True), patch.object(
        config, "embeddings_enabled", return_value=True
    ), patch.object(pinecone_store.embeddings, "embed", side_effect=failing_embed):
        embedding_failure_trace = modules.Trace()
        _, embedding_failure_backend, embedding_failure_reason = pinecone_store.search(
            query, top_k=12, trace_callback=embedding_failure_trace.add
        )

    with patch.object(config, "supabase_enabled", return_value=True), patch.object(
        supabase_store, "_supabase", return_value=BrokenSupabase()
    ):
        facts, source = supabase_store.get_festivals([festival_id for festival_id, _ in matches[:3]])
        with patch.object(
            supabase_store,
            "_local_company",
            return_value={"company": {}, "history": []},
        ):
            memory, memory_source = supabase_store.get_company_memory()

    local_ids = {festival["id"] for festival in corpus.load_festivals()}
    explicit_vector_fallback = (
        backend == "local_tfidf_fallback"
        and reason == "vector_retrieval_error:TimeoutError"
        and bool(matches)
    )
    explicit_supabase_fallback = (
        "supabase_request_failed" in source
        and all(festival["id"] in local_ids for festival in facts)
    )
    explicit_embedding_fallback = (
        embedding_failure_backend == "local_tfidf_fallback"
        and embedding_failure_reason == "vector_retrieval_error:TimeoutError"
        and len(embedding_failure_trace.steps) == 1
        and "error" in embedding_failure_trace.steps[0]["response"]
    )
    missing_memory_not_fabricated = (
        memory == {"company": {}, "history": []}
        and "supabase_request_failed" in memory_source
    )
    trace_ok = (
        len(trace.steps) == 1
        and trace.steps[0]["module"] == "FestivalSearch"
        and trace.steps[0]["prompt"]["provider"]["kind"] == "embedding"
    )
    ok = all(
        (
            explicit_vector_fallback,
            explicit_embedding_fallback,
            explicit_supabase_fallback,
            missing_memory_not_fabricated,
            trace_ok,
        )
    )
    return scenario(
        "Pinecone and Supabase failure fallback",
        "PASS" if ok else "FAIL",
        (
            "Provider failures degrade to explicitly labelled local sources; the embedding attempt remains in trace."
            if ok
            else "A provider failure was silent, mislabeled as semantic retrieval, fabricated memory/facts, or lost its attempt trace."
        ),
        test_input="Mocked Pinecone timeout and Supabase timeout.",
        expected=[
            "fallback is explicit",
            "failed semantic retrieval is not labelled successful",
            "local facts are identified as local",
            "embedding attempt appears in order",
        ],
        observed={
            "vector_backend": backend,
            "vector_fallback_reason": reason,
            "embedding_trace": trace.steps,
            "embedding_failure_backend": embedding_failure_backend,
            "embedding_failure_reason": embedding_failure_reason,
            "embedding_failure_trace": embedding_failure_trace.steps,
            "facts_source": source,
            "fact_ids": [festival["id"] for festival in facts],
            "missing_company_memory": memory,
            "missing_company_memory_source": memory_source,
            "embedding_attempts": embedding_steps,
        },
        baseline_blocker=not ok,
    )


class FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
        self.is_success = 200 <= status_code < 300

    def json(self) -> Any:
        return self._body

    def raise_for_status(self) -> None:
        if not self.is_success:
            request = httpx.Request("POST", "https://provider.invalid")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("simulated", request=request, response=response)


class SequencedClient:
    responses: list[FakeResponse] = []

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> "SequencedClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        return self.responses.pop(0)


class TimeoutClient(SequencedClient):
    def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        raise httpx.ReadTimeout("simulated provider timeout")


def llm_failure_case(config: Any, llm_module: Any) -> dict[str, Any]:
    retry_trace: list[dict[str, Any]] = []
    SequencedClient.responses = [
        FakeResponse(400, {"error": "unsupported reasoning_effort"}),
        FakeResponse(
            200,
            {
                "choices": [
                    {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4},
            },
        ),
    ]
    with patch.object(config, "llm_enabled", return_value=True), patch.object(
        llm_module.httpx, "Client", SequencedClient
    ):
        retry_client = llm_module.LLMClient(
            trace_callback=lambda module, prompt, response: retry_trace.append(
                {"module": module, "prompt": prompt, "response": response}
            )
        )
        retry_result = retry_client.complete_json("system", "{}", module="MatchScorer")

    malformed_trace: list[dict[str, Any]] = []
    SequencedClient.responses = [
        FakeResponse(
            200,
            {
                "choices": [
                    {"message": {"content": '{"broken":'}, "finish_reason": "stop"}
                ]
            },
        )
    ]
    malformed_error = None
    with patch.object(config, "llm_enabled", return_value=True), patch.object(
        llm_module.httpx, "Client", SequencedClient
    ):
        malformed_client = llm_module.LLMClient(
            trace_callback=lambda module, prompt, response: malformed_trace.append(
                {"module": module, "prompt": prompt, "response": response}
            )
        )
        try:
            malformed_client.complete_json("system", "{}", module="FilmAnalyzer")
        except llm_module.LLMError as exc:
            malformed_error = str(exc)

    timeout_trace: list[dict[str, Any]] = []
    timeout_error = None
    with patch.object(config, "llm_enabled", return_value=True), patch.object(
        llm_module.httpx, "Client", TimeoutClient
    ):
        timeout_client = llm_module.LLMClient(
            trace_callback=lambda module, prompt, response: timeout_trace.append(
                {"module": module, "prompt": prompt, "response": response}
            )
        )
        try:
            timeout_client.complete_json("system", "{}", module="RoadmapBuilder")
        except llm_module.LLMError as exc:
            timeout_error = str(exc)

    retry_ok = (
        retry_result == {"ok": True}
        and retry_client.usage["attempts"] == 2
        and [step["prompt"]["provider"]["attempt"] for step in retry_trace] == [1, 2]
        and "error" in retry_trace[0]["response"]
        and retry_trace[1]["response"] == {"ok": True}
    )
    malformed_ok = (
        bool(malformed_error)
        and len(malformed_trace) == 1
        and "raw_response" in malformed_trace[0]["response"]
    )
    timeout_ok = (
        bool(timeout_error)
        and len(timeout_trace) == 1
        and timeout_trace[0]["prompt"]["provider"]["attempt"] == 1
        and "Could not reach" in timeout_trace[0]["response"]["error"]
    )
    ok = retry_ok and malformed_ok and timeout_ok
    return scenario(
        "LLM retry, malformed JSON and timeout tracing",
        "PASS" if ok else "FAIL",
        (
            "Every mocked LLM attempt is traced in order, including the rejected-parameter retry, malformed successful response and transport timeout."
            if ok
            else "At least one LLM retry/error attempt was missing, out of order, or not surfaced as a readable error."
        ),
        test_input={
            "retry": "HTTP 400 then success",
            "malformed": "HTTP 200 with malformed JSON content",
            "timeout": "mocked httpx.ReadTimeout",
        },
        expected=[
            "every attempt has prompt and response",
            "attempt order is stable",
            "malformed JSON is not accepted",
            "timeout is human-readable",
        ],
        observed={
            "retry_trace": retry_trace,
            "malformed_trace": malformed_trace,
            "malformed_error": malformed_error,
            "timeout_trace": timeout_trace,
            "timeout_error": timeout_error,
        },
        baseline_blocker=not ok,
    )


def api_contract_case(api_app: Any, graph: Any) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    client = TestClient(api_app)
    provider_error_step = {
        "module": "FilmAnalyzer",
        "prompt": {
            "system": "system",
            "user": "{}",
            "provider": {"kind": "chat", "attempt": 1},
        },
        "response": {"error": "simulated provider timeout"},
    }
    success_payload = {
        "response": "# Strategy",
        "steps": [
            {"module": "Planner", "prompt": {}, "response": {}},
            provider_error_step,
        ],
        "meta": {"film_title": "Fixture"},
    }
    with patch.object(graph, "run", return_value=success_payload):
        success = client.post("/api/execute", json={"prompt": "fixture"}).json()
    with patch.object(
        graph,
        "run",
        side_effect=graph.AgentRunError("LLMError: simulated provider timeout", [provider_error_step]),
    ):
        runtime_error = client.post("/api/execute", json={"prompt": "fixture"}).json()
    empty = client.post("/api/execute", json={"prompt": " "}).json()
    malformed = client.post(
        "/api/execute", content="{bad", headers={"Content-Type": "application/json"}
    ).json()
    cases = {"success": success, "runtime_error": runtime_error, "empty": empty, "malformed": malformed}
    exact = all(set(body) == {"status", "error", "response", "steps"} for body in cases.values())
    runtime_ok = (
        runtime_error["status"] == "error"
        and runtime_error["response"] is None
        and runtime_error["steps"] == [provider_error_step]
        and "Traceback" not in runtime_error["error"]
    )
    validation_ok = all(
        body["status"] == "error" and body["steps"] == []
        for body in (empty, malformed)
    )
    success_ok = success["status"] == "ok" and success["error"] is None
    ok = exact and runtime_ok and validation_ok and success_ok
    return scenario(
        "API success, validation-error and runtime-error contract",
        "PASS" if ok else "FAIL",
        (
            "All exercised API paths return exactly status/error/response/steps; runtime errors retain partial trace without a stack trace."
            if ok
            else "At least one API path violated the exact four-field contract or lost/leaked error trace data."
        ),
        test_input=["success", "empty prompt", "malformed JSON", "runtime provider failure"],
        expected=[
            "exact four top-level fields",
            "validation errors have empty steps",
            "runtime errors preserve partial steps",
            "no stack trace leaks",
        ],
        observed=cases,
        baseline_blocker=not ok,
    )


def cross_cutting_examples_case(
    root: Path,
    prompts: Any,
    scoring: Any,
) -> dict[str, Any]:
    examples = json.loads((root / "data" / "prompt_examples.json").read_text(encoding="utf-8"))
    defects: list[dict[str, Any]] = []
    canonical = {"Planner", "Executor", *prompts.TASK_CATALOG, "Replanner"}
    for example_index, example in enumerate(examples):
        steps = example.get("steps") or []
        modules_seen = {step.get("module") for step in steps}
        if modules_seen != canonical:
            defects.append(
                {"example": example_index, "type": "module_mismatch", "actual": sorted(modules_seen)}
            )
        search = next(
            step for step in steps
            if step.get("module") == "FestivalSearch"
            and isinstance(step.get("response"), dict)
            and isinstance(step["response"].get("festivals"), list)
        )
        candidate_ids = [row["id"] for row in search["response"]["festivals"]]
        if len(candidate_ids) != len(set(candidate_ids)):
            defects.append({"example": example_index, "type": "duplicate_candidate_ids"})
        executor = next(step for step in steps if step.get("module") == "Executor")
        scorer_outcome = next(
            row["outcome"]
            for row in executor["response"]["executed"]
            if row["module"] == "MatchScorer"
        )
        expected_outcome = (
            f"{len(candidate_ids)} creative-fit ratings combined with deterministic scores"
        )
        if scorer_outcome != expected_outcome:
            defects.append(
                {
                    "example": example_index,
                    "type": "executor_count_mismatch",
                    "expected": expected_outcome,
                    "actual": scorer_outcome,
                }
            )
        deterministic = next(
            step for step in steps
            if step.get("module") == "MatchScorer"
            and step.get("prompt", {}).get("operation") == "deterministic_weighted_score"
        )
        rankings = deterministic["response"]["ranking"]
        if {row["id"] for row in rankings} != set(candidate_ids):
            defects.append({"example": example_index, "type": "ranking_candidate_mismatch"})
        for row in rankings:
            if row["score"] != max(0, min(100, round(row["base_score"] - row["premiere_penalty"]))):
                defects.append(
                    {"example": example_index, "type": "score_arithmetic", "festival": row["id"]}
                )
        chat_attempts = [
            step for step in steps
            if step.get("prompt", {}).get("provider", {}).get("kind") == "chat"
        ]
        embedding_attempts = [
            step for step in steps
            if step.get("prompt", {}).get("provider", {}).get("kind") == "embedding"
        ]
        if not all(step.get("prompt") is not None and step.get("response") is not None for step in chat_attempts + embedding_attempts):
            defects.append({"example": example_index, "type": "provider_attempt_missing_payload"})
        roadmap = next(
            step for step in steps
            if step.get("module") == "RoadmapBuilder"
            and step.get("prompt", {}).get("provider", {}).get("kind") == "chat"
        )
        payload = json.loads(roadmap["prompt"]["user"])
        if len(payload["festivals"]) != len(candidate_ids):
            defects.append({"example": example_index, "type": "roadmap_candidate_count"})
        for festival in payload["festivals"]:
            expected_score = scoring.compute_score(
                festival["evidence"] and {
                    # Score arithmetic is asserted from the deterministic ranking above;
                    # here only ensure the final payload includes every score-bearing record.
                    "deadline_urgency": 0
                },
                festival["premiere_risk"],
            )
            if "score" not in festival or expected_score is None:
                defects.append({"example": example_index, "type": "roadmap_score_missing"})

    ok = not defects
    return scenario(
        "Cross-cutting invariants on prompt examples",
        "PASS" if ok else "FAIL",
        (
            "Prompt examples preserve canonical modules, trace payloads, candidate uniqueness, score arithmetic and Executor counts."
            if ok
            else "At least one committed human-facing example violates a cross-cutting trace or strategy invariant."
        ),
        test_input=f"{len(examples)} committed prompt examples",
        expected=[
            "canonical module names and ordering",
            "every provider attempt has prompt and response",
            "no duplicate candidate IDs",
            "score arithmetic is consistent",
            "Executor count equals scored candidates",
        ],
        observed={"example_count": len(examples), "defects": defects},
        diagnosis={
            "root_layer": "prompt_examples trace/presentation" if defects else "none",
        },
        baseline_blocker=not ok,
    )


def run_offline(root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(root))
    from app import config
    from api.index import app as api_app
    from app import llm as llm_module
    from app.agent import domain, graph, modules, prompts, scoring
    from app.stores import corpus, pinecone_store, supabase_store

    festivals = corpus.load_festivals()
    results = [
        unknown_premiere_case(modules),
        already_premiered_case(modules, domain, festivals),
        contradictory_case(modules),
        hard_mismatch_case(domain),
        youth_over_inference_case(modules, corpus, scoring),
        retrieval_case(
            modules,
            corpus,
            festivals,
            name="Genre specialist retrieval: Sitges",
            query=(
                "Spanish 108-minute psychological thriller horror drama with a strong genre "
                "audience and world premiere available; fantastic cinema suspense"
            ),
            expected_ids=set(),
            expected_name_fragment="Sitges",
            later_100=True,
        ),
        retrieval_case(
            modules,
            corpus,
            festivals,
            name="Religion documentary retrieval",
            query=(
                "feature documentary about faith religion spirituality interreligious dialogue "
                "community ethics peace and religious identity"
            ),
            expected_ids={"religion-today-film-festival"},
        ),
        retrieval_case(
            modules,
            corpus,
            festivals,
            name="Authored documentary retrieval",
            query=(
                "strongly authored creative documentary essay film formal observational nonfiction "
                "directorial voice experimental archive"
            ),
            expected_ids={"idfa", "play-doc", "doclisboa", "yamagata-idf", "ficunam"},
            minimum_expected=2,
        ),
        retrieval_case(
            modules,
            corpus,
            festivals,
            name="Children's animation retrieval",
            query=(
                "animated feature made for children and families with a genuine youth audience, "
                "youth jury, accessible storytelling and coming of age"
            ),
            expected_ids={
                "giffoni-film-festival-gff",
                "zlin-iff",
                "vienna-international-children-s-ff",
                "kineko-international-children-s-film-festival",
            },
            minimum_expected=2,
        ),
        preference_case(modules, corpus, domain, scoring),
        no_good_candidates_case(modules),
        deadlines_case(domain),
        company_memory_case(scoring),
        fallback_case(modules, corpus, pinecone_store, supabase_store, config),
        llm_failure_case(config, llm_module),
        api_contract_case(api_app, graph),
        cross_cutting_examples_case(root, prompts, scoring),
    ]
    return {
        "mode": "offline",
        "corpus_size": len(festivals),
        "results": results,
        "cost": {
            "real_api_execute_runs": 0,
            "real_chat_calls": 0,
            "real_chat_attempts": 0,
            "real_embedding_calls": 0,
            "offline_or_mocked_scenarios": len(results),
        },
    }


def run_live(root: Path) -> dict[str, Any]:
    sys.path.insert(0, str(root))
    from fastapi.testclient import TestClient

    from api.index import app

    body = TestClient(app).post("/api/execute", json={"prompt": UNKNOWN_INPUT}).json()
    provider_steps = [
        step
        for step in body.get("steps", [])
        if isinstance(step.get("prompt"), dict) and step["prompt"].get("provider")
    ]
    chat_steps = [
        step for step in provider_steps if step["prompt"]["provider"].get("kind") == "chat"
    ]
    embedding_steps = [
        step
        for step in provider_steps
        if step["prompt"]["provider"].get("kind") == "embedding"
    ]
    connection_markers = (
        "could not reach",
        "name or service not known",
        "temporary failure",
        "timed out",
        "timeout",
        "connection",
    )
    error_text = str(body.get("error") or "").lower()
    if body.get("status") != "ok":
        status = "NOT RUN" if any(marker in error_text for marker in connection_markers) else "FAIL"
        reason = (
            "The external provider was inaccessible; the scenario is not counted as a behavioral pass."
            if status == "NOT RUN"
            else "The live API execution reached the system but failed for a non-connectivity reason."
        )
        result = scenario(
            "Live exact-input /api/execute probe",
            status,
            reason,
            test_input=UNKNOWN_INPUT,
            expected=[
                "successful exact API contract",
                "unknown premiere remains unknown",
                "no manufactured premiere target",
                "all provider attempts appear in steps",
            ],
            observed={
                "api_status": body.get("status"),
                "error": body.get("error"),
                "response": body.get("response"),
                "steps": body.get("steps"),
            },
            diagnosis={"external_access": "unavailable" if status == "NOT RUN" else "available"},
            baseline_blocker=status == "FAIL",
        )
    else:
        analyzer_steps = [step for step in chat_steps if step.get("module") == "FilmAnalyzer"]
        profile = analyzer_steps[-1].get("response", {}) if analyzer_steps else {}
        response_text = str(body.get("response") or "")
        unknown = profile.get("premiere_status") == "unknown"
        no_target = "## Premiere Strategy" not in response_text
        surfaced = "premiere" in response_text.lower() and (
            "unknown" in response_text.lower()
            or "confirm" in response_text.lower()
            or "no premiere target" in response_text.lower()
        )
        trace_complete = all(
            step.get("prompt") is not None and step.get("response") is not None
            for step in provider_steps
        )
        ok = unknown and no_target and surfaced and trace_complete
        result = scenario(
            "Live exact-input /api/execute probe",
            "PASS" if ok else "FAIL",
            (
                "The live exact input preserved unknown premiere state and complete provider tracing."
                if ok
                else "The live exact input manufactured premiere certainty, omitted the missing fact, or lost a provider attempt."
            ),
            test_input=UNKNOWN_INPUT,
            expected=[
                "premiere_status unknown",
                "no confirmed premiere target",
                "preliminary strategy surfaces the missing fact",
                "all provider attempts have prompt/response in order",
            ],
            observed={
                "profile": profile,
                "response": response_text,
                "provider_steps": provider_steps,
            },
            diagnosis={
                "film_extraction": "PASS" if unknown else "FAIL",
                "premiere_logic": "PASS" if no_target else "FAIL",
                "roadmap_presentation": "PASS" if surfaced else "FAIL",
                "trace": "PASS" if trace_complete else "FAIL",
            },
            baseline_blocker=not ok,
        )

    return {
        "mode": "live",
        "results": [result],
        "cost": {
            "real_api_execute_runs": 1,
            "real_chat_calls": sum(
                1
                for step in chat_steps
                if not isinstance(step.get("response"), dict) or "error" not in step["response"]
            ),
            "real_chat_attempts": len(chat_steps),
            "real_embedding_calls": sum(
                1
                for step in embedding_steps
                if step.get("response", {}).get("status") == "ok"
            ),
            "real_embedding_attempts": len(embedding_steps),
            "offline_or_mocked_scenarios": 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    args = parser.parse_args()

    root = args.project_root.resolve()
    result = run_live(root) if args.mode == "live" else run_offline(root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
