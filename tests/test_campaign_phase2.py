"""Integration tests for typed adaptation, reuse, replanning, and scenarios."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from app.campaign.adapter import (
    LegacyEvidenceAdapter,
    LegacyEvidenceBundle,
    LegacyEvidenceError,
)
from app.campaign.contracts import parse_campaign_command
from app.campaign.models import (
    ArtifactName,
    CampaignReadiness,
    InvalidationClass,
    PlanningInput,
    PremiereAvailability,
)
from app.campaign.orchestration import CampaignOrchestrator, snapshot_from_evidence
from app.campaign.rendering import CampaignRenderer
from app.campaign.repository import InMemoryCampaignRepository
from app.campaign.scenarios import CampaignScenarioEngine, ScenarioError
from app.campaign.state import CampaignStateReducer


AS_OF = date(2026, 8, 25)
OBSERVED = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)
SCREENED = datetime(2026, 8, 25, 19, 0, tzinfo=timezone.utc)


def _raw_candidate(
    festival_id: str,
    name: str,
    score: int,
    *,
    premiere_scope: str,
    premiere_rule: str,
    country: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    retrieval = {
        "id": festival_id,
        "name": name,
        "country": country,
        "region": "North America" if country == "Canada" else "Europe",
        "tier": "A",
        "accepts": ["feature_doc"],
        "premiere_requirement": premiere_scope,
        "premiere_requirement_raw": premiere_rule,
        "premiere_territory": None,
        "submission_open": "2026-01-01",
        "next_deadline": "2026-10-01",
        "final_deadline": "2026-10-01",
        "typical_deadline_month": "October",
        "submission_fee": "$50",
        "identity_confidence": "high",
        "semantic_score": 0.9,
        "lexical_score": 0.7,
        "retrieval_sources": ["semantic", "lexical"],
        "retrieval_backend": "fixture-vector",
    }
    rating = Decimal(score) / Decimal(20)
    creative = {
        "id": festival_id,
        "ratings": {
            "thematic_fit": str(rating),
            "genre_fit": str(rating),
            "lineup_similarity": str(rating),
            "strategic_value": str(rating),
        },
        "evidence": {
            "thematic_fit": "fixture thematic evidence",
            "genre_fit": "fixture genre evidence",
            "lineup_similarity": "fixture lineup evidence",
            "strategic_value": "fixture strategic evidence",
        },
    }
    risk = {
        "id": festival_id,
        "premiere_risk": "none",
        "premiere_opportunity": premiere_scope != "none",
        "eligible": True,
        "deadline_status": "open",
        "deadline": {
            "status": "open",
            "urgency": float(rating),
            "reason": "Fixture deadline is open.",
            "next_deadline": "2026-10-01",
            "confidence": "high",
            "days_until_open": 0,
        },
        "premiere_constraint": {
            "scope": premiere_scope,
            "territory": None,
            "confidence": "high",
            "reason": "Fixture premiere rule.",
        },
        "runtime_constraint": None,
        "runtime_warning": None,
        "eligibility_issue": None,
        "uncertainties": [],
    }
    weights = {
        "thematic_fit": 25,
        "genre_fit": 15,
        "lineup_similarity": 20,
        "company_relationship": 15,
        "strategic_value": 15,
        "deadline_urgency": 10,
    }
    breakdown = {
        key: {
            "rating": float(rating),
            "weight": weight,
            "points": str((rating / Decimal(5) * Decimal(weight)).quantize(Decimal("0.1"))),
        }
        for key, weight in weights.items()
    }
    ranked = {
        "id": festival_id,
        "name": name,
        "tier": "A",
        "score": score,
        "base_score": str(score),
        "premiere_penalty": 0,
        "premiere_risk": "none",
        "premiere_opportunity": premiere_scope != "none",
        "deadline_status": "open",
        "eligible": True,
        "deadline": risk["deadline"],
        "ratings": {
            "thematic_fit": str(rating),
            "genre_fit": str(rating),
            "lineup_similarity": str(rating),
            "company_relationship": str(rating),
            "strategic_value": str(rating),
            "deadline_urgency": str(rating),
        },
        "breakdown": breakdown,
        "bucket": "submit_first",
        "validation": {"adjustments": []},
    }
    return retrieval, creative, risk, ranked


def _raw_bundle() -> LegacyEvidenceBundle:
    hot = _raw_candidate(
        "hot-docs",
        "Hot Docs",
        90,
        premiere_scope="world",
        premiere_rule="World",
        country="Canada",
    )
    idfa = _raw_candidate(
        "idfa",
        "IDFA",
        82,
        premiere_scope="none",
        premiere_rule="No requirement",
        country="Netherlands",
    )
    return LegacyEvidenceBundle(
        profile={
            "title": "Borrowed Ground",
            "logline": "An environmental documentary about women and displacement.",
            "format": "feature_doc",
            "country": "Israel",
            "themes": ["environment", "women", "displacement"],
            "runtime_minutes": 92,
            "premiere_status": "world_premiere_available",
            "premiere_history": [],
            "search_query": "environmental women displacement documentary",
            "_validation": {"valid": True, "adjustments": []},
        },
        retrieval_candidates=(hot[0], idfa[0]),
        creative_scores={"hot-docs": hot[1], "idfa": idfa[1]},
        risks={"hot-docs": hot[2], "idfa": idfa[2]},
        ranked_candidates=(hot[3], idfa[3]),
        company_memory={"company": {"name": "Meridian Films"}, "history": []},
        trace=({"module": "FixtureLegacyPipeline", "provider_call": False},),
        chat_attempts=2,
        embedding_attempts=1,
    )


def _adapted():
    return LegacyEvidenceAdapter().adapt(
        _raw_bundle(), as_of_date=AS_OF, observed_at=OBSERVED
    )


def _repository_and_orchestrator():
    evidence = _adapted()
    snapshot = snapshot_from_evidence(
        workspace_id="workspace-phase2",
        campaign_id="campaign-phase2",
        evidence=evidence,
    )
    repository = InMemoryCampaignRepository(clock=lambda: SCREENED)
    repository.register_workspace("workspace-phase2", "a" * 64)
    repository.save_campaign("workspace-phase2", snapshot)
    orchestrator = CampaignOrchestrator(repository)
    initial = orchestrator.plan_initial(
        "workspace-phase2",
        "campaign-phase2",
        as_of_date=AS_OF,
        evidence=evidence,
    )
    assert initial.status == "ready"
    assert initial.plan is not None
    return repository, orchestrator, initial


def _command(
    command_type: str,
    payload: dict[str, Any],
    *,
    version: int,
    key: str,
    invalidation: str | None = None,
):
    if invalidation is None:
        invalidation = (
            "B"
            if command_type in {"confirm_screening", "verify_opportunity_fact"}
            else "A"
            if command_type == "update_profile_fact"
            else "C"
        )
    return parse_campaign_command(
        {
            "type": command_type,
            "payload": payload,
            "expected_version": version,
            "idempotency_key": key,
            "actor": {"kind": "human", "actor_ref": "human:phase2"},
            "invalidation_class": invalidation,
        },
        {"hot-docs", "idfa"},
    )


def test_legacy_adapter_builds_complete_typed_boundary_and_rejects_id_drift() -> None:
    adapted = _adapted()
    assert adapted.profile.profile_hash
    assert adapted.retrieval_input.profile_hash == adapted.profile.profile_hash
    assert [item.festival_id for item in adapted.candidates] == ["hot-docs", "idfa"]
    assert all(item.component_hash for item in adapted.candidates)
    assert all(
        item.future_quality
        == item.model_validate(item.model_dump()).future_quality
        for item in adapted.candidates
    )
    assert adapted.candidates[0].retrieved.fee_fact.amount == 50
    assert adapted.chat_attempts == 2
    assert adapted.embedding_attempts == 1

    bundle = _raw_bundle()
    bad = LegacyEvidenceBundle(
        profile=bundle.profile,
        retrieval_candidates=bundle.retrieval_candidates,
        creative_scores={"hot-docs": bundle.creative_scores["hot-docs"]},
        risks=bundle.risks,
        ranked_candidates=bundle.ranked_candidates,
        company_memory=bundle.company_memory,
    )
    with pytest.raises(LegacyEvidenceError, match="exact festivals.id"):
        LegacyEvidenceAdapter().adapt(bad, as_of_date=AS_OF, observed_at=OBSERVED)


@pytest.mark.parametrize(
    ("profile_updates", "expected_world", "expected_international"),
    [
        (
            {
                "premiere_status": "world_premiere_available",
                "premiere_history": [],
            },
            PremiereAvailability.AVAILABLE,
            PremiereAvailability.AVAILABLE,
        ),
        (
            {
                "premiere_status": "international_premiere_available",
                "premiere_history": [
                    {
                        "festival": "User-reported public screening",
                        "country": "Israel",
                        "date": "2026-01-10",
                        "home_country": True,
                    }
                ],
                "_film_history_evidence": {
                    "single_home_country_screening": True
                },
            },
            PremiereAvailability.CONSUMED,
            PremiereAvailability.AVAILABLE,
        ),
        (
            {
                "premiere_status": "already_premiered",
                "premiere_history": [
                    {
                        "festival": "Public online availability",
                        "country": None,
                        "date": None,
                        "event_kind": "online_availability",
                    }
                ],
            },
            PremiereAvailability.CONSUMED,
            PremiereAvailability.UNKNOWN,
        ),
        (
            {"premiere_status": "unknown", "premiere_history": []},
            PremiereAvailability.UNKNOWN,
            PremiereAvailability.UNKNOWN,
        ),
        (
            {
                "premiere_status": "unknown",
                "premiere_history": [],
                "_validation": {
                    "valid": False,
                    "adjustments": [],
                    "contradictions": [{"field": "premiere_status"}],
                },
            },
            PremiereAvailability.UNKNOWN,
            PremiereAvailability.UNKNOWN,
        ),
    ],
)
def test_adapter_preserves_film_history_state_separately_from_festival_rules(
    profile_updates, expected_world, expected_international
) -> None:
    bundle = _raw_bundle()
    profile = {**bundle.profile, **profile_updates}
    evidence = LegacyEvidenceAdapter().adapt(
        LegacyEvidenceBundle(
            profile=profile,
            retrieval_candidates=bundle.retrieval_candidates,
            creative_scores=bundle.creative_scores,
            risks=bundle.risks,
            ranked_candidates=bundle.ranked_candidates,
            company_memory=bundle.company_memory,
        ),
        as_of_date=AS_OF,
        observed_at=OBSERVED,
    )
    snapshot = snapshot_from_evidence(
        workspace_id="workspace-history",
        campaign_id="campaign-history",
        evidence=evidence,
    )
    scopes = {
        (item.scope.value, item.territory): item.availability
        for item in snapshot.premiere_ledger.scopes
    }

    assert scopes[("world", None)] == expected_world
    assert scopes[("international", None)] == expected_international


def test_online_availability_remains_a_verification_gate_after_campaign_risk_refresh() -> None:
    bundle = _raw_bundle()
    evidence = LegacyEvidenceAdapter().adapt(
        LegacyEvidenceBundle(
            profile={
                **bundle.profile,
                "premiere_status": "already_premiered",
                "premiere_history": [
                    {
                        "festival": "Public online availability",
                        "country": None,
                        "date": None,
                        "event_kind": "online_availability",
                    }
                ],
            },
            retrieval_candidates=bundle.retrieval_candidates,
            creative_scores=bundle.creative_scores,
            risks=bundle.risks,
            ranked_candidates=bundle.ranked_candidates,
            company_memory=bundle.company_memory,
        ),
        as_of_date=AS_OF,
        observed_at=OBSERVED,
    )
    snapshot = snapshot_from_evidence(
        workspace_id="workspace-online",
        campaign_id="campaign-online",
        evidence=evidence,
    )
    refreshed = LegacyEvidenceAdapter().refresh_risk(
        snapshot.candidates,
        snapshot.profile,
        snapshot.premiere_ledger,
        snapshot.screenings,
        as_of_date=AS_OF,
    )
    hot_docs = next(item for item in refreshed if item.festival_id == "hot-docs")

    assert hot_docs.risk.eligible is True
    assert hot_docs.risk.premiere_risk == "medium"
    assert any(
        item.fact_key == "premiere.rule" for item in hot_docs.risk.uncertainties
    )


def test_initial_orchestration_persists_only_typed_planning_input() -> None:
    repository, _orchestrator, initial = _repository_and_orchestrator()
    assert isinstance(initial.planning_input, PlanningInput)
    assert initial.plan.primary_launch.festival_id == "hot-docs"
    assert initial.aggregate.snapshot.readiness == CampaignReadiness.READY
    stored = repository.load_campaign("workspace-phase2", "campaign-phase2")
    attempt = stored.strategy_versions[-1].attempt
    assert set(attempt.input_snapshot_json) >= {
        "profile",
        "candidates",
        "compatibility_edges",
        "artifact_keys",
        "planning_input_hash",
    }
    assert "ranked_festivals" not in attempt.input_snapshot_json
    assert attempt.plan == initial.plan


def test_hot_docs_rejection_replans_with_zero_providers_and_explicit_reuse() -> None:
    _repository, orchestrator, initial = _repository_and_orchestrator()
    submitted = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "mark_submitted",
            {"festival_id": "hot-docs", "source_refs": ["human:submission"]},
            version=0,
            key="phase2-submit-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    assert submitted.status == "ready"
    assert submitted.plan.primary_launch.festival_id == "hot-docs"

    rejected = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "record_rejection",
            {
                "festival_id": "hot-docs",
                "source_refs": ["human:rejection-email"],
            },
            version=1,
            key="phase2-reject-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    assert rejected.status == "ready"
    assert rejected.strategy_ref != submitted.strategy_ref
    assert rejected.plan.primary_launch.festival_id == "idfa"
    assert rejected.diff.primary_before == "hot-docs"
    assert rejected.diff.primary_after == "idfa"
    assert rejected.diff.diff_hash
    manifest = rejected.reuse_manifest
    assert manifest.invalidation_class == InvalidationClass.C
    assert manifest.chat_attempts == 0
    assert manifest.embedding_attempts == 0
    assert manifest.reused_artifacts == (
        ArtifactName.RETRIEVAL,
        ArtifactName.CREATIVE_EVIDENCE,
        ArtifactName.RISK,
        ArtifactName.PREMIERE_LEDGER,
        ArtifactName.COMPATIBILITY_GRAPH,
    )
    assert manifest.rerun_artifacts == (
        ArtifactName.PLANNER,
        ArtifactName.CLARIFICATION,
    )
    assert (
        manifest.prior_artifact_keys.retrieval_key
        == manifest.current_artifact_keys.retrieval_key
    )
    assert (
        manifest.prior_artifact_keys.plan_key
        != manifest.current_artifact_keys.plan_key
    )
    usage = rejected.aggregate.strategy_versions[-1].attempt.usage_json
    assert usage == {"chat_attempts": 0, "embedding_attempts": 0}
    replay = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "record_rejection",
            {
                "festival_id": "hot-docs",
                "source_refs": ["human:rejection-email"],
            },
            version=1,
            key="phase2-reject-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    assert replay.strategy_ref == rejected.strategy_ref
    assert len(replay.aggregate.strategy_versions) == len(
        rejected.aggregate.strategy_versions
    )


def test_public_screening_is_b_replan_and_updates_cached_risk_projection() -> None:
    repository, orchestrator, initial = _repository_and_orchestrator()
    scheduled = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "schedule_screening",
            {
                "screening_id": "screening-hot-docs",
                "festival_id": "hot-docs",
                "country": "Canada",
                "region": "North America",
                "scheduled_at": "2026-08-25T19:00:00Z",
                "access": "unknown",
                "source_refs": ["human:schedule"],
            },
            version=0,
            key="phase2-schedule-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    assert scheduled.status == "ready"
    confirmed = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "confirm_screening",
            {
                "screening_id": "screening-hot-docs",
                "occurred_at": "2026-08-25T19:00:00Z",
                "access": "public",
                "country": "Canada",
                "region": "North America",
                "source_refs": ["human:screening-proof"],
            },
            version=1,
            key="phase2-confirm-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    assert confirmed.status == "ready"
    assert confirmed.plan.primary_launch.festival_id == "idfa"
    manifest = confirmed.reuse_manifest
    assert manifest.invalidation_class == InvalidationClass.B
    assert manifest.chat_attempts == manifest.embedding_attempts == 0
    assert ArtifactName.CREATIVE_EVIDENCE in manifest.reused_artifacts
    assert ArtifactName.RISK in manifest.rerun_artifacts
    assert (
        manifest.prior_artifact_keys.creative_key
        == manifest.current_artifact_keys.creative_key
    )
    assert (
        manifest.prior_artifact_keys.risk_key
        != manifest.current_artifact_keys.risk_key
    )
    world = next(
        item
        for item in confirmed.aggregate.snapshot.premiere_ledger.scopes
        if item.scope.value == "world"
    )
    assert world.availability == PremiereAvailability.CONSUMED
    persisted = repository.load_campaign("workspace-phase2", "campaign-phase2")
    hot_docs = next(
        item for item in persisted.snapshot.candidates if item.festival_id == "hot-docs"
    )
    assert not hot_docs.risk.eligible


def test_a_change_without_explicit_refresh_stays_stale_and_never_falls_back() -> None:
    _repository, orchestrator, initial = _repository_and_orchestrator()
    changed = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "update_profile_fact",
            {
                "fact_key": "format",
                "fact": {
                    "value": "short_doc",
                    "status": "asserted",
                    "source_refs": ["human:profile-correction"],
                    "observed_at": "2026-08-25T00:00:00Z",
                },
            },
            version=0,
            key="phase2-change-format",
        ),
        as_of_date=AS_OF,
    )
    assert changed.status == "stale"
    assert changed.plan is None
    assert changed.cache_miss_reasons == (
        "identity_change_requires_provider_refresh",
    )
    assert changed.aggregate.snapshot.active_strategy_ref == initial.strategy_ref
    assert changed.aggregate.strategy_stale
    failed = changed.aggregate.strategy_versions[-1].attempt
    assert failed.outcome == "failed"
    assert failed.error_json["code"] == "cache_miss_requires_refresh"
    assert failed.usage_json == {}


def test_a_change_with_explicit_evidence_refresh_replaces_current_candidate_universe() -> None:
    repository, orchestrator, _initial = _repository_and_orchestrator()
    command = _command(
        "update_profile_fact",
        {
            "fact_key": "format",
            "fact": {
                "value": "short_doc",
                "status": "asserted",
                "source_refs": ["human:profile-correction"],
                "observed_at": "2026-08-25T00:00:00Z",
            },
        },
        version=0,
        key="phase2-refresh-format",
    )
    base = repository.load_campaign("workspace-phase2", "campaign-phase2")
    predicted = CampaignStateReducer().reduce(
        base.snapshot,
        command,
        event_id="prediction:event:1",
        sequence_no=1,
        occurred_at=SCREENED,
    ).snapshot
    bundle = _raw_bundle()
    idfa_retrieval = bundle.retrieval_candidates[1]
    idfa_retrieval = {**idfa_retrieval, "accepts": ["short_doc"]}
    refreshed_bundle = LegacyEvidenceBundle(
        profile={**bundle.profile, "format": "short_doc"},
        retrieval_candidates=(idfa_retrieval,),
        creative_scores={"idfa": bundle.creative_scores["idfa"]},
        risks={"idfa": bundle.risks["idfa"]},
        ranked_candidates=(bundle.ranked_candidates[1],),
        company_memory=bundle.company_memory,
        trace=({"module": "ExplicitProviderRefresh", "provider_call": True},),
        chat_attempts=2,
        embedding_attempts=1,
    )
    refreshed = LegacyEvidenceAdapter().adapt(
        refreshed_bundle,
        as_of_date=AS_OF,
        observed_at=OBSERVED,
        authoritative_profile=predicted.profile,
    )
    result = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        command,
        as_of_date=AS_OF,
        refreshed_evidence=refreshed,
    )
    assert result.status == "ready"
    assert [item.festival_id for item in result.planning_input.candidates] == ["idfa"]
    assert [item.festival_id for item in result.aggregate.snapshot.candidates] == ["idfa"]
    assert {item.festival_id for item in result.aggregate.snapshot.opportunities} == {
        "hot-docs",
        "idfa",
    }
    assert result.reuse_manifest.invalidation_class == InvalidationClass.A
    assert result.reuse_manifest.chat_attempts == 2
    assert result.reuse_manifest.embedding_attempts == 1
    assert ArtifactName.RETRIEVAL in result.reuse_manifest.rerun_artifacts


def test_scenario_equals_real_reducer_planner_result_and_performs_no_write() -> None:
    repository, orchestrator, initial = _repository_and_orchestrator()
    submitted = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "mark_submitted",
            {"festival_id": "hot-docs", "source_refs": ["human:submission"]},
            version=0,
            key="scenario-submit-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    before = repository.load_campaign("workspace-phase2", "campaign-phase2")
    hypothetical = _command(
        "record_rejection",
        {"festival_id": "hot-docs", "source_refs": ["human:rejection"]},
        version=1,
        key="scenario-reject-hot-docs",
    )
    scenario = CampaignScenarioEngine(orchestrator).simulate(
        before.snapshot,
        (hypothetical,),
        prior_input=submitted.planning_input,
        prior_plan=submitted.plan,
        base_strategy_ref=submitted.strategy_ref,
        as_of_date=AS_OF,
        occurred_at=SCREENED,
        base_events=before.events,
    )
    after_scenario = repository.load_campaign("workspace-phase2", "campaign-phase2")
    assert after_scenario == before
    assert scenario.mutated_campaign is False
    assert scenario.hypothetical_snapshot.campaign_version == 2
    assert scenario.hypothetical_plan.primary_launch.festival_id == "idfa"
    assert scenario.reuse_manifest.chat_attempts == 0
    assert scenario.diff.primary_after == "idfa"

    real = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        hypothetical,
        as_of_date=AS_OF,
    )
    assert real.plan == scenario.hypothetical_plan
    assert real.planning_input == scenario.planning_input


def test_explicit_unscreened_fact_survives_rejection_and_non_mutating_screening_scenario() -> None:
    repository, orchestrator, initial = _repository_and_orchestrator()
    initial_world = next(
        item
        for item in initial.aggregate.snapshot.premiere_ledger.scopes
        if item.scope.value == "world"
    )
    assert initial_world.availability == PremiereAvailability.AVAILABLE

    orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "mark_submitted",
            {"festival_id": "hot-docs", "source_refs": ["human:submission"]},
            version=0,
            key="history-submit-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    rejected = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "record_rejection",
            {"festival_id": "hot-docs", "source_refs": ["human:rejection"]},
            version=1,
            key="history-reject-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    rejected_world = next(
        item
        for item in rejected.aggregate.snapshot.premiere_ledger.scopes
        if item.scope.value == "world"
    )
    assert rejected_world.availability == PremiereAvailability.AVAILABLE
    assert rejected.aggregate.snapshot.profile.premiere_assertions

    schedule = _command(
        "schedule_screening",
        {
            "screening_id": "scenario-idfa-screening",
            "festival_id": "idfa",
            "country": "Netherlands",
            "region": "Europe",
            "scheduled_at": "2026-08-25T19:00:00Z",
            "access": "public",
            "source_refs": ["human:scenario"],
        },
        version=2,
        key="history-scenario-schedule",
    )
    confirm = _command(
        "confirm_screening",
        {
            "screening_id": "scenario-idfa-screening",
            "occurred_at": "2026-08-25T19:00:00Z",
            "access": "public",
            "country": "Netherlands",
            "region": "Europe",
            "source_refs": ["human:scenario-proof"],
        },
        version=3,
        key="history-scenario-confirm",
    )
    before = repository.load_campaign("workspace-phase2", "campaign-phase2")
    scenario = CampaignScenarioEngine(orchestrator).simulate(
        before.snapshot,
        (schedule, confirm),
        prior_input=rejected.planning_input,
        prior_plan=rejected.plan,
        base_strategy_ref=rejected.strategy_ref,
        as_of_date=AS_OF,
        occurred_at=SCREENED,
        base_events=before.events,
    )
    hypothetical_world = next(
        item
        for item in scenario.hypothetical_snapshot.premiere_ledger.scopes
        if item.scope.value == "world"
    )

    assert hypothetical_world.availability == PremiereAvailability.CONSUMED
    assert scenario.hypothetical_plan is not None
    assert scenario.planning_input is not None
    assert scenario.hypothetical_plan.primary_launch.festival_id == "idfa"
    hypothetical_primary = next(
        item
        for item in scenario.planning_input.candidates
        if item.festival_id == "idfa"
    )
    assert hypothetical_primary.risk.premiere_constraint.scope.value == "none"
    assert scenario.hypothetical_snapshot.profile.premiere_assertions == (
        before.snapshot.profile.premiere_assertions
    )
    assert repository.load_campaign("workspace-phase2", "campaign-phase2") == before
    assert scenario.mutated_campaign is False


def test_scenario_bounds_and_rendering_expose_no_new_llm_signal() -> None:
    _repository, orchestrator, initial = _repository_and_orchestrator()
    with pytest.raises(ScenarioError, match="one to three"):
        CampaignScenarioEngine(orchestrator).simulate(
            initial.aggregate.snapshot,
            (),
            prior_input=initial.planning_input,
            prior_plan=initial.plan,
            base_strategy_ref=initial.strategy_ref,
            as_of_date=AS_OF,
        )

    submitted = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "mark_submitted",
            {"festival_id": "hot-docs", "source_refs": ["human:submission"]},
            version=0,
            key="render-submit-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    view = CampaignRenderer().render(
        submitted.aggregate.snapshot,
        submitted.plan,
        strategy_ref=submitted.strategy_ref,
        diff=submitted.diff,
        reuse_manifest=submitted.reuse_manifest,
        company_memory={"company": "Meridian Films", "history_rows": 171},
    )
    assert view.primary.festival_id == "hot-docs"
    assert view.reuse.no_new_llm is True
    assert view.reuse.chat_attempts == 0
    assert view.company_memory["history_rows"] == 171


def test_failed_replan_retains_prior_active_strategy_and_marks_stale() -> None:
    _repository, orchestrator, initial = _repository_and_orchestrator()
    first = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "exclude_opportunity",
            {"festival_id": "hot-docs"},
            version=0,
            key="phase2-exclude-hot-docs",
        ),
        as_of_date=AS_OF,
    )
    assert first.status == "ready"
    assert first.plan.primary_launch.festival_id == "idfa"
    second = orchestrator.apply_command_and_replan(
        "workspace-phase2",
        "campaign-phase2",
        _command(
            "exclude_opportunity",
            {"festival_id": "idfa"},
            version=1,
            key="phase2-exclude-idfa",
        ),
        as_of_date=AS_OF,
    )
    assert second.status == "failed"
    assert second.plan is None
    assert second.aggregate.snapshot.active_strategy_ref == first.strategy_ref
    assert second.aggregate.strategy_stale
    assert second.aggregate.strategy_versions[-1].attempt.outcome == "failed"
