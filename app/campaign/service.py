"""Application service for capability-scoped Campaign Workspace operations.

This is the product boundary between FastAPI and the campaign domain.  It
coordinates the existing legacy evidence pipeline only when an A-class refresh
is actually required; B/C commands and scenarios remain provider-free.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from app.campaign.adapter import LegacyEvidenceAdapter, LegacyEvidenceBundle
from app.campaign.contracts import canonical_hash
from app.campaign.legacy_pipeline import LegacyEvidencePipeline
from app.campaign.models import (
    CampaignCommand,
    CampaignCreationRequest,
    InvalidationClass,
    PlanningInput,
    ReuseManifest,
    SimulateRequest,
)
from app.campaign.orchestration import (
    CampaignOrchestrator,
    PlanningExecution,
    snapshot_from_evidence,
)
from app.campaign.rendering import CampaignRenderer
from app.campaign.repository import (
    CampaignAggregate,
    CampaignRepository,
    StoredStrategyVersion,
)
from app.campaign.scenarios import CampaignScenarioEngine, ScenarioResult
from app.campaign.state import CampaignStateReducer, VersionConflict


class EvidencePipeline(Protocol):
    def run(
        self,
        *,
        as_of_date: date,
        free_text: str | None = None,
        structured_profile: Any | None = None,
    ) -> LegacyEvidenceBundle: ...


class CampaignServiceError(RuntimeError):
    """The requested application operation cannot be completed."""


class StrategyHistoryNotFound(CampaignServiceError):
    """The requested immutable strategy number does not exist."""


class CampaignService:
    """Coordinate persistence, evidence refresh, planning, and rendering."""

    def __init__(
        self,
        repository: CampaignRepository,
        *,
        pipeline: EvidencePipeline | None = None,
        adapter: LegacyEvidenceAdapter | None = None,
        as_of_date: Callable[[], date] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter or LegacyEvidenceAdapter()
        self.pipeline = pipeline or LegacyEvidencePipeline(adapter=self.adapter)
        self.as_of_date = as_of_date or date.today
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.orchestrator = CampaignOrchestrator(
            repository, adapter=self.adapter
        )
        self.renderer = CampaignRenderer()
        self.scenarios = CampaignScenarioEngine(self.orchestrator)
        self.reducer = CampaignStateReducer()

    def create_workspace(
        self, capability_digest: str, *, display_name: str = "Private demo workspace"
    ) -> str:
        workspace_id = f"workspace-{uuid4().hex}"
        self.repository.create_workspace(
            workspace_id,
            capability_digest,
            display_name=display_name,
        )
        return workspace_id

    def create_campaign(
        self, workspace_id: str, request: CampaignCreationRequest
    ) -> PlanningExecution:
        as_of = self.as_of_date()
        bundle = self.pipeline.run(
            as_of_date=as_of,
            free_text=request.free_text,
            structured_profile=request.structured_profile,
        )
        evidence = self.adapter.adapt(
            bundle,
            as_of_date=as_of,
            observed_at=self.now(),
            authoritative_profile=request.structured_profile,
        )
        campaign_id = f"campaign-{uuid4().hex}"
        snapshot = snapshot_from_evidence(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            evidence=evidence,
        )
        self.repository.save_campaign(workspace_id, snapshot)
        return self.orchestrator.plan_initial(
            workspace_id,
            campaign_id,
            as_of_date=as_of,
            evidence=evidence,
        )

    def list_campaigns(self, workspace_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(self._summary(item) for item in self.repository.list_campaigns(workspace_id))

    def get_campaign(self, workspace_id: str, campaign_id: str) -> dict[str, Any]:
        return self._detail(self.repository.load_campaign(workspace_id, campaign_id))

    def apply_command(
        self,
        workspace_id: str,
        campaign_id: str,
        command: CampaignCommand,
    ) -> PlanningExecution:
        aggregate = self.repository.load_campaign(workspace_id, campaign_id)
        replay = next(
            (
                event
                for event in aggregate.events
                if event.command.idempotency_key == command.idempotency_key
            ),
            None,
        )
        if replay is not None:
            # Repository idempotency validates that the full typed command matches.
            return self.orchestrator.apply_command_and_replan(
                workspace_id,
                campaign_id,
                command,
                as_of_date=self.as_of_date(),
            )
        if command.expected_version != aggregate.snapshot.campaign_version:
            raise VersionConflict(
                command.expected_version, aggregate.snapshot.campaign_version
            )

        refreshed = None
        if command.invalidation_class == InvalidationClass.A:
            preview = self.reducer.reduce(
                aggregate.snapshot,
                command,
                event_id="preview:event",
                sequence_no=len(aggregate.events) + 1,
                occurred_at=self.now(),
                prior_event=self._prior_event(aggregate, command),
            )
            refreshed = self._refresh(preview.snapshot.profile)
        return self.orchestrator.apply_command_and_replan(
            workspace_id,
            campaign_id,
            command,
            as_of_date=self.as_of_date(),
            refreshed_evidence=refreshed,
        )

    def replan(
        self, workspace_id: str, campaign_id: str, *, expected_version: int
    ) -> PlanningExecution:
        aggregate = self.repository.load_campaign(workspace_id, campaign_id)
        if expected_version != aggregate.snapshot.campaign_version:
            raise VersionConflict(expected_version, aggregate.snapshot.campaign_version)
        invalidation = self._current_invalidation(aggregate)
        refreshed = (
            self._refresh(aggregate.snapshot.profile)
            if invalidation == InvalidationClass.A
            else None
        )
        return self.orchestrator.replan_stale(
            workspace_id,
            campaign_id,
            as_of_date=self.as_of_date(),
            invalidation=invalidation,
            refreshed_evidence=refreshed,
        )

    def simulate(
        self,
        workspace_id: str,
        campaign_id: str,
        request: SimulateRequest,
    ) -> ScenarioResult:
        aggregate = self.repository.load_campaign(workspace_id, campaign_id)
        active = self._active_ready(aggregate)
        if active is None or active.attempt.plan is None:
            raise CampaignServiceError("a ready active strategy is required for simulation")
        planning_input = PlanningInput.model_validate(
            active.attempt.input_snapshot_json,
            context={
                "known_festival_ids": frozenset(
                    item.festival_id for item in aggregate.snapshot.opportunities
                )
            },
        )
        return self.scenarios.simulate(
            aggregate.snapshot,
            request.commands,
            prior_input=planning_input,
            prior_plan=active.attempt.plan,
            base_strategy_ref=active.attempt.strategy_id,
            as_of_date=self.as_of_date(),
            occurred_at=self.now(),
            base_events=aggregate.events,
        )

    def strategy_history(
        self, workspace_id: str, campaign_id: str, strategy_no: int
    ) -> dict[str, Any]:
        aggregate = self.repository.load_campaign(workspace_id, campaign_id)
        strategy = next(
            (item for item in aggregate.strategy_versions if item.strategy_no == strategy_no),
            None,
        )
        if strategy is None:
            raise StrategyHistoryNotFound("strategy version not found")
        attempt = strategy.attempt
        return {
            "campaign_id": campaign_id,
            "strategy_no": strategy.strategy_no,
            "based_on_campaign_version": attempt.based_on_campaign_version,
            "input_hash": attempt.input_hash,
            "outcome": attempt.outcome,
            "plan": self._dump(attempt.plan),
            "diff": self._dump(attempt.diff),
            "reuse_manifest": dict(attempt.reuse_manifest_json) or None,
            "usage": dict(attempt.usage_json),
            "trace": self._collapsed_trace(attempt.trace_json),
            "policy_versions": attempt.policy_versions,
            "model_versions": attempt.model_versions,
            "error": dict(attempt.error_json) if attempt.error_json else None,
        }

    def _refresh(self, profile: Any):
        as_of = self.as_of_date()
        bundle = self.pipeline.run(as_of_date=as_of, structured_profile=profile)
        return self.adapter.adapt(
            bundle,
            as_of_date=as_of,
            observed_at=self.now(),
            authoritative_profile=profile,
        )

    @staticmethod
    def _prior_event(aggregate: CampaignAggregate, command: CampaignCommand):
        prior_ref = getattr(command.payload, "prior_ref", None)
        if not prior_ref:
            return None
        return next(
            (event for event in aggregate.events if event.event_id == prior_ref), None
        )

    @staticmethod
    def _current_invalidation(aggregate: CampaignAggregate) -> InvalidationClass:
        if not aggregate.events:
            return InvalidationClass.C
        return aggregate.events[-1].command.invalidation_class

    @staticmethod
    def _active_ready(aggregate: CampaignAggregate) -> StoredStrategyVersion | None:
        active_ref = aggregate.snapshot.active_strategy_ref
        return next(
            (
                item
                for item in reversed(aggregate.strategy_versions)
                if item.attempt.strategy_id == active_ref
                and item.attempt.outcome == "ready"
            ),
            None,
        )

    @staticmethod
    def _latest_ready(aggregate: CampaignAggregate) -> StoredStrategyVersion | None:
        return next(
            (
                item
                for item in reversed(aggregate.strategy_versions)
                if item.attempt.outcome == "ready"
            ),
            None,
        )

    def _detail(self, aggregate: CampaignAggregate) -> dict[str, Any]:
        active = self._active_ready(aggregate)
        latest_ready = self._latest_ready(aggregate)
        plan = active.attempt.plan if active else None
        diff = latest_ready.attempt.diff if latest_ready else None
        manifest = self._manifest(latest_ready) if latest_ready else None
        memory = self._company_memory(aggregate)
        view = self.renderer.render(
            aggregate.snapshot,
            plan,
            strategy_ref=active.attempt.strategy_id if active else None,
            diff=diff,
            reuse_manifest=manifest,
            company_memory=memory,
            cache_status="stale" if aggregate.strategy_stale else "valid",
        )
        return {
            "snapshot": aggregate.snapshot.model_dump(mode="json"),
            "active_plan": self._dump(plan),
            "latest_diff": self._dump(diff),
            "strategy_stale": aggregate.strategy_stale,
            "view": view.model_dump(mode="json"),
            "events": tuple(event.model_dump(mode="json") for event in aggregate.events),
            "strategies": tuple(
                {
                    "strategy_no": item.strategy_no,
                    "strategy_ref": item.attempt.strategy_id,
                    "campaign_version": item.attempt.based_on_campaign_version,
                    "outcome": item.attempt.outcome,
                    "usage": dict(item.attempt.usage_json),
                    "created_at": item.created_at.isoformat(),
                }
                for item in aggregate.strategy_versions
            ),
            "trace": (
                self._collapsed_trace(latest_ready.attempt.trace_json)
                if latest_ready
                else {"provider_steps": (), "deterministic_steps": ()}
            ),
        }

    @staticmethod
    def _summary(aggregate: CampaignAggregate) -> dict[str, Any]:
        snapshot = aggregate.snapshot
        return {
            "campaign_id": snapshot.campaign_id,
            "title": snapshot.profile.title.value or "Untitled film",
            "campaign_version": snapshot.campaign_version,
            "lifecycle": snapshot.lifecycle.value,
            "readiness": snapshot.readiness.value,
            "active_strategy_ref": snapshot.active_strategy_ref,
            "strategy_stale": aggregate.strategy_stale,
        }

    @staticmethod
    def _manifest(strategy: StoredStrategyVersion) -> ReuseManifest | None:
        raw = strategy.attempt.reuse_manifest_json
        return ReuseManifest.model_validate(raw) if raw else None

    @staticmethod
    def _company_memory(aggregate: CampaignAggregate) -> Mapping[str, Any]:
        for strategy in reversed(aggregate.strategy_versions):
            memory = strategy.attempt.trace_json.get("company_memory")
            if isinstance(memory, Mapping):
                return dict(memory)
        return {}

    @staticmethod
    def _collapsed_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
        provider = tuple(trace.get("provider_trace") or ())
        deterministic = tuple(trace.get("deterministic_trace") or ())
        return {
            "provider_steps": provider,
            "deterministic_steps": deterministic,
            "trace_hash": canonical_hash(
                {"provider_trace": provider, "deterministic_trace": deterministic}
            ),
        }

    @staticmethod
    def _dump(value: Any) -> Any:
        return value.model_dump(mode="json") if value is not None else None


__all__ = [
    "CampaignService",
    "CampaignServiceError",
    "EvidencePipeline",
    "StrategyHistoryNotFound",
]
