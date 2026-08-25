"""Phase 0 contract and golden-fixture tests.

These tests intentionally instantiate contracts and verify frozen policy data.
They do not mock or implement CampaignPlanner, a reducer, persistence, or routes.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import TypeAdapter, ValidationError

from app.agent import scoring
from app.campaign import models
from app.campaign.contracts import (
    BOUNDARY_CONTRACTS,
    CAMPAIGN_RUNTIME_ENABLED,
    COMMAND_EVENT_TYPES,
    DECISION_GRADE_GROUPS,
    DECISION_GRADE_IS_LLM_LABEL,
    DECISION_GRADE_RULES,
    DECISION_GRADE_SOURCE,
    PERSISTENCE_TABLE_CONTRACTS,
    campaign_plan_hash,
    campaign_profile_hash,
    campaign_snapshot_hash,
    canonical_hash,
    canonical_json,
    capability_digest,
    compatibility_edge_hash,
    creative_evidence_hash,
    frozen_candidate_hash,
    generate_raw_capability,
    graph_key,
    parse_campaign_command,
    plan_key,
    planning_input_hash,
    policy_result_hash,
    retrieval_input_hash,
    risk_evidence_hash,
    strategy_diff_hash,
    validate_with_festival_catalog,
    validate_plan_against_input,
)


FIXTURES = Path(__file__).parent / "fixtures" / "campaign"
KNOWN_IDS = frozenset(json.loads((FIXTURES / "known_festival_ids.json").read_text()))
BOUNDARY_SPEC = json.loads((FIXTURES / "boundary_contracts.json").read_text())
OBSERVED_AT = datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)
HASH = "0" * 64


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _fact(value: Any, *, status: models.FactStatus = models.FactStatus.CONFIRMED) -> models.Fact[Any]:
    return models.Fact[Any](
        value=value,
        status=status,
        source_refs=("fixture:source",) if status != models.FactStatus.UNKNOWN else (),
        observed_at=OBSERVED_AT,
    )


def _profile() -> models.CampaignProfile:
    spec = BOUNDARY_SPEC["profile"]
    initial = models.CampaignProfile(
        title=_fact(spec["title"]),
        synopsis=_fact(spec["synopsis"]),
        format=_fact(spec["format"]),
        country=_fact(spec["country"]),
        themes=_fact(tuple(spec["themes"])),
        runtime_minutes=_fact(spec["runtime_minutes"]),
        premiere_assertions=(_fact(spec["premiere_assertion"]),),
        target_regions=tuple(spec["target_regions"]),
        profile_hash=HASH,
    )
    return initial.model_copy(update={"profile_hash": campaign_profile_hash(initial)})


def _retrieval_input(profile: models.CampaignProfile) -> models.RetrievalInput:
    initial = models.RetrievalInput(
        profile_hash=profile.profile_hash,
        semantic_query="Israeli environmental feature documentary about women and displacement",
        format="feature_doc",
        country="Israel",
        themes=("environment", "women", "displacement"),
        target_regions=("North America", "Europe"),
        retrieval_policy_version="retrieval-v1",
        embedding_model="fixture-embedding-v1",
        corpus_identity_version="festival-corpus-v1",
        as_of_date=date(2026, 8, 25),
        retrieval_key=HASH,
    )
    return initial.model_copy(update={"retrieval_key": retrieval_input_hash(initial)})


def _dimensions(score: int) -> tuple[models.DimensionEvidence, ...]:
    names = (
        "thematic_fit",
        "genre_fit",
        "lineup_similarity",
        "company_relationship",
        "strategic_value",
        "deadline_urgency",
    )
    rating = Decimal("4")
    points = Decimal(str(score)) / Decimal(len(names))
    return tuple(
        models.DimensionEvidence(
            dimension=name,
            raw_rating=rating,
            guarded_rating=rating,
            points=points,
            evidence_refs=(f"fixture:dimension:{name}",),
        )
        for name in names
    )


def _candidate(
    festival_id: str,
    score: int,
    grade: str,
    *,
    profile: models.CampaignProfile | None = None,
    retrieval: models.RetrievalInput | None = None,
) -> models.FrozenCandidateEvidence:
    profile = profile or _profile()
    retrieval = retrieval or _retrieval_input(profile)
    spec = next(
        (item for item in BOUNDARY_SPEC["festivals"] if item["festival_id"] == festival_id),
        None,
    )
    name = spec["festival_name"] if spec else festival_id.replace("-", " ").title()
    country = spec["country"] if spec else "United Kingdom"
    region = spec["region"] if spec else "Europe"
    tier = spec["tier"] if spec else "B"
    rank = spec["retrieval_rank"] if spec else 3
    festival_hash = _digest(f"festival:{festival_id}:v1")
    retrieved = models.RetrievedFestivalEvidence(
        festival_id=festival_id,
        identity=models.FestivalIdentitySnapshot(
            festival_name=name,
            country=_fact(country),
            region=_fact(region),
            tier=_fact(tier),
            accepts=_fact(("feature_doc",)),
        ),
        festival_facts_hash=festival_hash,
        retrieval_rank=rank,
        semantic_score=Decimal("0.90"),
        lexical_score=Decimal("0.70"),
        retrieval_sources=("semantic", "lexical"),
        retrieval_backend="frozen-fixture",
        source_confidence=models.FactStatus.CONFIRMED,
        premiere_rule=_fact("World premiere required"),
        deadline_fact=_fact("2026-10-01"),
        fee_fact=models.FeeFact(
            amount=Decimal("50"),
            currency="USD",
            status=models.FactStatus.CONFIRMED,
            source_refs=(f"fixture:fee:{festival_id}",),
            observed_at=OBSERVED_AT,
        ),
        retrieval_key=retrieval.retrieval_key,
    )
    dimensions = _dimensions(score)
    creative_initial = models.CandidateCreativeEvidence(
        festival_id=festival_id,
        profile_hash=profile.profile_hash,
        festival_facts_hash=festival_hash,
        dimensions=dimensions,
        guardrail_adjustments=(),
        company_relationship=models.CompanyRelationshipEvidence(
            rating=Decimal("0"),
            screenings=0,
            award_count=0,
            evidence_refs=(),
        ),
        prompt_version="match-prompt-v1",
        model_version="fixture-model-v1",
        guardrail_version="guardrail-v1",
        creative_key=HASH,
    )
    creative = creative_initial.model_copy(
        update={"creative_key": creative_evidence_hash(creative_initial)}
    )
    risk_initial = models.CandidateRiskEvidence(
        festival_id=festival_id,
        profile_hash=profile.profile_hash,
        festival_facts_hash=festival_hash,
        eligible=True,
        premiere_constraint=models.PremiereConstraint(
            scope=models.PremiereScope.WORLD,
            rule_status=models.FactStatus.CONFIRMED,
            evidence_refs=(f"fixture:premiere:{festival_id}",),
        ),
        premiere_risk="none",
        deadline=models.DeadlineAssessment(
            status=models.DeadlineStatus.OPEN,
            next_deadline=date(2026, 10, 1),
            confidence=models.FactStatus.CONFIRMED,
            material_eligible=True,
            evidence_refs=(f"fixture:deadline:{festival_id}",),
        ),
        runtime_eligible=True,
        uncertainties=(),
        as_of_date=date(2026, 8, 25),
        risk_policy_version="risk-v1",
        risk_input_hash=_digest(f"risk-input:{festival_id}"),
        risk_key=HASH,
    )
    risk = risk_initial.model_copy(update={"risk_key": risk_evidence_hash(risk_initial)})
    candidate_initial = models.FrozenCandidateEvidence(
        festival_id=festival_id,
        retrieved=retrieved,
        creative=creative,
        risk=risk,
        score_breakdown=models.ScoreBreakdown(
            score=score,
            base_score=Decimal(score),
            premiere_penalty=Decimal("0"),
            dimensions=dimensions,
        ),
        decision_grade=models.DecisionGrade(grade),
        future_quality=models.calculate_future_quality(dimensions),
        component_hash=HASH,
    )
    return candidate_initial.model_copy(
        update={"component_hash": frozen_candidate_hash(candidate_initial)}
    )


def _ledger() -> models.PremiereLedgerSnapshot:
    input_hash = _digest("premiere-ledger-input")
    return models.PremiereLedgerSnapshot(
        scopes=(
            models.PremiereScopeState(
                scope=models.PremiereScope.WORLD,
                availability=models.PremiereAvailability.AVAILABLE,
                reason_code="sourced-unscreened-assertion",
                evidence_refs=("fixture:unscreened",),
            ),
            models.PremiereScopeState(
                scope=models.PremiereScope.INTERNATIONAL,
                availability=models.PremiereAvailability.AVAILABLE,
                reason_code="sourced-unscreened-assertion",
                evidence_refs=("fixture:unscreened",),
            ),
        ),
        derivation_policy_version="premiere-ledger-v1",
        input_hash=input_hash,
        ledger_hash=_digest(f"ledger:{input_hash}"),
    )


def _edge(source: str, target: str, status: str) -> models.CompatibilityEdge:
    initial = models.CompatibilityEdge(
        from_festival_id=source,
        to_festival_id=target,
        status=models.CompatibilityStatus(status),
        scope=models.PremiereScope.WORLD,
        reason_code=f"fixture-{status}",
        evidence_refs=("fixture:ledger", "fixture:rule"),
        source_confidence=(
            models.FactStatus.UNKNOWN
            if status == models.CompatibilityStatus.VERIFY
            else models.FactStatus.CONFIRMED
        ),
        graph_policy_version="graph-v1",
        edge_hash=HASH,
    )
    return initial.model_copy(update={"edge_hash": compatibility_edge_hash(initial)})


def _artifact_keys(profile: models.CampaignProfile, retrieval: models.RetrievalInput) -> models.ArtifactKeys:
    return models.ArtifactKeys(
        identity_hash=profile.profile_hash,
        retrieval_key=retrieval.retrieval_key,
        creative_key=_digest("creative:aggregate"),
        risk_key=_digest("risk:aggregate"),
        graph_key=_digest("graph:aggregate"),
        plan_key=_digest("plan:aggregate"),
    )


def _planning_input_from_specs(specs: list[dict[str, Any]], case_id: str) -> models.PlanningInput:
    profile = _profile()
    retrieval = _retrieval_input(profile)
    candidates = tuple(
        _candidate(
            item["festival_id"],
            item["score"],
            item["grade"],
            profile=profile,
            retrieval=retrieval,
        )
        for item in specs
    )
    edges: list[models.CompatibilityEdge] = []
    for source in specs:
        for target in specs:
            if source["festival_id"] == target["festival_id"]:
                continue
            target_id = target["festival_id"]
            status = (
                "compatible"
                if target_id in source["preserved_ids"]
                else "verify"
                if target_id in source["verify_ids"]
                else "incompatible"
            )
            edges.append(_edge(source["festival_id"], target_id, status))
    constraints: tuple[models.CampaignConstraint, ...] = ()
    if case_id.startswith("D-"):
        constraints = (
            models.CampaignConstraint(
                constraint_id="preserve-world",
                constraint_type="preserve_world_premiere",
                strength=models.ConstraintStrength.HARD,
                value=True,
                locked=True,
                source_ref="human:preservation-policy",
            ),
        )
    initial = models.PlanningInput(
        campaign_id=f"campaign-{case_id.lower()}",
        campaign_version=1,
        profile=profile,
        premiere_ledger=_ledger(),
        candidates=candidates,
        compatibility_edges=tuple(edges),
        opportunities=tuple(
            models.CampaignOpportunity(
                opportunity_id=f"opportunity-{item.festival_id}",
                festival_id=item.festival_id,
            )
            for item in candidates
        ),
        constraints=constraints,
        preservation_mode=models.PreservationMode.BALANCED,
        budget_constraint=models.BudgetConstraint(
            constraint_id="hard-budget",
            limit=models.Money(amount=Decimal("100"), currency="USD"),
            hard=True,
        ),
        required_fees=(),
        as_of_date=date(2026, 8, 25),
        policy_versions=("planner-v2", "budget-v1", "graph-v1"),
        artifact_keys=_artifact_keys(profile, retrieval),
        planning_input_hash=HASH,
    )
    return initial.model_copy(update={"planning_input_hash": planning_input_hash(initial)})


def _diagnostic(item: dict[str, Any]) -> models.PlannerCandidateDiagnostic:
    grade = models.DecisionGrade(item["grade"])
    return models.PlannerCandidateDiagnostic(
        festival_id=item["festival_id"],
        hard_filter=models.HardFilterResult(
            feasible=not item.get("filtered", False),
            reason_codes=(item["filter_reason"],) if item.get("filter_reason") else (),
            constraint_refs=("preserve-world",) if item.get("filtered") else (),
        ),
        on_pareto_frontier=item["frontier"],
        immediate_utility=item["score"],
        decision_grade=grade,
        decision_grade_group=DECISION_GRADE_GROUPS[grade],
        preservation=models.PreservationDiagnostics(
            mode=models.PreservationMode.BALANCED,
            known_preserved_pct=Decimal(item["known_preserved_pct"]),
            possible_additional_pct=Decimal(item["possible_pct"]),
            known_destroyed_pct=Decimal(item["destroyed_pct"]),
            preserved_ids=tuple(item["preserved_ids"]),
            verify_ids=tuple(item["verify_ids"]),
            destroyed_ids=tuple(item["destroyed_ids"]),
        ),
        verification_burden=models.VerificationBurden(
            blocking_gate_count=item["blocking_gates"],
            verify_edge_quality_pct=Decimal(item["verify_quality_pct"]),
            total_gate_count=item["total_gates"],
        ),
        budget_assessment=models.BudgetAssessment(
            state="KNOWN_FEASIBLE",
            hard_limit=models.Money(amount=Decimal("100"), currency="USD"),
            known_total=models.Money(amount=Decimal("0"), currency="USD"),
        ),
        immediate_rank=item["rU"],
        preservation_rank=item["rP"],
        deterministic_tie_break_id=item["festival_id"],
    )


def _policy_result(case: dict[str, Any]) -> models.ExpectedPolicyResult:
    selections = tuple(
        models.ModeSelection(
            mode=models.PreservationMode(mode),
            selected_festival_id=case[mode],
            ordered_frontier_ids=tuple(case["expected_frontier"]),
        )
        for mode in ("balanced", "strict", "opportunistic")
    )
    initial = models.ExpectedPolicyResult(
        case_id=case["case_id"],
        naive_selected_id=case["naive"],
        expected_frontier_ids=tuple(case["expected_frontier"]),
        diagnostics=tuple(_diagnostic(item) for item in case["candidates"]),
        mode_selections=selections,
        result_hash=HASH,
    )
    return initial.model_copy(update={"result_hash": policy_result_hash(initial)})


def _boundary_bundle() -> dict[str, models.FrozenModel]:
    profile = _profile()
    retrieval = _retrieval_input(profile)
    hot_docs = _candidate(
        "hot-docs", 90, "submit_first", profile=profile, retrieval=retrieval
    )
    idfa = _candidate(
        "idfa", 82, "submit_first", profile=profile, retrieval=retrieval
    )
    edges = (
        _edge("hot-docs", "idfa", "incompatible"),
        _edge("idfa", "hot-docs", "compatible"),
    )
    planning_initial = models.PlanningInput(
        campaign_id="campaign-golden",
        campaign_version=3,
        profile=profile,
        premiere_ledger=_ledger(),
        candidates=(hot_docs, idfa),
        compatibility_edges=edges,
        opportunities=(
            models.CampaignOpportunity(
                opportunity_id="opportunity-hot-docs",
                festival_id="hot-docs",
                submission_state=models.SubmissionState.SUBMITTED,
            ),
            models.CampaignOpportunity(
                opportunity_id="opportunity-idfa",
                festival_id="idfa",
            ),
        ),
        constraints=(),
        preservation_mode=models.PreservationMode.BALANCED,
        budget_constraint=models.BudgetConstraint(
            constraint_id="hard-budget",
            limit=models.Money(amount=Decimal("200"), currency="USD"),
        ),
        required_fees=(
            models.RequiredFee(
                fee_id="fee-hot-docs",
                action_id="submit-hot-docs",
                festival_id="hot-docs",
                action_scope=models.FeeActionScope.CURRENT_ROOT,
                required_now=True,
                fee=hot_docs.retrieved.fee_fact,
            ),
        ),
        as_of_date=date(2026, 8, 25),
        policy_versions=("planner-v2", "budget-v1", "graph-v1"),
        artifact_keys=_artifact_keys(profile, retrieval),
        planning_input_hash=HASH,
    )
    planning = planning_initial.model_copy(
        update={"planning_input_hash": planning_input_hash(planning_initial)}
    )
    diagnostic = models.PlannerCandidateDiagnostic(
        festival_id="hot-docs",
        hard_filter=models.HardFilterResult(feasible=True),
        on_pareto_frontier=True,
        immediate_utility=90,
        decision_grade=models.DecisionGrade.SUBMIT_FIRST,
        decision_grade_group=models.DecisionGradeGroup.LAUNCH_READY,
        preservation=models.PreservationDiagnostics(
            mode=models.PreservationMode.BALANCED,
            known_preserved_pct=Decimal("0"),
            possible_additional_pct=Decimal("0"),
            known_destroyed_pct=Decimal("100"),
            destroyed_ids=("idfa",),
        ),
        verification_burden=models.VerificationBurden(
            blocking_gate_count=0,
            verify_edge_quality_pct=Decimal("0"),
            total_gate_count=0,
        ),
        budget_assessment=models.BudgetAssessment(
            state=models.HardBudgetState.KNOWN_FEASIBLE,
            hard_limit=models.Money(amount=Decimal("200"), currency="USD"),
            known_total=models.Money(amount=Decimal("50"), currency="USD"),
            required_action_ids=("submit-hot-docs",),
            included_fee_ids=("fee-hot-docs",),
        ),
        immediate_rank=1,
        preservation_rank=2,
        deterministic_tie_break_id="hot-docs",
    )
    budget = models.BudgetAssessment(
        state=models.HardBudgetState.KNOWN_FEASIBLE,
        hard_limit=models.Money(amount=Decimal("200"), currency="USD"),
        known_total=models.Money(amount=Decimal("50"), currency="USD"),
        required_action_ids=("submit-hot-docs",),
        included_fee_ids=("fee-hot-docs",),
    )
    plan_initial = models.CampaignPlan(
        primary_launch=models.PrimaryLaunch(
            festival_id="hot-docs",
            submission_action="Submit the current application.",
            screening_gate="screening-confirmed-public",
            reason_refs=("score:hot-docs", "edge:hot-docs:idfa"),
        ),
        alternative_launches=(
            models.AlternativeLaunch(
                festival_id="idfa",
                activates_on="primary_rejected_or_withdrawn",
            ),
        ),
        rejection_branch=models.RejectionBranch(
            of_festival_id="hot-docs",
            promote_festival_id="idfa",
        ),
        screened_branch=models.ScreenedBranch(
            at_festival_id="hot-docs",
            premiere_effect=models.PremiereEffect(
                world=models.PremiereAvailability.CONSUMED,
                international=models.PremiereAvailability.CONSUMED,
            ),
            post_premiere_opportunity_ids=(),
        ),
        budget=budget,
        option_preservation=diagnostic.preservation,
        next_actions=(
            models.NextAction(
                action_id="submit-hot-docs",
                festival_id="hot-docs",
                description="Submit after confirming the fee.",
                required_now=True,
            ),
        ),
        selection_diagnostics=(diagnostic,),
        plan_hash=HASH,
    )
    plan = plan_initial.model_copy(update={"plan_hash": campaign_plan_hash(plan_initial)})
    snapshot_initial = models.CampaignSnapshot(
        workspace_id="workspace-golden",
        campaign_id="campaign-golden",
        campaign_version=3,
        lifecycle=models.CampaignLifecycle.ACTIVE,
        readiness=models.CampaignReadiness.READY,
        profile=profile,
        premiere_ledger=planning.premiere_ledger,
        opportunities=planning.opportunities,
        candidates=planning.candidates,
        active_strategy_ref="strategy-3",
        aggregate_hash=HASH,
    )
    snapshot = snapshot_initial.model_copy(
        update={"aggregate_hash": campaign_snapshot_hash(snapshot_initial)}
    )
    keys = planning.artifact_keys
    reuse = models.ReuseManifest(
        invalidation_class=models.InvalidationClass.C,
        prior_artifact_keys=keys,
        current_artifact_keys=keys,
        reused_artifacts=(
            models.ArtifactName.RETRIEVAL,
            models.ArtifactName.CREATIVE_EVIDENCE,
            models.ArtifactName.RISK,
            models.ArtifactName.PREMIERE_LEDGER,
            models.ArtifactName.COMPATIBILITY_GRAPH,
        ),
        rerun_artifacts=(models.ArtifactName.PLANNER, models.ArtifactName.CLARIFICATION),
        invalidated_artifacts=(),
        reasons=("record-rejection",),
        chat_attempts=0,
        embedding_attempts=0,
    )
    diff_initial = models.StrategyDiff(
        campaign_id="campaign-golden",
        base_campaign_version=2,
        new_campaign_version=3,
        base_strategy_ref="strategy-2",
        new_strategy_ref="strategy-3",
        primary_before="idfa",
        primary_after="hot-docs",
        added_route_ids=("hot-docs",),
        removed_route_ids=(),
        unchanged_route_ids=("idfa",),
        budget_state_before=models.HardBudgetState.KNOWN_FEASIBLE,
        budget_state_after=models.HardBudgetState.KNOWN_FEASIBLE,
        preservation_before=diagnostic.preservation,
        preservation_after=diagnostic.preservation,
        causal_refs=("event:rejection",),
        reuse_summary=reuse,
        diff_hash=HASH,
    )
    diff = diff_initial.model_copy(update={"diff_hash": strategy_diff_hash(diff_initial)})
    return {
        "CampaignProfile": profile,
        "RetrievalInput": retrieval,
        "RetrievedFestivalEvidence": hot_docs.retrieved,
        "CandidateCreativeEvidence": hot_docs.creative,
        "CandidateRiskEvidence": hot_docs.risk,
        "FrozenCandidateEvidence": hot_docs,
        "CampaignSnapshot": snapshot,
        "CompatibilityEdge": edges[0],
        "PlanningInput": planning,
        "CampaignPlan": plan,
        "ReuseManifest": reuse,
        "StrategyDiff": diff,
    }


def test_phase_zero_is_runtime_disabled_and_persistence_is_contract_only() -> None:
    assert CAMPAIGN_RUNTIME_ENABLED is False
    assert [item.table_name for item in PERSISTENCE_TABLE_CONTRACTS] == [
        "workspaces",
        "film_projects",
        "campaigns",
        "campaign_constraints",
        "campaign_events",
        "campaign_opportunities",
        "screenings",
        "strategy_versions",
    ]


def test_all_boundary_contracts_have_typed_frozen_golden_instances() -> None:
    bundle = _boundary_bundle()
    exact_json = json.loads((FIXTURES / "boundary_models.json").read_text())
    declared = {item.model_name for item in BOUNDARY_CONTRACTS}
    assert declared == set(BOUNDARY_SPEC["expected_boundary_models"])
    assert declared == set(bundle)
    assert declared == set(exact_json)
    for descriptor in BOUNDARY_CONTRACTS:
        instance = bundle[descriptor.model_name]
        assert instance.model_dump(mode="json", by_alias=True) == exact_json[descriptor.model_name]
        reloaded = type(instance).model_validate(
            exact_json[descriptor.model_name],
            context={"known_festival_ids": KNOWN_IDS},
        )
        assert reloaded == instance
        assert instance.model_config["frozen"] is True
        assert instance.model_config["extra"] == "forbid"
        assert descriptor.producer and descriptor.consumers and descriptor.hash_field
        with pytest.raises(ValidationError):
            type(instance).model_validate({**instance.model_dump(), "unexpected": True})


def test_every_frozen_candidate_future_quality_matches_five_dimension_formula() -> None:
    payloads: list[dict[str, Any]] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if {"festival_id", "future_quality", "score_breakdown"} <= value.keys():
                payloads.append(value)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for fixture_name in ("boundary_models.json", "planner_archetype_models.json"):
        collect(json.loads((FIXTURES / fixture_name).read_text()))

    assert payloads
    for payload in payloads:
        candidate = models.FrozenCandidateEvidence.model_validate(
            payload,
            context={"known_festival_ids": KNOWN_IDS},
        )
        expected = models.calculate_future_quality(candidate.score_breakdown.dimensions)
        assert candidate.future_quality == expected
        assert candidate.future_quality.as_tuple().exponent == -1

    excluded_dimensions = deepcopy(payloads[0])
    excluded_dimensions["score_breakdown"]["premiere_penalty"] = "100"
    for dimension in excluded_dimensions["score_breakdown"]["dimensions"]:
        if dimension["dimension"] == "deadline_urgency":
            dimension["points"] = "0"
    assert models.FrozenCandidateEvidence.model_validate(
        excluded_dimensions,
        context={"known_festival_ids": KNOWN_IDS},
    ).future_quality == Decimal(excluded_dimensions["future_quality"])


def test_planner_boundary_accepts_only_planning_input_contract() -> None:
    planning = _boundary_bundle()["PlanningInput"]
    assert isinstance(planning, models.PlanningInput)
    assert "raw legacy dictionaries" in models.PlanningInput.__doc__ or (
        "only future CampaignPlanner input" in models.PlanningInput.__doc__
    )
    with pytest.raises(ValidationError):
        models.PlanningInput.model_validate({"legacy_ranked_festivals": []})


@pytest.mark.parametrize("enum_type", [
    models.FactStatus,
    models.CampaignLifecycle,
    models.CampaignReadiness,
    models.SubmissionState,
    models.OfferState,
    models.OpportunityPolicyState,
    models.ScreeningState,
    models.ScreeningAccess,
    models.PremiereAvailability,
    models.CompatibilityStatus,
    models.PreservationMode,
    models.InvalidationClass,
    models.HardBudgetState,
])
def test_core_enums_reject_unknown_values(enum_type: type[Any]) -> None:
    adapter = TypeAdapter(enum_type)
    with pytest.raises(ValidationError):
        adapter.validate_python("not-a-contract-value")


def test_fact_epistemic_contract_and_utc_normalization() -> None:
    unknown = models.Fact[str](
        status="unknown", source_refs=(), observed_at="2026-08-25T10:00:00+03:00"
    )
    assert unknown.value is None
    assert unknown.model_dump(mode="json")["observed_at"] == "2026-08-25T07:00:00Z"
    with pytest.raises(ValidationError):
        models.Fact[str](
            value="available",
            status="contradicted",
            source_refs=("source:a", "source:b"),
            observed_at=OBSERVED_AT,
        )
    with pytest.raises(ValidationError):
        models.Fact[str](
            value="confirmed-without-source",
            status="confirmed",
            observed_at=OBSERVED_AT,
        )


def test_canonical_festival_id_chain_and_catalog_rejections() -> None:
    bundle = _boundary_bundle()
    for name in (
        "RetrievedFestivalEvidence",
        "CandidateCreativeEvidence",
        "CandidateRiskEvidence",
        "FrozenCandidateEvidence",
        "CampaignSnapshot",
        "CompatibilityEdge",
        "PlanningInput",
        "CampaignPlan",
    ):
        instance = bundle[name]
        validated = validate_with_festival_catalog(type(instance), instance.model_dump(), KNOWN_IDS)
        assert isinstance(validated, type(instance))

    retrieved = bundle["RetrievedFestivalEvidence"].model_dump()
    retrieved["festival_id"] = "hot-docs-film-festival"
    with pytest.raises(ValidationError, match="unknown canonical festival_id"):
        validate_with_festival_catalog(models.RetrievedFestivalEvidence, retrieved, KNOWN_IDS)
    retrieved["festival_id"] = "Hot Docs"
    with pytest.raises(ValidationError):
        validate_with_festival_catalog(models.RetrievedFestivalEvidence, retrieved, KNOWN_IDS)
    del retrieved["festival_id"]
    with pytest.raises(ValidationError):
        validate_with_festival_catalog(models.RetrievedFestivalEvidence, retrieved, KNOWN_IDS)


def test_planning_input_rejects_duplicate_mismatch_and_absent_graph_ids() -> None:
    planning = _boundary_bundle()["PlanningInput"]
    payload = planning.model_dump()
    payload["candidates"] = [payload["candidates"][0], payload["candidates"][0]]
    payload["opportunities"] = [payload["opportunities"][0], payload["opportunities"][0]]
    with pytest.raises(ValidationError, match="candidate festival_ids must be unique"):
        models.PlanningInput.model_validate(payload)

    candidate = planning.candidates[0].model_dump()
    candidate["creative"]["festival_id"] = "idfa"
    with pytest.raises(ValidationError, match="must share festival_id"):
        models.FrozenCandidateEvidence.model_validate(candidate)

    payload = planning.model_dump()
    payload["compatibility_edges"][0]["to_festival_id"] = "sundance-film-festival"
    with pytest.raises(ValidationError, match="complete directed candidate edge set"):
        models.PlanningInput.model_validate(payload)


def test_plan_references_must_be_planning_input_ids() -> None:
    bundle = _boundary_bundle()
    planning = bundle["PlanningInput"]
    plan = bundle["CampaignPlan"]
    payload = plan.model_dump(by_alias=True)
    payload["alternative_launches"][0]["festival_id"] = "sundance-film-festival"
    payload["rejection_branch"]["promote_festival_id"] = "sundance-film-festival"
    with pytest.raises(ValidationError, match="unknown canonical festival_id"):
        validate_plan_against_input(payload, planning)
    incompatible = plan.model_dump(by_alias=True)
    incompatible["post_premiere_opportunities"] = ("idfa",)
    with pytest.raises(ValueError, match="incompatible edge"):
        validate_plan_against_input(incompatible, planning)


def _actor() -> dict[str, str]:
    return {"kind": "human", "actor_ref": "human:fixture"}


def _command_payloads() -> dict[str, dict[str, Any]]:
    fact = {
        "value": "Updated value",
        "status": "asserted",
        "source_refs": ["human:fixture"],
        "observed_at": "2026-08-25T07:00:00Z",
    }
    decision = {"festival_id": "hot-docs", "source_refs": ["human:fixture"]}
    return {
        "update_profile_fact": {"fact_key": "synopsis", "fact": fact},
        "set_constraint": {
            "constraint": {
                "constraint_id": "constraint-1",
                "constraint_type": "preservation_mode",
                "strength": "preference",
                "value": "balanced",
                "locked": False,
                "active": True,
                "candidate_expanding": False,
                "source_ref": "human:fixture"
            }
        },
        "remove_constraint": {"constraint_id": "constraint-1", "explicit_unlock": False},
        "lock_opportunity": {"festival_id": "hot-docs"},
        "unlock_opportunity": {"festival_id": "hot-docs"},
        "exclude_opportunity": {"festival_id": "hot-docs"},
        "include_opportunity": {"festival_id": "hot-docs"},
        "mark_submitted": decision,
        "record_rejection": decision,
        "record_invitation": {**decision, "offer_ref": "offer:fixture"},
        "accept_offer": decision,
        "decline_offer": decision,
        "withdraw": decision,
        "schedule_screening": {
            "screening_id": "screening-1",
            "festival_id": "hot-docs",
            "country": "Canada",
            "scheduled_at": "2026-09-01T19:00:00Z",
            "access": "unknown",
            "source_refs": [],
        },
        "confirm_screening": {
            "screening_id": "screening-1",
            "occurred_at": "2026-09-01T19:00:00Z",
            "access": "public",
            "country": "Canada",
            "source_refs": ["human:screening-proof"],
        },
        "cancel_screening": {"screening_id": "screening-1", "correction": False},
        "verify_opportunity_fact": {
            "festival_id": "hot-docs",
            "verification_item_id": "verify-deadline",
            "result": fact,
        },
        "correct_record": {
            "prior_ref": "event:1",
            "corrected_domain": "domain_evidence",
            "replacement": fact,
        },
        "close_campaign": {"reason_ref": "human:close"},
    }


def _expected_invalidation(command_type: str) -> str:
    if command_type == "update_profile_fact":
        return "A"
    if command_type in {"confirm_screening", "verify_opportunity_fact", "correct_record"}:
        return "B"
    return "C"


def test_all_lifecycle_commands_map_to_typed_events_and_reject_malformed_payloads() -> None:
    commands = _command_payloads()
    assert set(commands) == {item.value for item in models.CampaignCommandType}
    assert set(COMMAND_EVENT_TYPES) == set(models.CampaignCommandType)
    for command_type, payload in commands.items():
        raw = {
            "type": command_type,
            "payload": payload,
            "expected_version": 3,
            "idempotency_key": f"fixture-{command_type}",
            "actor": _actor(),
            "invalidation_class": _expected_invalidation(command_type),
        }
        command = parse_campaign_command(raw, KNOWN_IDS)
        event = models.CampaignEvent(
            event_id=f"event-{command_type}",
            campaign_id="campaign-golden",
            sequence_no=1,
            type=COMMAND_EVENT_TYPES[command.type],
            command=command,
            before_aggregate_hash=_digest("before"),
            after_aggregate_hash=_digest("after"),
            occurred_at=OBSERVED_AT,
        )
        assert event.command.invalidation_class.value == _expected_invalidation(command_type)
        malformed = deepcopy(raw)
        malformed["payload"] = {"unexpected": True}
        with pytest.raises(ValidationError):
            parse_campaign_command(malformed, KNOWN_IDS)


def test_command_invalidation_distinguishes_scheduled_and_confirmed_public_screening() -> None:
    payloads = _command_payloads()
    schedule = parse_campaign_command(
        {
            "type": "schedule_screening",
            "payload": payloads["schedule_screening"],
            "expected_version": 1,
            "idempotency_key": "schedule-fixture-1",
            "actor": _actor(),
            "invalidation_class": "C",
        },
        KNOWN_IDS,
    )
    confirm = parse_campaign_command(
        {
            "type": "confirm_screening",
            "payload": payloads["confirm_screening"],
            "expected_version": 2,
            "idempotency_key": "confirm-fixture-1",
            "actor": _actor(),
            "invalidation_class": "B",
        },
        KNOWN_IDS,
    )
    assert schedule.invalidation_class == models.InvalidationClass.C
    assert confirm.invalidation_class == models.InvalidationClass.B


def test_premiere_semantics_are_frozen_as_expectations_without_a_ledger_engine() -> None:
    fixture = json.loads((FIXTURES / "premiere_semantics.json").read_text())
    cases = {}
    for item in fixture["cases"]:
        state = models.PremiereScopeState(
            scope=item["scope"],
            availability=item["availability"],
            contradiction=item["contradiction"],
            reason_code=item["reason_code"],
            evidence_refs=(f"fixture:{item['case_id']}",),
        )
        cases[item["case_id"]] = (item, state)
    assert cases["empty-history-is-unknown"][1].availability == models.PremiereAvailability.UNKNOWN
    assert cases["sourced-unscreened-is-available"][1].availability == models.PremiereAvailability.AVAILABLE
    assert cases["confirmed-public-consumes-world"][1].availability == models.PremiereAvailability.CONSUMED
    assert cases["foreign-public-consumes-international"][1].availability == models.PremiereAvailability.CONSUMED
    assert cases["domestic-public-does-not-consume-international"][1].availability == models.PremiereAvailability.AVAILABLE
    assert cases["scheduled-public-does-not-consume"][1].availability == models.PremiereAvailability.AVAILABLE
    assert cases["occurred-private-does-not-consume"][1].availability == models.PremiereAvailability.AVAILABLE
    assert cases["occurred-unknown-access-stays-unknown"][0]["blocking"] is True
    assert cases["contradiction-is-unknown"][1].contradiction is True
    assert cases["cancelled-screening-does-not-consume"][1].availability == models.PremiereAvailability.AVAILABLE
    assert cases["correction-rederives-with-history-retained"][0]["prior_event_retained"] is True
    assert cases["company-memory-never-consumes-current-film"][1].availability == models.PremiereAvailability.UNKNOWN


def test_constraint_and_correction_metadata_deterministically_freeze_a_b_c() -> None:
    base_constraint = _command_payloads()["set_constraint"]["constraint"]
    with pytest.raises(ValidationError):
        parse_campaign_command({
            "type": "set_constraint",
            "payload": {"constraint": {**base_constraint, "candidate_expanding": True}},
            "expected_version": 1,
            "idempotency_key": "expand-region-1",
            "actor": _actor(),
            "invalidation_class": "C",
        })
    expanding = parse_campaign_command({
        "type": "set_constraint",
        "payload": {"constraint": {**base_constraint, "candidate_expanding": True}},
        "expected_version": 1,
        "idempotency_key": "expand-region-2",
        "actor": _actor(),
        "invalidation_class": "A",
    })
    assert expanding.invalidation_class == models.InvalidationClass.A


def test_canonical_serialization_and_hashes_are_semantic_and_deterministic() -> None:
    first = {"b": models.PreservationMode.BALANCED, "a": Decimal("1.00")}
    second = {"a": Decimal("1.0"), "b": "balanced"}
    assert canonical_json(first) == canonical_json(second) == '{"a":"1","b":"balanced"}'
    assert canonical_hash(first) == canonical_hash(second)
    offset_fact = models.Fact[str](
        value="same",
        status="confirmed",
        source_refs=("fixture:source",),
        observed_at="2026-08-25T10:00:00+03:00",
    )
    utc_fact = models.Fact[str](
        value="same",
        status="confirmed",
        source_refs=("fixture:source",),
        observed_at="2026-08-25T07:00:00Z",
    )
    assert canonical_hash(offset_fact) == canonical_hash(utc_fact)
    assert canonical_hash({"semantic": 1}) != canonical_hash({"semantic": 2})

    bundle = _boundary_bundle()
    assert bundle["CampaignProfile"].profile_hash == campaign_profile_hash(bundle["CampaignProfile"])
    assert bundle["RetrievalInput"].retrieval_key == retrieval_input_hash(bundle["RetrievalInput"])
    assert bundle["PlanningInput"].planning_input_hash == planning_input_hash(bundle["PlanningInput"])
    assert len(graph_key(bundle["PlanningInput"])) == 64
    assert len(plan_key(bundle["PlanningInput"])) == 64
    policy_change = bundle["PlanningInput"].model_copy(
        update={"preservation_mode": models.PreservationMode.STRICT}
    )
    assert graph_key(policy_change) == graph_key(bundle["PlanningInput"])
    assert plan_key(policy_change) != plan_key(bundle["PlanningInput"])
    assert bundle["CampaignSnapshot"].aggregate_hash == campaign_snapshot_hash(bundle["CampaignSnapshot"])
    assert bundle["CampaignPlan"].plan_hash == campaign_plan_hash(bundle["CampaignPlan"])
    assert bundle["StrategyDiff"].diff_hash == strategy_diff_hash(bundle["StrategyDiff"])


def test_hashes_ignore_volatile_plan_prose_but_change_on_semantic_input() -> None:
    plan = _boundary_bundle()["CampaignPlan"]
    payload = plan.model_dump()
    payload["primary_launch"]["submission_action"] = "Different display prose only."
    payload["next_actions"][0]["description"] = "Different rendered action prose."
    prose_variant = models.CampaignPlan.model_validate(payload)
    assert campaign_plan_hash(prose_variant) == campaign_plan_hash(plan)
    payload["primary_launch"]["festival_id"] = "docaviv"
    payload["rejection_branch"]["of_festival_id"] = "docaviv"
    semantic_variant = models.CampaignPlan.model_validate(payload)
    assert campaign_plan_hash(semantic_variant) != campaign_plan_hash(plan)

    planning = _boundary_bundle()["PlanningInput"]
    reordered = planning.model_copy(
        update={
            "candidates": tuple(reversed(planning.candidates)),
            "compatibility_edges": tuple(reversed(planning.compatibility_edges)),
            "opportunities": tuple(reversed(planning.opportunities)),
        }
    )
    assert planning_input_hash(reordered) == planning_input_hash(planning)
    changed_payload = planning.model_dump()
    changed_payload["candidates"][0]["score_breakdown"]["score"] = 91
    changed = models.PlanningInput.model_validate(changed_payload)
    assert planning_input_hash(changed) != planning_input_hash(planning)


def test_budget_golden_states_required_now_scope_and_unknown_fee_semantics() -> None:
    fixture = json.loads((FIXTURES / "budget_cases.json").read_text())
    limit = models.Money(**fixture["hard_limit"])
    for case in fixture["cases"]:
        assessment = models.BudgetAssessment(
            state=case["state"],
            hard_limit=limit,
            known_total=models.Money(amount=case["known_total"], currency="USD"),
            unknown_fee_ids=tuple(case["unknown_fee_ids"]),
            required_action_ids=("submit-root",),
            included_fee_ids=("fee-root",),
        )
        assert assessment.state.value == case["state"]
    unknown = models.FeeFact(
        status="unknown", source_refs=(), observed_at=OBSERVED_AT
    )
    dumped = unknown.model_dump(mode="json")
    assert dumped["amount"] is None and dumped["currency"] is None
    with pytest.raises(ValidationError):
        models.FeeFact(
            amount=0,
            currency="USD",
            status="unknown",
            source_refs=(),
            observed_at=OBSERVED_AT,
        )
    for scope in fixture["required_now"]["included"]:
        required = models.RequiredFee(
            fee_id=f"fee-{scope}",
            action_id=f"action-{scope}",
            festival_id="hot-docs",
            action_scope=scope,
            required_now=True,
            fee=models.FeeFact(
                amount=10,
                currency="USD",
                status="confirmed",
                source_refs=("fixture:fee",),
                observed_at=OBSERVED_AT,
            ),
        )
        assert required.required_now
    for scope in fixture["required_now"]["excluded"]:
        with pytest.raises(ValidationError):
            models.RequiredFee(
                fee_id=f"fee-{scope}",
                action_id=f"action-{scope}",
                festival_id="hot-docs",
                action_scope=scope,
                required_now=True,
                fee=unknown,
            )


def test_no_hard_budget_and_soft_preference_do_not_fabricate_hard_semantics() -> None:
    bundle = _boundary_bundle()
    baseline_input = bundle["PlanningInput"]
    baseline_plan = bundle["CampaignPlan"]
    assert baseline_input.budget_constraint is not None

    def planning_variant(
        constraint: models.BudgetConstraint | None,
        required_fees: tuple[models.RequiredFee, ...],
    ) -> models.PlanningInput:
        payload = baseline_input.model_dump()
        payload["budget_constraint"] = (
            constraint.model_dump() if constraint is not None else None
        )
        payload["required_fees"] = [item.model_dump() for item in required_fees]
        payload["planning_input_hash"] = HASH
        initial = models.PlanningInput.model_validate(payload)
        return initial.model_copy(
            update={"planning_input_hash": planning_input_hash(initial)}
        )

    plan_payload = baseline_plan.model_dump(by_alias=True)
    plan_payload["budget"] = None
    plan_payload["selection_diagnostics"][0]["budget_assessment"] = None

    no_budget_input = planning_variant(None, ())
    no_budget_plan = validate_plan_against_input(plan_payload, no_budget_input)
    assert no_budget_plan.budget is None
    assert no_budget_plan.model_dump(mode="json")["budget"] is None
    aggregate = models.CampaignAggregateResponse(
        snapshot=bundle["CampaignSnapshot"],
        active_plan=no_budget_plan,
    )
    assert aggregate.model_dump(mode="json")["active_plan"]["budget"] is None
    with pytest.raises(ValueError, match="hard budget requires"):
        validate_plan_against_input(plan_payload, baseline_input)

    soft_constraint = models.BudgetConstraint(
        constraint_id="soft-budget",
        limit=models.Money(amount=Decimal("200"), currency="USD"),
        hard=False,
    )
    known_over_fee = models.RequiredFee(
        fee_id="fee-soft-over",
        action_id="submit-hot-docs",
        festival_id="hot-docs",
        action_scope=models.FeeActionScope.CURRENT_ROOT,
        required_now=True,
        fee=models.FeeFact(
            amount=Decimal("250"),
            currency="USD",
            status=models.FactStatus.CONFIRMED,
            source_refs=("fixture:soft-fee",),
            observed_at=OBSERVED_AT,
        ),
    )
    soft_over_input = planning_variant(soft_constraint, (known_over_fee,))
    plan_payload["selection_diagnostics"][0]["soft_budget_preference_rank"] = 2
    soft_over_plan = validate_plan_against_input(plan_payload, soft_over_input)
    assert soft_over_plan.budget is None
    assert soft_over_plan.selection_diagnostics[0].hard_filter.feasible
    assert soft_over_plan.selection_diagnostics[0].soft_budget_preference_rank == 2

    unknown_fee = models.RequiredFee(
        fee_id="fee-soft-unknown",
        action_id="submit-hot-docs",
        festival_id="hot-docs",
        action_scope=models.FeeActionScope.CURRENT_ROOT,
        required_now=True,
        fee=models.FeeFact(
            status=models.FactStatus.UNKNOWN,
            source_refs=(),
            observed_at=OBSERVED_AT,
        ),
    )
    soft_unknown_input = planning_variant(soft_constraint, (unknown_fee,))
    soft_unknown_plan = validate_plan_against_input(plan_payload, soft_unknown_input)
    assert soft_unknown_plan.budget is None
    assert unknown_fee.fee.amount is None
    assert unknown_fee.fee.currency is None
    assert not any(
        gate.blocking and gate.affected_decision.startswith("budget")
        for gate in soft_unknown_plan.verification_gates
    )

    hard_unknown_input = planning_variant(
        baseline_input.budget_constraint,
        (unknown_fee,),
    )
    hard_verify_payload = baseline_plan.model_dump(by_alias=True)
    hard_verify_payload["budget"] = models.BudgetAssessment(
        state=models.HardBudgetState.VERIFY,
        hard_limit=baseline_input.budget_constraint.limit,
        known_total=models.Money(amount=Decimal("0"), currency="USD"),
        unknown_fee_ids=(unknown_fee.fee_id,),
        required_action_ids=(unknown_fee.action_id,),
        included_fee_ids=(unknown_fee.fee_id,),
    ).model_dump()
    hard_verify_payload["verification_gates"] = [{
        "id": "verify-hard-budget-fee",
        "fact_key": "fee.fee-soft-unknown",
        "affected_decision": "budget:primary_launch",
        "blocking": True,
        "source_refs": [],
    }]
    assert validate_plan_against_input(
        hard_verify_payload, hard_unknown_input
    ).budget.state == models.HardBudgetState.VERIFY

    false_feasible_payload = deepcopy(hard_verify_payload)
    false_feasible_payload["budget"] = models.BudgetAssessment(
        state=models.HardBudgetState.KNOWN_FEASIBLE,
        hard_limit=baseline_input.budget_constraint.limit,
        known_total=models.Money(amount=Decimal("0"), currency="USD"),
        required_action_ids=(unknown_fee.action_id,),
        included_fee_ids=(unknown_fee.fee_id,),
    ).model_dump()
    with pytest.raises(ValueError, match="required-now fee facts"):
        validate_plan_against_input(false_feasible_payload, hard_unknown_input)

    blocking_soft_payload = deepcopy(plan_payload)
    blocking_soft_payload["verification_gates"] = [{
        "id": "verify-soft-budget-fee",
        "fact_key": "fee.fee-soft-unknown",
        "affected_decision": "budget:primary_launch",
        "blocking": True,
        "source_refs": [],
    }]
    with pytest.raises(ValidationError, match="actual hard budget"):
        validate_plan_against_input(blocking_soft_payload, soft_unknown_input)


def test_campaign_plan_cardinality_grounding_and_budget_validation() -> None:
    plan = _boundary_bundle()["CampaignPlan"]
    payload = plan.model_dump(by_alias=True)
    payload["alternative_launches"] = list(payload["alternative_launches"])
    payload["alternative_launches"].append(
        {"festival_id": "docaviv", "activates_on": "primary_rejected_or_withdrawn"}
    )
    payload["alternative_launches"].append(
        {"festival_id": "sheffield-docfest", "activates_on": "primary_rejected_or_withdrawn"}
    )
    with pytest.raises(ValidationError):
        models.CampaignPlan.model_validate(payload)

    payload = plan.model_dump(by_alias=True)
    payload["rejection_branch"]["promote_festival_id"] = "docaviv"
    with pytest.raises(ValidationError, match="grounded alternative"):
        models.CampaignPlan.model_validate(payload)

    with pytest.raises(ValidationError):
        models.BudgetAssessment(
            state="KNOWN_FEASIBLE",
            hard_limit={"amount": "100", "currency": "USD"},
            known_total={"amount": "80", "currency": "USD"},
            unknown_fee_ids=("fee-unknown",),
        )
    verify_budget = models.BudgetAssessment(
        state="VERIFY",
        hard_limit={"amount": "100", "currency": "USD"},
        known_total={"amount": "80", "currency": "USD"},
        unknown_fee_ids=("fee-unknown",),
    )
    verify_payload = plan.model_dump(by_alias=True)
    verify_payload["budget"] = verify_budget.model_dump()
    with pytest.raises(ValidationError, match="blocking budget gate"):
        models.CampaignPlan.model_validate(verify_payload)
    verify_payload["verification_gates"] = [{
        "id": "verify-budget-fee",
        "fact_key": "fee.fee-unknown",
        "affected_decision": "budget:primary_launch",
        "blocking": True,
        "source_refs": [],
    }]
    assert models.CampaignPlan.model_validate(verify_payload).budget.state == models.HardBudgetState.VERIFY
    infeasible_payload = plan.model_dump(by_alias=True)
    infeasible_payload["budget"] = models.BudgetAssessment(
        state="KNOWN_INFEASIBLE",
        hard_limit={"amount": "100", "currency": "USD"},
        known_total={"amount": "101", "currency": "USD"},
    ).model_dump()
    with pytest.raises(ValidationError, match="cannot be KNOWN_INFEASIBLE"):
        models.CampaignPlan.model_validate(infeasible_payload)

    diagnostic_payload = plan.selection_diagnostics[0].model_dump()
    diagnostic_payload["budget_assessment"] = infeasible_payload["budget"]
    with pytest.raises(ValidationError, match="must be hard-filtered"):
        models.PlannerCandidateDiagnostic.model_validate(diagnostic_payload)
    diagnostic_payload["hard_filter"]["feasible"] = False
    diagnostic_payload["hard_filter"]["reason_codes"] = ["hard_budget_known_infeasible"]
    assert not models.PlannerCandidateDiagnostic.model_validate(
        diagnostic_payload
    ).hard_filter.feasible


def _bucket_candidate(score: int, **updates: Any) -> dict[str, Any]:
    candidate = {
        "score": score,
        "premiere_risk": "none",
        "deadline_status": "open",
        "eligible": True,
        "premiere_opportunity": False,
        "ratings": {"company_relationship": 0},
        "tier": "B",
    }
    candidate.update(updates)
    return candidate


def test_baseline_decision_grade_source_values_and_every_threshold_boundary() -> None:
    assert DECISION_GRADE_SOURCE == "app.agent.scoring.assign_bucket"
    assert DECISION_GRADE_IS_LLM_LABEL is False
    assert len(DECISION_GRADE_RULES) == 7
    assert {item.value for item in models.DecisionGrade} == {
        "submit_first", "prioritize_next", "leverage", "hold_avoid"
    }
    assert scoring.assign_bucket(_bucket_candidate(44)) == "hold_avoid"
    assert scoring.assign_bucket(_bucket_candidate(45)) == "prioritize_next"
    assert scoring.assign_bucket(_bucket_candidate(54, ratings={"company_relationship": 4})) == "prioritize_next"
    assert scoring.assign_bucket(_bucket_candidate(55, ratings={"company_relationship": 4})) == "leverage"
    assert scoring.assign_bucket(_bucket_candidate(64, tier="A")) == "prioritize_next"
    assert scoring.assign_bucket(_bucket_candidate(65, tier="A")) == "submit_first"
    assert scoring.assign_bucket(_bucket_candidate(71)) == "prioritize_next"
    assert scoring.assign_bucket(_bucket_candidate(72)) == "submit_first"
    assert scoring.assign_bucket(_bucket_candidate(69, deadline_status="closed")) == "hold_avoid"
    assert scoring.assign_bucket(_bucket_candidate(70, deadline_status="closed")) == "prioritize_next"
    assert scoring.assign_bucket(_bucket_candidate(80, deadline_status="upcoming", deadline={"days_until_open": 42})) == "submit_first"
    assert scoring.assign_bucket(_bucket_candidate(80, deadline_status="upcoming", deadline={"days_until_open": 43})) == "prioritize_next"
    assert scoring.assign_bucket(_bucket_candidate(80, premiere_risk="high")) == "hold_avoid"
    assert scoring.assign_bucket(_bucket_candidate(80, premiere_risk="high", premiere_opportunity=True)) == "submit_first"


def _dominates(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_burden = (left["blocking_gates"], left["verify_quality_pct"], left["total_gates"])
    right_burden = (right["blocking_gates"], right["verify_quality_pct"], right["total_gates"])
    no_worse = (
        left["score"] >= right["score"]
        and left["known_preserved_pct"] >= right["known_preserved_pct"]
        and left_burden <= right_burden
    )
    strict = (
        left["score"] > right["score"]
        or left["known_preserved_pct"] > right["known_preserved_pct"]
        or left_burden < right_burden
    )
    return no_worse and strict


def _weighted_preservation(
    planning: models.PlanningInput, root_id: str
) -> tuple[dict[models.CompatibilityStatus, Decimal], dict[models.CompatibilityStatus, tuple[str, ...]]]:
    candidate_quality = {
        candidate.festival_id: candidate.future_quality
        for candidate in planning.candidates
    }
    nonterminal = {
        opportunity.festival_id
        for opportunity in planning.opportunities
        if opportunity.submission_state
        not in {models.SubmissionState.REJECTED, models.SubmissionState.WITHDRAWN}
    }
    downstream_ids = tuple(
        festival_id
        for festival_id in planning.candidate_ids
        if festival_id != root_id and festival_id in nonterminal
    )
    edge_by_target = {
        edge.to_festival_id: edge.status
        for edge in planning.compatibility_edges
        if edge.from_festival_id == root_id
    }
    ids_by_status = {
        status: tuple(
            festival_id
            for festival_id in downstream_ids
            if edge_by_target[festival_id] == status
        )
        for status in models.CompatibilityStatus
    }
    total_quality = sum(
        (candidate_quality[festival_id] for festival_id in downstream_ids),
        Decimal("0"),
    )
    if not downstream_ids:
        percentages = {
            models.CompatibilityStatus.COMPATIBLE: Decimal("100"),
            models.CompatibilityStatus.VERIFY: Decimal("0"),
            models.CompatibilityStatus.INCOMPATIBLE: Decimal("0"),
        }
    else:
        percentages = {
            status: (
                Decimal("100")
                * sum(
                    (candidate_quality[festival_id] for festival_id in ids_by_status[status]),
                    Decimal("0"),
                )
                / total_quality
            ).quantize(models.FUTURE_QUALITY_QUANTUM)
            for status in models.CompatibilityStatus
        }
    return percentages, ids_by_status


def test_archetypes_a_to_e_are_typed_consistent_and_freeze_all_six_invariants() -> None:
    fixture = json.loads((FIXTURES / "planner_archetypes.json").read_text())
    exact_models = json.loads((FIXTURES / "planner_archetype_models.json").read_text())
    assert len(fixture["cases"]) == 5
    assert len(fixture["invariants"]) == 6
    expected = {
        "A-option-destruction": ("idfa", "idfa", "hot-docs"),
        "B-immediate-dominates": ("hot-docs", "sheffield-docfest", "hot-docs"),
        "C-verification-burden": ("idfa", "idfa", "idfa"),
        "D-hard-preservation": ("idfa", "idfa", "idfa"),
        "E-no-tradeoff": ("hot-docs", "hot-docs", "hot-docs"),
    }
    for case in fixture["cases"]:
        planning = _planning_input_from_specs(case["candidates"], case["case_id"])
        policy = _policy_result(case)
        assert planning.model_dump(mode="json", by_alias=True) == exact_models[case["case_id"]]["planning_input"]
        assert policy.model_dump(mode="json", by_alias=True) == exact_models[case["case_id"]]["expected_policy_result"]
        assert models.PlanningInput.model_validate(
            exact_models[case["case_id"]]["planning_input"],
            context={"known_festival_ids": KNOWN_IDS},
        ) == planning
        assert models.ExpectedPolicyResult.model_validate(
            exact_models[case["case_id"]]["expected_policy_result"],
            context={"known_festival_ids": KNOWN_IDS},
        ) == policy
        assert isinstance(planning, models.PlanningInput)
        assert planning.planning_input_hash == planning_input_hash(planning)
        assert policy.result_hash == policy_result_hash(policy)
        assert tuple(item.selected_festival_id for item in policy.mode_selections) == expected[case["case_id"]]
        feasible = [item for item in case["candidates"] if not item.get("filtered", False)]
        computed_frontier = [
            item["festival_id"]
            for item in feasible
            if not any(_dominates(other, item) for other in feasible if other is not item)
        ]
        assert computed_frontier == case["expected_frontier"]
        for item in case["candidates"]:
            actual_grade = scoring.assign_bucket(_bucket_candidate(item["score"], tier="B"))
            assert actual_grade == item["grade"]
        for source, diagnostic in zip(case["candidates"], policy.diagnostics, strict=True):
            percentages, ids_by_status = _weighted_preservation(
                planning, source["festival_id"]
            )
            assert diagnostic.preservation.known_preserved_pct == percentages[
                models.CompatibilityStatus.COMPATIBLE
            ]
            assert diagnostic.preservation.possible_additional_pct == percentages[
                models.CompatibilityStatus.VERIFY
            ]
            assert diagnostic.preservation.known_destroyed_pct == percentages[
                models.CompatibilityStatus.INCOMPATIBLE
            ]
            assert diagnostic.preservation.preserved_ids == ids_by_status[
                models.CompatibilityStatus.COMPATIBLE
            ]
            assert diagnostic.preservation.verify_ids == ids_by_status[
                models.CompatibilityStatus.VERIFY
            ]
            assert diagnostic.preservation.destroyed_ids == ids_by_status[
                models.CompatibilityStatus.INCOMPATIBLE
            ]
            assert diagnostic.verification_burden.verify_edge_quality_pct == percentages[
                models.CompatibilityStatus.VERIFY
            ]
            assert not set(diagnostic.preservation.verify_ids) & set(
                diagnostic.preservation.preserved_ids
            )
            assert not set(diagnostic.preservation.destroyed_ids) & set(
                diagnostic.preservation.preserved_ids
            )

    case_a = fixture["cases"][0]
    assert case_a["naive"] == "hot-docs"
    case_b = fixture["cases"][1]
    assert case_b["candidates"][0]["grade"] == "submit_first"
    assert case_b["candidates"][1]["grade"] == "prioritize_next"


def test_clarification_golden_contracts_cover_blocking_suppression_and_lower_bound() -> None:
    fixture = json.loads((FIXTURES / "clarifications.json").read_text())
    cases = {
        item["case_id"]: models.Clarification.model_validate(
            {key: value for key, value in item.items() if key != "case_id"}
        )
        for item in fixture["cases"]
    }
    assert cases["premiere-contradiction"].blocking
    assert cases["premiere-contradiction"].contradiction
    assert cases["composer-suppressed"].suppressed
    assert cases["missing-premiere-changes-primary"].impact == models.ClarificationImpact.CHANGES_PRIMARY
    assert not cases["later-gate-does-not-block-campaign"].blocking
    assert cases["unknown-required-fee-blocks"].blocking
    assert cases["format-country-lower-bound"].candidate_set_lower_bound


def test_reuse_manifest_golden_a_b_c_and_zero_call_fixture() -> None:
    fixture = json.loads((FIXTURES / "reuse_manifests.json").read_text())
    keys = _boundary_bundle()["PlanningInput"].artifact_keys
    manifests: dict[str, models.ReuseManifest] = {}
    for case in fixture["cases"]:
        assert parse_campaign_command(case["command"], KNOWN_IDS).invalidation_class.value == case["invalidation_class"]
        manifests[case["case_id"]] = models.ReuseManifest(
            schema_version=1,
            invalidation_class=case["invalidation_class"],
            prior_artifact_keys=keys,
            current_artifact_keys=keys,
            reused_artifacts=tuple(case["reused_artifacts"]),
            rerun_artifacts=tuple(case["rerun_artifacts"]),
            invalidated_artifacts=tuple(case["invalidated_artifacts"]),
            reasons=tuple(case["reasons"]),
            chat_attempts=case["chat_attempts"],
            embedding_attempts=case["embedding_attempts"],
        )
    rejection = manifests["hot-docs-rejection"]
    assert rejection.invalidation_class == models.InvalidationClass.C
    rejection_case = fixture["cases"][0]
    assert rejection_case["before"]["submission_state"] == "submitted"
    assert rejection_case["after"]["submission_state"] == "rejected"
    assert rejection_case["before"]["current"] is True
    assert rejection_case["after"]["current"] is False
    assert rejection.chat_attempts == rejection.embedding_attempts == 0
    assert models.ArtifactName.COMPATIBILITY_GRAPH in rejection.reused_artifacts
    screening = manifests["confirmed-public-screening"]
    assert screening.invalidation_class == models.InvalidationClass.B
    assert models.ArtifactName.RETRIEVAL in screening.reused_artifacts
    assert models.ArtifactName.PREMIERE_LEDGER in screening.invalidated_artifacts
    assert models.ArtifactName.PLANNER in screening.invalidated_artifacts
    assert models.ArtifactName.CLARIFICATION in screening.invalidated_artifacts
    identity = manifests["identity-change"]
    assert identity.invalidation_class == models.InvalidationClass.A
    assert models.ArtifactName.RETRIEVAL in identity.invalidated_artifacts
    assert models.ArtifactName.CREATIVE_EVIDENCE in identity.invalidated_artifacts


def test_future_api_request_response_fixtures_validate_without_routes() -> None:
    fixture = json.loads((FIXTURES / "api_contracts.json").read_text())
    boundary_json = json.loads((FIXTURES / "boundary_models.json").read_text())
    assert models.BootstrapResponse.model_validate(fixture["bootstrap"]["response"])
    assert models.CampaignCreationRequest.model_validate(fixture["campaign_creation"]["request"])
    assert models.CampaignCreationResponse.model_validate(fixture["campaign_creation"]["response"])
    assert parse_campaign_command(fixture["command"]["request"], KNOWN_IDS)
    assert models.CommandResponse.model_validate(fixture["command"]["response"])
    assert models.ReplanRequest.model_validate(fixture["replan"]["request"])
    assert models.ReplanResponse.model_validate(fixture["replan"]["response"])
    assert models.SimulateRequest.model_validate(
        fixture["simulate"]["request"], context={"known_festival_ids": KNOWN_IDS}
    )
    assert models.SimulateResponse.model_validate(fixture["simulate"]["response"])
    assert models.StrategyHistoryResponse.model_validate(
        fixture["strategy_history_get"]["response"]
    )
    assert models.ApiError.model_validate(fixture["conflict_409"]["response"])
    assert fixture["conflict_409"]["status"] == 409
    assert models.ApiError.model_validate(fixture["validation_error"]["response"])
    refs = fixture["campaign_get_aggregate"]["response_fixture_refs"]
    resolved = {
        key: boundary_json[ref.split("#/")[1]]
        for key, ref in refs.items()
    }
    aggregate = models.CampaignAggregateResponse.model_validate(
        resolved,
        context={"known_festival_ids": KNOWN_IDS},
    )
    assert set(aggregate.model_dump()) == {"snapshot", "active_plan", "latest_diff"}


def test_capability_contract_rng_digest_cookie_and_trace_rules() -> None:
    fixture = json.loads((FIXTURES / "capability_contract.json").read_text())
    contract = models.CapabilityContract.model_validate(fixture)
    assert contract.entropy_bits == 256
    with patch("app.campaign.contracts.secrets.token_bytes", return_value=bytes(range(32))) as rng:
        raw = generate_raw_capability()
    rng.assert_called_once_with(32)
    assert len(raw) == 43
    assert set(raw) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert capability_digest(raw) == hashlib.sha256(raw.encode("ascii")).hexdigest()
    serialized = canonical_json(contract)
    assert raw not in serialized
    assert contract.extra_hmac_secret is False
    assert contract.campaign_id_authorizes_access is False
    malformed = {**fixture, "exact_origin_allowlist": ["*"]}
    with pytest.raises(ValidationError, match="exact HTTP"):
        models.CapabilityContract.model_validate(malformed)


def test_existing_execute_contract_regression_is_explicit_and_untouched() -> None:
    source = (Path(__file__).parent.parent / "api" / "index.py").read_text()
    assert "The response always carries exactly the four contract fields" in source
    assert "status,\n    error, response and steps" in source
    assert set(json.loads('{"status":"ok","error":null,"response":"x","steps":[]}')) == {
        "status", "error", "response", "steps"
    }
