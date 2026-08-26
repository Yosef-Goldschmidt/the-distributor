"""Ephemeral scenarios built from the real reducer and B/C planning path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.campaign.models import (
    CampaignCommand,
    CampaignEvent,
    CampaignPlan,
    CampaignSnapshot,
    InvalidationClass,
    PlanningInput,
    ReuseManifest,
    StrategyDiff,
)
from app.campaign.orchestration import (
    CacheMissRequiresRefresh,
    CampaignOrchestrator,
)
from app.campaign.state import CampaignStateReducer


class ScenarioError(ValueError):
    """A hypothetical command set is invalid or exceeds the bounded contract."""


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    base_snapshot: CampaignSnapshot
    hypothetical_snapshot: CampaignSnapshot
    hypothetical_plan: CampaignPlan | None
    planning_input: PlanningInput | None
    diff: StrategyDiff | None
    reuse_manifest: ReuseManifest | None
    simulated_events: tuple[CampaignEvent, ...]
    mutated_campaign: bool = False
    requires_provider_refresh: bool = False
    cache_miss_reasons: tuple[str, ...] = ()


class CampaignScenarioEngine:
    """Clone, reduce, replan, compare, and discard with no repository access."""

    def __init__(
        self,
        orchestrator: CampaignOrchestrator,
        *,
        reducer: CampaignStateReducer | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.reducer = reducer or CampaignStateReducer()

    def simulate(
        self,
        snapshot: CampaignSnapshot,
        commands: tuple[CampaignCommand, ...],
        *,
        prior_input: PlanningInput,
        prior_plan: CampaignPlan,
        base_strategy_ref: str,
        as_of_date: date,
        occurred_at: datetime | None = None,
        base_events: tuple[CampaignEvent, ...] = (),
    ) -> ScenarioResult:
        if not 1 <= len(commands) <= 3:
            raise ScenarioError("scenarios require one to three typed commands")
        occurred_at = occurred_at or datetime.now(timezone.utc)
        hypothetical = CampaignSnapshot.model_validate(
            snapshot.model_dump(mode="python"),
            context={
                "known_festival_ids": frozenset(
                    item.festival_id for item in snapshot.opportunities
                )
            },
        )
        events: list[CampaignEvent] = []
        strongest = InvalidationClass.C
        rank = {
            InvalidationClass.C: 0,
            InvalidationClass.B: 1,
            InvalidationClass.A: 2,
        }
        known_events = {item.event_id: item for item in base_events}
        for index, command in enumerate(commands, 1):
            prior_event = None
            if command.type.value == "correct_record":
                prior_event = known_events.get(command.payload.prior_ref)
            reduction = self.reducer.reduce(
                hypothetical,
                command,
                event_id=f"scenario:event:{index}",
                sequence_no=len(base_events) + index,
                occurred_at=occurred_at,
                prior_event=prior_event,
            )
            hypothetical = reduction.snapshot
            events.append(reduction.event)
            known_events[reduction.event.event_id] = reduction.event
            if rank[command.invalidation_class] > rank[strongest]:
                strongest = command.invalidation_class

        if strongest == InvalidationClass.A:
            return ScenarioResult(
                base_snapshot=snapshot,
                hypothetical_snapshot=hypothetical,
                hypothetical_plan=None,
                planning_input=None,
                diff=None,
                reuse_manifest=None,
                simulated_events=tuple(events),
                requires_provider_refresh=True,
                cache_miss_reasons=("scenario_identity_change_requires_provider_refresh",),
            )
        try:
            planning_input, plan, manifest, diff = self.orchestrator.plan_in_memory(
                hypothetical,
                as_of_date=as_of_date,
                invalidation=strongest,
                prior_input=prior_input,
                prior_plan=prior_plan,
                causal_refs=tuple(item.event_id for item in events),
                base_strategy_ref=base_strategy_ref,
                hypothetical_strategy_ref=f"scenario:{snapshot.campaign_id}",
            )
        except CacheMissRequiresRefresh as exc:
            return ScenarioResult(
                base_snapshot=snapshot,
                hypothetical_snapshot=hypothetical,
                hypothetical_plan=None,
                planning_input=None,
                diff=None,
                reuse_manifest=None,
                simulated_events=tuple(events),
                cache_miss_reasons=exc.reasons,
            )
        return ScenarioResult(
            base_snapshot=snapshot,
            hypothetical_snapshot=hypothetical,
            hypothetical_plan=plan,
            planning_input=planning_input,
            diff=diff,
            reuse_manifest=manifest,
            simulated_events=tuple(events),
        )


__all__ = ["CampaignScenarioEngine", "ScenarioError", "ScenarioResult"]
