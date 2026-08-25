"""Focused deterministic tests for Campaign Workspace Phase 1B."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.agent.scoring import assign_bucket
from app.campaign.clarification import ClarificationEngine
from app.campaign.compatibility import CompatibilityBuilder
from app.campaign.contracts import (
    campaign_plan_hash,
    compatibility_edge_hash,
    planning_input_hash,
)
from app.campaign.models import (
    BudgetConstraint,
    CompatibilityStatus,
    FactStatus,
    FeeActionScope,
    FeeFact,
    HardBudgetState,
    Money,
    OpportunityPolicyState,
    PlanningInput,
    PremiereAvailability,
    PremiereScope,
    PreservationMode,
    RequiredFee,
    SubmissionState,
    VerificationItem,
)
from app.campaign.planning import CampaignPlanner, assess_budget, future_quality
from app.campaign.validation import StrategyValidationError, StrategyValidator


FIXTURES = Path(__file__).parent / "fixtures" / "campaign"
KNOWN_IDS = frozenset(json.loads((FIXTURES / "known_festival_ids.json").read_text()))
ARCHETYPE_MODELS = json.loads((FIXTURES / "planner_archetype_models.json").read_text())
ARCHETYPES = {
    item["case_id"]: item
    for item in json.loads((FIXTURES / "planner_archetypes.json").read_text())["cases"]
}
OBSERVED_AT = datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)


def _input(case_id: str) -> PlanningInput:
    return PlanningInput.model_validate(
        ARCHETYPE_MODELS[case_id]["planning_input"],
        context={"known_festival_ids": KNOWN_IDS},
    )


def _updated(planning_input: PlanningInput, **updates: Any) -> PlanningInput:
    changed = planning_input.model_copy(update=updates)
    return changed.model_copy(update={"planning_input_hash": planning_input_hash(changed)})


def _mode(planning_input: PlanningInput, mode: PreservationMode) -> PlanningInput:
    return _updated(planning_input, preservation_mode=mode)


def _unknown_fee(festival_id: str, *, fee_id: str = "fee-unknown") -> RequiredFee:
    return RequiredFee(
        fee_id=fee_id,
        action_id=f"submit-{festival_id}",
        festival_id=festival_id,
        action_scope=FeeActionScope.CURRENT_ROOT,
        required_now=True,
        fee=FeeFact(status=FactStatus.UNKNOWN, observed_at=OBSERVED_AT),
    )


def _known_fee(
    festival_id: str,
    amount: str,
    *,
    fee_id: str = "fee-known",
    scope: FeeActionScope = FeeActionScope.CURRENT_ROOT,
    required_now: bool = True,
) -> RequiredFee:
    return RequiredFee(
        fee_id=fee_id,
        action_id=f"action-{fee_id}",
        festival_id=festival_id,
        action_scope=scope,
        required_now=required_now,
        fee=FeeFact(
            amount=Decimal(amount),
            currency="USD",
            status=FactStatus.CONFIRMED,
            source_refs=(f"fixture:{fee_id}",),
            observed_at=OBSERVED_AT,
        ),
    )


def test_compatibility_builder_is_directed_complete_and_evidence_grounded() -> None:
    planning_input = _input("A-option-destruction")
    hot_docs, idfa = planning_input.candidates

    domestic_country = hot_docs.retrieved.identity.country.model_copy(
        update={
            "value": planning_input.profile.country.value,
            "status": FactStatus.CONFIRMED,
            "source_refs": ("fixture:domestic-country",),
        }
    )
    domestic_identity = hot_docs.retrieved.identity.model_copy(
        update={"country": domestic_country}
    )
    domestic_retrieved = hot_docs.retrieved.model_copy(
        update={"identity": domestic_identity}
    )
    domestic_hot_docs = hot_docs.model_copy(update={"retrieved": domestic_retrieved})

    international_constraint = idfa.risk.premiere_constraint.model_copy(
        update={"scope": PremiereScope.INTERNATIONAL, "territory": "International"}
    )
    international_risk = idfa.risk.model_copy(
        update={"premiere_constraint": international_constraint}
    )
    international_idfa = idfa.model_copy(update={"risk": international_risk})

    edges = CompatibilityBuilder().build(
        (international_idfa, domestic_hot_docs),
        planning_input.profile,
        planning_input.premiere_ledger,
    )
    assert len(edges) == 2
    assert [(edge.from_festival_id, edge.to_festival_id) for edge in edges] == [
        ("hot-docs", "idfa"),
        ("idfa", "hot-docs"),
    ]
    by_pair = {
        (edge.from_festival_id, edge.to_festival_id): edge for edge in edges
    }
    assert by_pair[("hot-docs", "idfa")].status == CompatibilityStatus.COMPATIBLE
    assert by_pair[("idfa", "hot-docs")].status == CompatibilityStatus.INCOMPATIBLE
    for edge in edges:
        assert edge.scope != PremiereScope.UNKNOWN
        assert edge.reason_code and edge.evidence_refs
        assert edge.edge_hash == compatibility_edge_hash(edge)


def test_compatibility_unknown_rule_is_verify_never_compatible() -> None:
    planning_input = _input("A-option-destruction")
    hot_docs, idfa = planning_input.candidates
    unknown_constraint = idfa.risk.premiere_constraint.model_copy(
        update={"rule_status": FactStatus.UNKNOWN}
    )
    unknown_idfa = idfa.model_copy(
        update={
            "risk": idfa.risk.model_copy(
                update={"premiere_constraint": unknown_constraint}
            )
        }
    )
    edges = CompatibilityBuilder().build(
        (hot_docs, unknown_idfa),
        planning_input.profile,
        planning_input.premiere_ledger,
    )
    edge = next(
        item
        for item in edges
        if item.from_festival_id == "hot-docs" and item.to_festival_id == "idfa"
    )
    assert edge.status == CompatibilityStatus.VERIFY
    assert edge.source_confidence == FactStatus.UNKNOWN


def test_future_quality_uses_only_the_exact_five_enduring_dimensions() -> None:
    candidate = _input("A-option-destruction").candidates[0]
    expected = Decimal(100) * Decimal(75) / Decimal(90)
    assert future_quality(candidate) == expected
    assert future_quality(candidate) != candidate.future_quality
    changed_dimensions = tuple(
        dimension.model_copy(update={"points": Decimal("999")})
        if dimension.dimension == "deadline_urgency"
        else dimension
        for dimension in candidate.score_breakdown.dimensions
    )
    changed = candidate.model_copy(
        update={
            "score_breakdown": candidate.score_breakdown.model_copy(
                update={
                    "dimensions": changed_dimensions,
                    "premiere_penalty": Decimal("99"),
                }
            )
        }
    )
    assert future_quality(changed) == expected


@pytest.mark.parametrize(
    ("case_id", "balanced", "strict", "opportunistic"),
    [
        ("A-option-destruction", "idfa", "idfa", "hot-docs"),
        ("B-immediate-dominates", "hot-docs", "sheffield-docfest", "hot-docs"),
        ("C-verification-burden", "idfa", "idfa", "idfa"),
        ("D-hard-preservation", "idfa", "idfa", "idfa"),
        ("E-no-tradeoff", "hot-docs", "hot-docs", "hot-docs"),
    ],
)
def test_planner_archetypes_a_to_e(
    case_id: str,
    balanced: str,
    strict: str,
    opportunistic: str,
) -> None:
    expected = {
        PreservationMode.BALANCED: balanced,
        PreservationMode.STRICT: strict,
        PreservationMode.OPPORTUNISTIC: opportunistic,
    }
    planning_input = _input(case_id)
    for mode, expected_id in expected.items():
        plan = CampaignPlanner().plan(_mode(planning_input, mode))
        assert plan.primary_launch.festival_id == expected_id
        assert len(plan.alternative_launches) <= 2
        assert all(
            diagnostic.decision_grade.value
            in {"submit_first", "prioritize_next", "leverage", "hold_avoid"}
            for diagnostic in plan.selection_diagnostics
        )


def test_pareto_dominated_root_is_never_primary_or_alternative() -> None:
    plan = CampaignPlanner().plan(_input("C-verification-burden"))
    selected = {
        plan.primary_launch.festival_id,
        *(item.festival_id for item in plan.alternative_launches),
    }
    hot_docs = next(
        item for item in plan.selection_diagnostics if item.festival_id == "hot-docs"
    )
    assert hot_docs.on_pareto_frontier is False
    assert "hot-docs" not in selected
    assert hot_docs.preservation.preserved_ids == ()
    assert hot_docs.preservation.verify_ids == ("idfa",)
    assert hot_docs.preservation.known_preserved_pct == 0
    assert hot_docs.preservation.possible_additional_pct == 100


def test_equal_preservation_and_verification_preserves_immediate_ordering() -> None:
    plan = CampaignPlanner().plan(_input("E-no-tradeoff"))
    assert plan.primary_launch.festival_id == "hot-docs"
    assert plan.option_preservation.known_preserved_pct == 100


def test_stable_festival_id_breaks_a_complete_tie() -> None:
    planning_input = _input("E-no-tradeoff")
    candidates = list(planning_input.candidates)
    idfa = candidates[1]
    candidates[1] = idfa.model_copy(
        update={
            "score_breakdown": idfa.score_breakdown.model_copy(update={"score": 88})
        }
    )
    tied = _updated(planning_input, candidates=tuple(candidates))
    assert CampaignPlanner().plan(tied).primary_launch.festival_id == "hot-docs"


def test_hard_preservation_filter_and_terminal_nodes_remain_diagnostic() -> None:
    plan = CampaignPlanner().plan(_input("D-hard-preservation"))
    filtered = next(
        item for item in plan.selection_diagnostics if item.festival_id == "hot-docs"
    )
    assert not filtered.hard_filter.feasible
    assert filtered.hard_filter.reason_codes == ("violates_preserve_world_premiere",)
    assert filtered.hard_filter.constraint_refs == ("preserve-world",)

    planning_input = _input("A-option-destruction")
    opportunities = list(planning_input.opportunities)
    opportunities[0] = opportunities[0].model_copy(
        update={"submission_state": SubmissionState.REJECTED}
    )
    replanned = CampaignPlanner().plan(_updated(planning_input, opportunities=tuple(opportunities)))
    rejected = next(
        item for item in replanned.selection_diagnostics if item.festival_id == "hot-docs"
    )
    assert not rejected.hard_filter.feasible
    assert "opportunity_rejected" in rejected.hard_filter.reason_codes


def test_budget_tri_state_and_required_now_branch_scope() -> None:
    base = _input("A-option-destruction")
    hard_100 = BudgetConstraint(
        constraint_id="hard-budget",
        limit=Money(amount=Decimal("100"), currency="USD"),
        hard=True,
    )
    inactive = _known_fee(
        "hot-docs",
        "999",
        fee_id="fee-rejection-only",
        scope=FeeActionScope.REJECTION_ALTERNATIVE,
        required_now=False,
    )
    feasible = _updated(
        base,
        budget_constraint=hard_100,
        required_fees=(_known_fee("hot-docs", "100"), inactive),
    )
    assessment = assess_budget(feasible, "hot-docs")
    assert assessment.state == HardBudgetState.KNOWN_FEASIBLE
    assert assessment.known_total.amount == 100
    assert assessment.included_fee_ids == ("fee-known",)

    infeasible = _updated(
        base,
        budget_constraint=hard_100,
        required_fees=(_known_fee("hot-docs", "101"), inactive),
    )
    assert assess_budget(infeasible, "hot-docs").state == HardBudgetState.KNOWN_INFEASIBLE

    verify = _updated(
        base,
        budget_constraint=hard_100,
        required_fees=(_known_fee("hot-docs", "80"), _unknown_fee("hot-docs"), inactive),
    )
    verify_assessment = assess_budget(verify, "hot-docs")
    assert verify_assessment.state == HardBudgetState.VERIFY
    assert verify_assessment.known_total.amount == 80
    assert verify_assessment.unknown_fee_ids == ("fee-unknown",)
    assert "fee-rejection-only" not in verify_assessment.included_fee_ids


def test_unknown_required_hard_budget_fee_is_a_blocking_gate_and_clarification() -> None:
    base = _input("A-option-destruction")
    opportunities = list(base.opportunities)
    opportunities[0] = opportunities[0].model_copy(
        update={"policy_state": OpportunityPolicyState.LOCKED}
    )
    planning_input = _updated(
        base,
        opportunities=tuple(opportunities),
        required_fees=(_unknown_fee("hot-docs"),),
    )
    plan = CampaignPlanner().plan(planning_input)
    assert plan.primary_launch.festival_id == "hot-docs"
    assert plan.budget.state == HardBudgetState.VERIFY
    assert any(
        gate.blocking and gate.affected_decision == "budget:primary_launch"
        for gate in plan.verification_gates
    )
    clarification = next(
        item for item in plan.clarifications if item.fact_key == "fee.hot-docs"
    )
    assert clarification.blocking
    assert clarification.impact.value == "blocking_hard_decision"
    assert "exact current Hot Docs submission fee" in (clarification.question or "")


def test_premiere_contradiction_outranks_suppressed_composer() -> None:
    base = _input("A-option-destruction")
    scopes = list(base.premiere_ledger.scopes)
    scopes[0] = scopes[0].model_copy(
        update={
            "availability": PremiereAvailability.UNKNOWN,
            "contradiction": True,
            "reason_code": "fixture-contradiction",
            "evidence_refs": ("fixture:screened", "fixture:unscreened"),
        }
    )
    ledger = base.premiere_ledger.model_copy(update={"scopes": tuple(scopes)})
    planning_input = _updated(base, premiere_ledger=ledger)
    plan = CampaignPlanner().plan(planning_input)
    audit = ClarificationEngine().generate(
        planning_input, plan, include_suppressed=True
    )
    assert audit[0].clarification_id == "clarify-premiere-contradiction"
    composer = next(item for item in audit if item.fact_key == "credits.composer")
    assert composer.suppressed
    assert composer.suppression_reason == "no_current_plan_dependency"


def test_premiere_question_is_suppressed_when_current_plan_is_independent() -> None:
    base = _input("E-no-tradeoff")
    plan = CampaignPlanner().plan(base)
    candidates = tuple(
        candidate.model_copy(
            update={
                "risk": candidate.risk.model_copy(
                    update={
                        "premiere_constraint": candidate.risk.premiere_constraint.model_copy(
                            update={"scope": PremiereScope.NONE, "territory": None}
                        )
                    }
                )
            }
        )
        for candidate in base.candidates
    )
    scopes = list(base.premiere_ledger.scopes)
    scopes[0] = scopes[0].model_copy(
        update={
            "availability": PremiereAvailability.UNKNOWN,
            "contradiction": True,
            "reason_code": "fixture-irrelevant-contradiction",
            "evidence_refs": ("fixture:a", "fixture:b"),
        }
    )
    independent = base.model_copy(
        update={
            "candidates": candidates,
            "premiere_ledger": base.premiere_ledger.model_copy(
                update={"scopes": tuple(scopes)}
            ),
        }
    )
    clarifications = ClarificationEngine().generate(independent, plan)
    assert not any(item.fact_key == "premiere.world" for item in clarifications)


def test_later_uncertainty_does_not_block_plan_and_identity_is_lower_bound() -> None:
    base = _mode(_input("A-option-destruction"), PreservationMode.OPPORTUNISTIC)
    opportunities = list(base.opportunities)
    opportunities[0] = opportunities[0].model_copy(
        update={
            "verification_items": (
                VerificationItem(
                    item_id="future-access",
                    fact_key="screening.future.access",
                    status=FactStatus.UNKNOWN,
                    blocking=False,
                ),
            )
        }
    )
    planning_input = _updated(base, opportunities=tuple(opportunities))
    plan = CampaignPlanner().plan(planning_input)
    later = next(
        item for item in plan.clarifications if item.fact_key == "screening.future.access"
    )
    assert later.blocking is False
    assert later.affected_decisions == ("post_premiere_gate",)

    unknown_format = planning_input.profile.format.model_copy(
        update={
            "value": None,
            "status": FactStatus.UNKNOWN,
            "source_refs": (),
        }
    )
    profile = planning_input.profile.model_copy(update={"format": unknown_format})
    identity_input = planning_input.model_copy(update={"profile": profile})
    clarifications = ClarificationEngine().generate(identity_input, plan)
    identity = next(
        item for item in clarifications if item.fact_key == "identity.format_country"
    )
    assert identity.candidate_set_lower_bound
    assert identity.blocking is False


def test_identical_frozen_input_produces_identical_plan_and_hash() -> None:
    planning_input = _input("A-option-destruction")
    first = CampaignPlanner().plan(planning_input)
    second = CampaignPlanner().plan(planning_input)
    assert first == second
    assert first.plan_hash == second.plan_hash == campaign_plan_hash(first)


def test_strategy_validator_rejects_unsupported_post_route_and_fabrication() -> None:
    planning_input = _mode(
        _input("A-option-destruction"), PreservationMode.OPPORTUNISTIC
    )
    plan = CampaignPlanner().plan(planning_input)
    assert plan.primary_launch.festival_id == "hot-docs"
    screened = plan.screened_branch.model_copy(
        update={"post_premiere_opportunity_ids": ("idfa",)}
    )
    incompatible = plan.model_copy(
        update={
            "post_premiere_opportunities": ("idfa",),
            "screened_branch": screened,
        }
    )
    incompatible = incompatible.model_copy(
        update={"plan_hash": campaign_plan_hash(incompatible)}
    )
    with pytest.raises(StrategyValidationError, match="incompatible"):
        StrategyValidator().validate(incompatible, planning_input)

    primary = plan.primary_launch.model_copy(
        update={
            "submission_action": (
                "The film will be accepted with 80% probability on 2030-01-01."
            )
        }
    )
    fabricated = plan.model_copy(update={"primary_launch": primary})
    with pytest.raises(StrategyValidationError) as exc_info:
        StrategyValidator().validate(fabricated, planning_input)
    assert "probability" in str(exc_info.value)
    assert "future outcome" in str(exc_info.value)
    assert "ungrounded date" in str(exc_info.value)


@pytest.mark.parametrize(
    ("score", "updates", "expected"),
    [
        (44, {}, "hold_avoid"),
        (45, {}, "prioritize_next"),
        (54, {"ratings": {"company_relationship": 4}}, "prioritize_next"),
        (55, {"ratings": {"company_relationship": 4}}, "leverage"),
        (64, {"tier": "A"}, "prioritize_next"),
        (65, {"tier": "A"}, "submit_first"),
        (71, {}, "prioritize_next"),
        (72, {}, "submit_first"),
        (69, {"deadline_status": "closed"}, "hold_avoid"),
        (70, {"deadline_status": "closed"}, "prioritize_next"),
        (80, {"deadline_status": "upcoming", "deadline": {"days_until_open": 42}}, "submit_first"),
        (80, {"deadline_status": "upcoming", "deadline": {"days_until_open": 43}}, "prioritize_next"),
        (80, {"premiere_risk": "high"}, "hold_avoid"),
        (80, {"premiere_risk": "high", "premiere_opportunity": True}, "submit_first"),
    ],
)
def test_every_assign_bucket_boundary(
    score: int,
    updates: dict[str, Any],
    expected: str,
) -> None:
    candidate: dict[str, Any] = {
        "score": score,
        "premiere_risk": "none",
        "deadline_status": "open",
        "eligible": True,
        "premiere_opportunity": False,
        "ratings": {"company_relationship": 0},
        "tier": "B",
    }
    candidate.update(updates)
    assert assign_bucket(candidate) == expected


def test_planner_rejects_raw_legacy_dictionary_input() -> None:
    with pytest.raises(TypeError, match="only the frozen PlanningInput"):
        CampaignPlanner().plan({"ranked_festivals": []})  # type: ignore[arg-type]


def test_strategy_validator_rejects_plan_hash_tampering() -> None:
    planning_input = _input("E-no-tradeoff")
    plan = CampaignPlanner().plan(planning_input)
    tampered = plan.model_copy(update={"plan_hash": "f" * 64})
    with pytest.raises(StrategyValidationError, match="hash"):
        StrategyValidator().validate(tampered, planning_input)
