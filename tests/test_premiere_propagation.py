"""Regression coverage for premiere state across the production module chain."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any
from unittest.mock import patch

from app.agent import graph, modules


FESTIVAL = {
    "id": "fixture-world",
    "name": "Fixture World Festival",
    "country": "France",
    "city": "Paris",
    "region": "Western Europe",
    "tier": "A",
    "month": "October",
    "typical_deadline_month": "June",
    "accepts": ["feature_doc"],
    "premiere_requirement": "world",
    "premiere_requirement_raw": "World",
    "submission_open": "2027-01-01",
    "final_deadline": "2027-06-01",
    "identity_confidence": "high",
    "focus": "International documentary competition.",
    "themes": ["documentary"],
    "strategic_value": "International documentary launch.",
}

CASE_ONE_BRIEF = (
    "The film has had no public, festival, press, market, educational, broadcast, "
    "streaming, or online screenings anywhere. It has only been viewed privately by "
    "the production team. Its world, international, and territorial premieres are available."
)

CASE_TWO_BRIEF = (
    "The film had a ticketed public screening in São Paulo on 12 May 2026 and an "
    "unrestricted worldwide YouTube publication on 10 June 2026. It has not screened "
    "at any film festival. The world premiere has already been consumed."
)


class PipelineLLM:
    def __init__(self, analyzer_result: dict[str, Any], trace: modules.Trace) -> None:
        self.analyzer_result = analyzer_result
        self.trace = trace
        self.payloads: dict[str, dict[str, Any]] = {}
        self.normalized_profile: dict[str, Any] = {}
        self.usage = {
            "calls": 0,
            "attempts": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
        }

    def complete_json(
        self, system: str, user: str, **kwargs: Any
    ) -> dict[str, Any]:
        module = kwargs["module"]
        payload = json.loads(user)
        self.payloads[module] = payload

        if module == "FilmAnalyzer":
            result = deepcopy(self.analyzer_result)
        elif module == "MatchScorer":
            result = {
                "scores": [
                    {
                        "id": candidate["id"],
                        "ratings": {
                            "thematic_fit": 4,
                            "genre_fit": 4,
                            "lineup_similarity": 3,
                            "strategic_value": 4,
                        },
                        "evidence": {
                            "thematic_fit": "Documentary focus matches.",
                            "genre_fit": "The festival accepts feature documentaries.",
                            "lineup_similarity": "The supplied identity supports a moderate match.",
                            "strategic_value": "The festival offers an international launch.",
                        },
                    }
                    for candidate in payload["candidates"]
                ]
            }
        elif module == "RoadmapBuilder":
            buckets = {
                "submit_first": [],
                "prioritize_next": [],
                "leverage": [],
                "hold_avoid": [],
            }
            for festival in payload["festivals"]:
                buckets[festival["bucket"]].append(
                    {
                        "id": festival["id"],
                        "evidence_dimensions": ["strategic_value"],
                    }
                )
            result = {
                "premiere_target": payload.get("recommended_premiere_target"),
                "buckets": buckets,
                "open_questions": [],
            }
        else:  # pragma: no cover - the deterministic plan has only three LLM calls
            raise AssertionError(f"Unexpected LLM module: {module}")

        self.usage["calls"] += 1
        self.usage["attempts"] += 1
        self.trace.add(
            module,
            {"system": system, "user": user, "provider": {"attempt": 1}},
            deepcopy(result),
        )
        return deepcopy(result)


def _base_analyzer_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "title": "Propagation Test",
        "logline": "A documentary used to test premiere-state propagation.",
        "format": "feature_doc",
        "genres": ["documentary"],
        "themes": ["documentary"],
        "country": "Brazil",
        "language": "Portuguese",
        "runtime_minutes": 80,
        "director_profile": None,
        "premiere_status": "unknown",
        "premiere_history": [],
        "target_audience": "Documentary audiences",
        "festival_angles": [],
        "missing_info": ["premiere status"],
        "search_query": "Brazilian feature documentary",
    }
    result.update(overrides)
    return result


def _run_chain(
    prompt: str, analyzer_result: dict[str, Any]
) -> tuple[dict[str, Any], PipelineLLM, dict[str, Any]]:
    trace = modules.Trace()
    llm = PipelineLLM(analyzer_result, trace)

    def company_memory(local_trace: modules.Trace) -> dict[str, Any]:
        result = {"company": {"name": "Fixture Distributor"}, "history": []}
        local_trace.add("CompanyMemory", {"source": "test"}, result)
        return result

    def festival_search(
        local_trace: modules.Trace,
        profile: dict[str, Any],
        memory: dict[str, Any],
    ) -> list[dict[str, Any]]:
        del memory
        llm.normalized_profile = deepcopy(profile)
        local_trace.add("FestivalSearch", {"source": "test"}, {"count": 1})
        return [deepcopy(FESTIVAL)]

    with (
        patch.object(modules, "company_memory", company_memory),
        patch.object(modules, "festival_search", festival_search),
    ):
        result = graph._run(prompt, llm, trace, time.monotonic())

    risk_step = next(
        step for step in result["steps"] if step["module"] == "RiskChecker"
    )
    risk = risk_step["response"]["risks"][0]
    return result, llm, risk


def _has_premiere_question(values: list[Any]) -> bool:
    return any(
        "premiere" in str(value).casefold() or "screening" in str(value).casefold()
        for value in values
    )


def test_world_premiere_available_survives_the_full_production_chain() -> None:
    result, llm, risk = _run_chain(
        CASE_ONE_BRIEF,
        _base_analyzer_result(
            premiere_status="world_premiere_available",
            premiere_history=[],
            missing_info=["Preferred premiere window or target festivals"],
        ),
    )

    analyzer_step = next(
        step
        for step in result["steps"]
        if step["module"] == "FilmAnalyzer"
        and "premiere_status" in step["response"]
    )
    assert analyzer_step["response"]["premiere_status"] == "world_premiere_available"
    assert analyzer_step["response"]["premiere_history"] == []
    assert analyzer_step["response"]["missing_info"] == [
        "Preferred premiere window or target festivals"
    ]
    assert llm.normalized_profile["premiere_status"] == "world_premiere_available"
    assert llm.normalized_profile["premiere_history"] == []
    assert llm.normalized_profile["missing_info"] == [
        "Preferred premiere window or target festivals"
    ]
    assert llm.payloads["MatchScorer"]["film"]["premiere_status"] == (
        "world_premiere_available"
    )
    assert llm.payloads["RoadmapBuilder"]["film"]["premiere_status"] == (
        "world_premiere_available"
    )
    assert "still holds its world premiere" in risk["risk_note"]
    ranked = result["meta"]["ranked_festivals"][0]
    assert ranked["premiere_risk"] == "none"
    assert ranked["premiere_penalty"] == 0
    assert llm.payloads["RoadmapBuilder"]["film"]["missing_info"] == [
        "Preferred premiere window or target festivals"
    ]
    open_questions = result["meta"]["roadmap"]["open_questions"]
    assert open_questions == [
        "Provide or confirm the missing film information: Preferred premiere window or "
        "target festivals."
    ]
    assert "confirm every prior public screening" not in result["response"]
    assert "remaining premiere status" not in result["response"]


def test_screening_and_online_release_do_not_collapse_to_generic_unknown() -> None:
    result, llm, risk = _run_chain(
        CASE_TWO_BRIEF,
        _base_analyzer_result(
            premiere_status="already_premiered",
            premiere_history=[
                {
                    "festival": "Ticketed public screening in São Paulo",
                    "country": "Brazil",
                    "date": "2026-05-12",
                    "event_kind": "screening",
                },
                {
                    "festival": "Worldwide public YouTube release",
                    "country": None,
                    "date": "2026-06-10",
                    "event_kind": "online_availability",
                },
            ],
            missing_info=[],
        ),
    )

    analyzer_step = next(
        step
        for step in result["steps"]
        if step["module"] == "FilmAnalyzer"
        and "premiere_status" in step["response"]
    )
    assert analyzer_step["response"]["premiere_status"] == "already_premiered"
    assert len(analyzer_step["response"]["premiere_history"]) == 2
    assert analyzer_step["response"]["missing_info"] == []
    assert llm.normalized_profile["premiere_status"] == "already_premiered"
    assert len(llm.normalized_profile["premiere_history"]) == 2
    assert llm.normalized_profile["missing_info"] == []
    assert "contradictions" not in llm.normalized_profile["_validation"]
    assert llm.payloads["MatchScorer"]["film"]["premiere_status"] == "already_premiered"
    assert llm.payloads["RoadmapBuilder"]["film"]["premiere_status"] == "already_premiered"
    assert "Unrestricted public online availability is recorded" in risk["risk_note"]
    assert "premiere status is unknown" not in risk["risk_note"]
    assert not _has_premiere_question(
        llm.payloads["RoadmapBuilder"]["film"]["missing_info"]
    )
    open_questions = result["meta"]["roadmap"]["open_questions"]
    assert any("premiere rules for Fixture World Festival" in item for item in open_questions)
    assert not any("prior public screening" in item for item in open_questions)
    assert not any("conflicting statements" in item for item in open_questions)
    assert "world premiere is already consumed" in result["response"]
    assert "screening history" not in result["response"]


def test_genuinely_missing_premiere_information_remains_unknown() -> None:
    result, llm, risk = _run_chain(
        "An 80-minute Brazilian feature documentary about coastal communities.",
        _base_analyzer_result(
            premiere_status="world_premiere_available",
            premiere_history=[],
            missing_info=[],
        ),
    )

    assert llm.payloads["MatchScorer"]["film"]["premiere_status"] == "unknown"
    assert llm.payloads["RoadmapBuilder"]["film"]["premiere_status"] == "unknown"
    assert "premiere status is unknown" in risk["risk_note"]
    assert _has_premiere_question(
        llm.payloads["RoadmapBuilder"]["film"]["missing_info"]
    )
    assert any(
        question.startswith("High-impact:")
        for question in result["meta"]["roadmap"]["open_questions"]
    )


def test_contradictory_premiere_information_still_requires_clarification() -> None:
    result, llm, risk = _run_chain(
        "The film has never screened publicly, but premiered publicly last month.",
        _base_analyzer_result(
            premiere_status="already_premiered",
            premiere_history=[
                {
                    "festival": "Prior public premiere",
                    "country": "Brazil",
                    "date": None,
                    "event_kind": "screening",
                }
            ],
            missing_info=[],
        ),
    )

    assert llm.payloads["MatchScorer"]["film"]["premiere_status"] == "unknown"
    assert llm.payloads["RoadmapBuilder"]["film"]["premiere_status"] == "unknown"
    assert "premiere status is unknown" in risk["risk_note"]
    assert _has_premiere_question(
        llm.payloads["RoadmapBuilder"]["film"]["missing_info"]
    )
    assert any(
        question.startswith("High-impact:")
        for question in result["meta"]["roadmap"]["open_questions"]
    )
