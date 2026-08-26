"""Offline deterministic evaluation for Campaign Workspace.

The runner imports no provider client and performs no database or vector-store
write.  It evaluates frozen planner archetypes, budget/premiere invariants,
incremental replanning, correction behavior, capability isolation, and scenario
discard semantics entirely in memory.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.campaign.adapter import AdaptedCampaignEvidence  # noqa: E402
from app.campaign.contracts import (  # noqa: E402
    campaign_plan_hash,
    parse_campaign_command,
    planning_input_hash,
)
from app.campaign.models import (  # noqa: E402
    BudgetConstraint,
    CampaignProfile,
    CampaignSnapshot,
    ExpectedPolicyResult,
    FactStatus,
    FeeActionScope,
    FeeFact,
    FrozenCandidateEvidence,
    HardBudgetState,
    Money,
    PlanningInput,
    PremiereAvailability,
    PremiereScope,
    PreservationMode,
    RequiredFee,
    RetrievalInput,
)
from app.campaign.orchestration import (  # noqa: E402
    CampaignOrchestrator,
    snapshot_from_evidence,
)
from app.campaign.planning import CampaignPlanner, assess_budget  # noqa: E402
from app.campaign.repository import (  # noqa: E402
    CampaignNotFound,
    InMemoryCampaignRepository,
)
from app.campaign.scenarios import CampaignScenarioEngine  # noqa: E402
from app.campaign.validation import StrategyValidator  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "campaign"
ARCHETYPES = json.loads((FIXTURES / "planner_archetype_models.json").read_text())
MODELS = json.loads((FIXTURES / "boundary_models.json").read_text())
KNOWN_IDS = frozenset(
    json.loads((FIXTURES / "known_festival_ids.json").read_text())
)
AS_OF = date(2026, 8, 25)
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
SCREENED_AT = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)


def _input(case_id: str) -> PlanningInput:
    return PlanningInput.model_validate(
        ARCHETYPES[case_id]["planning_input"],
        context={"known_festival_ids": KNOWN_IDS},
    )


def _with_mode(value: PlanningInput, mode: PreservationMode) -> PlanningInput:
    changed = value.model_copy(update={"preservation_mode": mode})
    return changed.model_copy(
        update={"planning_input_hash": planning_input_hash(changed)}
    )


def _adapted() -> AdaptedCampaignEvidence:
    snapshot = CampaignSnapshot.model_validate(
        MODELS["CampaignSnapshot"], context={"known_festival_ids": KNOWN_IDS}
    )
    return AdaptedCampaignEvidence(
        profile=CampaignProfile.model_validate(MODELS["CampaignProfile"]),
        retrieval_input=RetrievalInput.model_validate(MODELS["RetrievalInput"]),
        candidates=tuple(
            FrozenCandidateEvidence.model_validate(
                item.model_dump(mode="python"),
                context={"known_festival_ids": KNOWN_IDS},
            )
            for item in snapshot.candidates
        ),
        screenings=(),
        company_memory_summary={"company_name": "Meridian Films"},
        trace=({"module": "OfflineFixture", "provider_call": False},),
        chat_attempts=2,
        embedding_attempts=1,
    )


def _command(
    command_type: str,
    payload: dict[str, Any],
    *,
    version: int,
    key: str,
    invalidation: str = "C",
):
    return parse_campaign_command(
        {
            "type": command_type,
            "payload": payload,
            "expected_version": version,
            "idempotency_key": key,
            "actor": {"kind": "human", "actor_ref": "human:offline-eval"},
            "invalidation_class": invalidation,
        },
        KNOWN_IDS,
    )


def _new_runtime(campaign_id: str):
    evidence = _adapted()
    repository = InMemoryCampaignRepository(clock=lambda: NOW)
    repository.register_workspace("workspace-eval", "a" * 64)
    snapshot = snapshot_from_evidence(
        workspace_id="workspace-eval",
        campaign_id=campaign_id,
        evidence=evidence,
    )
    repository.save_campaign("workspace-eval", snapshot)
    orchestrator = CampaignOrchestrator(repository)
    initial = orchestrator.plan_initial(
        "workspace-eval", campaign_id, as_of_date=AS_OF, evidence=evidence
    )
    assert initial.status == "ready" and initial.plan and initial.planning_input
    return repository, orchestrator, initial


def _planner_evaluation() -> dict[str, Any]:
    cases: dict[str, Any] = {}
    all_pass = True
    for case_id in sorted(ARCHETYPES):
        planning_input = _input(case_id)
        expected = ExpectedPolicyResult.model_validate(
            ARCHETYPES[case_id]["expected_policy_result"],
            context={"known_festival_ids": KNOWN_IDS},
        )
        expected_by_mode = {
            item.mode: item.selected_festival_id for item in expected.mode_selections
        }
        modes: dict[str, Any] = {}
        for mode in PreservationMode:
            current = _with_mode(planning_input, mode)
            first = CampaignPlanner().plan(current)
            second = CampaignPlanner().plan(current)
            StrategyValidator().validate(first, current)
            selected = first.primary_launch.festival_id
            passed = (
                selected == expected_by_mode[mode]
                and first == second
                and first.plan_hash == campaign_plan_hash(first)
                and len(first.alternative_launches) <= 2
                and all(
                    item.hard_filter.feasible
                    for item in first.selection_diagnostics
                    if item.festival_id
                    in {
                        selected,
                        *(route.festival_id for route in first.alternative_launches),
                    }
                )
            )
            all_pass &= passed
            modes[mode.value] = {
                "status": "PASS" if passed else "FAIL",
                "selected": selected,
                "expected": expected_by_mode[mode],
                "plan_hash": first.plan_hash,
                "frontier": [
                    item.festival_id
                    for item in first.selection_diagnostics
                    if item.on_pareto_frontier
                ],
                "preserved": first.option_preservation.preserved_ids,
                "destroyed": first.option_preservation.destroyed_ids,
                "verify": first.option_preservation.verify_ids,
            }
        cases[case_id] = modes
    return {"status": "PASS" if all_pass else "FAIL", "cases": cases}


def _budget_evaluation() -> dict[str, Any]:
    base = _input("E-no-tradeoff")
    hard = BudgetConstraint(
        constraint_id="eval-hard-budget",
        limit=Money(amount=Decimal("40"), currency="USD"),
        hard=True,
    )
    known_fee = RequiredFee(
        fee_id="eval-known-fee",
        action_id="eval-submit-hot-docs",
        festival_id="hot-docs",
        action_scope=FeeActionScope.CURRENT_ROOT,
        required_now=True,
        fee=FeeFact(
            amount=Decimal("50"),
            currency="USD",
            status=FactStatus.CONFIRMED,
            source_refs=("fixture:fee",),
            observed_at=NOW,
        ),
    )
    unknown_fee = known_fee.model_copy(
        update={
            "fee_id": "eval-unknown-fee",
            "fee": FeeFact(status=FactStatus.UNKNOWN, observed_at=NOW),
        }
    )
    known = assess_budget(
        base.model_copy(update={"budget_constraint": hard, "required_fees": (known_fee,)}),
        "hot-docs",
    )
    verify = assess_budget(
        base.model_copy(update={"budget_constraint": hard, "required_fees": (unknown_fee,)}),
        "hot-docs",
    )
    none = assess_budget(
        base.model_copy(update={"budget_constraint": None, "required_fees": ()}),
        "hot-docs",
    )
    passed = (
        known is not None
        and known.state == HardBudgetState.KNOWN_INFEASIBLE
        and verify is not None
        and verify.state == HardBudgetState.VERIFY
        and verify.known_total.amount == 0
        and verify.unknown_fee_ids == ("eval-unknown-fee",)
        and none is None
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "known_over_limit": known.model_dump(mode="json") if known else None,
        "unknown_required_fee": verify.model_dump(mode="json") if verify else None,
        "no_hard_budget": None,
    }


def _incremental_and_scenario_evaluation() -> dict[str, Any]:
    repository, orchestrator, initial = _new_runtime("campaign-eval-reuse")
    before = repository.load_campaign("workspace-eval", "campaign-eval-reuse")
    screening_commands = (
        _command(
            "schedule_screening",
            {
                "screening_id": "screening-eval",
                "festival_id": "hot-docs",
                "country": "Canada",
                "region": "North America",
                "scheduled_at": SCREENED_AT.isoformat(),
                "access": "public",
                "source_refs": ["fixture:scenario-calendar"],
            },
            version=0,
            key="eval-scenario-schedule-0001",
        ),
        _command(
            "confirm_screening",
            {
                "screening_id": "screening-eval",
                "occurred_at": SCREENED_AT.isoformat(),
                "access": "public",
                "country": "Canada",
                "region": "North America",
                "source_refs": ["fixture:scenario-proof"],
            },
            version=1,
            key="eval-scenario-confirm-0001",
            invalidation="B",
        ),
    )
    scenario = CampaignScenarioEngine(orchestrator).simulate(
        before.snapshot,
        screening_commands,
        prior_input=initial.planning_input,
        prior_plan=initial.plan,
        base_strategy_ref=initial.strategy_ref,
        as_of_date=AS_OF,
        occurred_at=SCREENED_AT,
        base_events=before.events,
    )
    after_scenario = repository.load_campaign("workspace-eval", "campaign-eval-reuse")

    submitted = orchestrator.apply_command_and_replan(
        "workspace-eval",
        "campaign-eval-reuse",
        _command(
            "mark_submitted",
            {"festival_id": "hot-docs", "source_refs": ["fixture:submission"]},
            version=0,
            key="eval-submit-hot-docs-0001",
        ),
        as_of_date=AS_OF,
    )
    rejected = orchestrator.apply_command_and_replan(
        "workspace-eval",
        "campaign-eval-reuse",
        _command(
            "record_rejection",
            {"festival_id": "hot-docs", "source_refs": ["fixture:rejection"]},
            version=1,
            key="eval-reject-hot-docs-0001",
        ),
        as_of_date=AS_OF,
    )
    manifest = rejected.reuse_manifest
    scenario_manifest = scenario.reuse_manifest
    passed = (
        before == after_scenario
        and scenario.mutated_campaign is False
        and scenario_manifest is not None
        and scenario_manifest.invalidation_class.value == "B"
        and scenario_manifest.chat_attempts == 0
        and scenario_manifest.embedding_attempts == 0
        and rejected.status == "ready"
        and rejected.diff is not None
        and rejected.diff.primary_before != rejected.diff.primary_after
        and manifest is not None
        and manifest.invalidation_class.value == "C"
        and manifest.chat_attempts == 0
        and manifest.embedding_attempts == 0
        and submitted.status == "ready"
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "rejection": {
            "base_strategy": rejected.diff.base_strategy_ref if rejected.diff else None,
            "new_strategy": rejected.diff.new_strategy_ref if rejected.diff else None,
            "primary_before": rejected.diff.primary_before if rejected.diff else None,
            "primary_after": rejected.diff.primary_after if rejected.diff else None,
            "reuse_manifest": manifest.model_dump(mode="json") if manifest else None,
        },
        "screening_scenario": {
            "campaign_unchanged": before == after_scenario,
            "mutated_campaign": scenario.mutated_campaign,
            "world_after": next(
                item.availability.value
                for item in scenario.hypothetical_snapshot.premiere_ledger.scopes
                if item.scope == PremiereScope.WORLD and item.territory is None
            ),
            "reuse_manifest": (
                scenario_manifest.model_dump(mode="json")
                if scenario_manifest
                else None
            ),
        },
    }


def _correction_evaluation() -> dict[str, Any]:
    evidence = _adapted()
    repository = InMemoryCampaignRepository(clock=lambda: NOW)
    repository.register_workspace("workspace-correction", "c" * 64)
    snapshot = snapshot_from_evidence(
        workspace_id="workspace-correction",
        campaign_id="campaign-correction",
        evidence=evidence,
    )
    repository.save_campaign("workspace-correction", snapshot)
    scheduled = repository.apply_command(
        "workspace-correction",
        "campaign-correction",
        _command(
            "schedule_screening",
            {
                "screening_id": "screening-correction",
                "festival_id": "hot-docs",
                "country": "Canada",
                "scheduled_at": SCREENED_AT.isoformat(),
                "access": "unknown",
                "source_refs": ["fixture:calendar"],
            },
            version=0,
            key="eval-correction-schedule-0001",
        ),
    )
    confirmed = repository.apply_command(
        "workspace-correction",
        "campaign-correction",
        _command(
            "confirm_screening",
            {
                "screening_id": "screening-correction",
                "occurred_at": SCREENED_AT.isoformat(),
                "access": "public",
                "country": "Canada",
                "source_refs": ["fixture:public-proof"],
            },
            version=1,
            key="eval-correction-confirm-0001",
            invalidation="B",
        ),
    )
    corrected = repository.apply_command(
        "workspace-correction",
        "campaign-correction",
        _command(
            "correct_record",
            {
                "prior_ref": confirmed.event.event_id,
                "corrected_domain": "domain_evidence",
                "replacement": {
                    "value": "private",
                    "status": "confirmed",
                    "source_refs": ["fixture:access-correction"],
                    "observed_at": NOW.isoformat(),
                },
            },
            version=2,
            key="eval-correction-private-0001",
            invalidation="B",
        ),
    )
    world_confirmed = next(
        item
        for item in confirmed.aggregate.snapshot.premiere_ledger.scopes
        if item.scope == PremiereScope.WORLD and item.territory is None
    )
    world_corrected = next(
        item
        for item in corrected.aggregate.snapshot.premiere_ledger.scopes
        if item.scope == PremiereScope.WORLD and item.territory is None
    )
    passed = (
        scheduled.aggregate.snapshot.campaign_version == 1
        and world_confirmed.availability == PremiereAvailability.CONSUMED
        and world_corrected.availability == PremiereAvailability.AVAILABLE
        and corrected.aggregate.snapshot.screenings[0].access.value == "private"
        and len(corrected.aggregate.events) == 3
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "world_after_public_confirmation": world_confirmed.availability.value,
        "world_after_private_correction": world_corrected.availability.value,
        "event_history_retained": len(corrected.aggregate.events),
    }


def _isolation_and_corpus_evaluation() -> dict[str, Any]:
    repository, _orchestrator, _initial = _new_runtime("campaign-eval-isolation")
    repository.register_workspace("workspace-other", "b" * 64)
    isolated = False
    try:
        repository.load_campaign("workspace-other", "campaign-eval-isolation")
    except CampaignNotFound:
        isolated = True
    festivals = json.loads((ROOT / "data" / "festivals.json").read_text())
    festival_ids = {str(item.get("id")) for item in festivals}
    sitges_present = "sitges" in festival_ids or any(
        "sitges" in str(item.get("name", "")).casefold() for item in festivals
    )
    return {
        "status": "PASS" if isolated and not sitges_present else "FAIL",
        "capability_scoped_repository_isolation": isolated,
        "corpus_count": len(festivals),
        "sitges": {
            "present": sitges_present,
            "classification": (
                "available" if sitges_present else "known corpus-coverage issue"
            ),
        },
    }


def evaluate_campaign() -> dict[str, Any]:
    sections = {
        "planner_archetypes": _planner_evaluation(),
        "budget_semantics": _budget_evaluation(),
        "incremental_replanning_and_scenarios": _incremental_and_scenario_evaluation(),
        "correction_behavior": _correction_evaluation(),
        "isolation_and_corpus": _isolation_and_corpus_evaluation(),
    }
    status = "PASS" if all(item["status"] == "PASS" for item in sections.values()) else "FAIL"
    return {
        "status": status,
        "provider_calls": 0,
        "external_writes": 0,
        "legacy_regression_gate": "full pytest suite",
        "sections": sections,
    }


def main() -> None:
    print(json.dumps(evaluate_campaign(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
