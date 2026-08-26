"""Campaign planning orchestration, invalidation, reuse proof, and persistence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Literal, Mapping

from app.campaign.adapter import AdaptedCampaignEvidence, LegacyEvidenceAdapter
from app.campaign.compatibility import GRAPH_POLICY_VERSION, CompatibilityBuilder
from app.campaign.contracts import (
    BUDGET_POLICY_VERSION,
    PLANNER_POLICY_VERSION,
    campaign_snapshot_hash,
    canonical_hash,
    graph_key,
    plan_key,
    planning_input_hash,
    strategy_diff_hash,
)
from app.campaign.models import (
    ArtifactKeys,
    ArtifactName,
    BudgetConstraint,
    CampaignCommand,
    CampaignLifecycle,
    CampaignOpportunity,
    CampaignPlan,
    CampaignReadiness,
    CampaignSnapshot,
    InvalidationClass,
    PlanningInput,
    PreservationMode,
    RequiredFee,
    ReuseManifest,
    StrategyDiff,
)
from app.campaign.planning import CampaignPlanner
from app.campaign.repository import (
    CampaignAggregate,
    CampaignRepository,
    CommandApplication,
    StoredStrategyVersion,
    StrategyAttempt,
)


ORCHESTRATOR_POLICY_VERSION = "campaign-orchestrator-v1"


class CampaignOrchestrationError(RuntimeError):
    """Base orchestration failure."""


class CacheMissRequiresRefresh(CampaignOrchestrationError):
    """A B/C run cannot prove that its required evidence cache is valid."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__("cache_miss_requires_refresh: " + ", ".join(reasons))


@dataclass(frozen=True, slots=True)
class PlanningExecution:
    status: Literal["ready", "failed", "stale"]
    aggregate: CampaignAggregate
    planning_input: PlanningInput | None = None
    plan: CampaignPlan | None = None
    diff: StrategyDiff | None = None
    reuse_manifest: ReuseManifest | None = None
    strategy_ref: str | None = None
    cache_miss_reasons: tuple[str, ...] = ()
    idempotent_replay: bool = False


def _aggregate_key(candidates: tuple[Any, ...], attribute: str) -> str:
    return canonical_hash(
        tuple(
            sorted(
                (
                    candidate.festival_id,
                    getattr(getattr(candidate, attribute), f"{attribute}_key"),
                )
                for candidate in candidates
            )
        )
    )


def _risk_aggregate_key(candidates: tuple[Any, ...]) -> str:
    return canonical_hash(
        tuple(
            sorted(
                (candidate.festival_id, candidate.risk.risk_key)
                for candidate in candidates
            )
        )
    )


def _retrieval_key(candidates: tuple[Any, ...]) -> str:
    keys = {candidate.retrieved.retrieval_key for candidate in candidates}
    if len(keys) != 1:
        raise CacheMissRequiresRefresh(("retrieval_key_mismatch_within_candidate_set",))
    return next(iter(keys))


class CampaignOrchestrator:
    """Select deterministic stages, prove reuse, and activate strategies by CAS."""

    def __init__(
        self,
        repository: CampaignRepository,
        *,
        adapter: LegacyEvidenceAdapter | None = None,
        compatibility: CompatibilityBuilder | None = None,
        planner: CampaignPlanner | None = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter or LegacyEvidenceAdapter()
        self.compatibility = compatibility or CompatibilityBuilder()
        self.planner = planner or CampaignPlanner()

    def plan_initial(
        self,
        workspace_id: str,
        campaign_id: str,
        *,
        as_of_date: date,
        evidence: AdaptedCampaignEvidence | None = None,
        budget_constraint: BudgetConstraint | None = None,
        required_fees: tuple[RequiredFee, ...] = (),
    ) -> PlanningExecution:
        aggregate = self.repository.load_campaign(workspace_id, campaign_id)
        snapshot = aggregate.snapshot
        if evidence is not None:
            if evidence.profile.profile_hash != snapshot.profile.profile_hash:
                raise CampaignOrchestrationError(
                    "adapted evidence profile does not match the persisted campaign profile"
                )
            if tuple(item.festival_id for item in evidence.candidates) != tuple(
                item.festival_id for item in snapshot.candidates
            ):
                raise CampaignOrchestrationError(
                    "adapted evidence candidates do not match the persisted campaign snapshot"
                )
        return self._plan_and_record(
            workspace_id,
            aggregate,
            as_of_date=as_of_date,
            invalidation=None,
            causal_refs=("campaign:initial",),
            prior=None,
            refreshed_evidence=evidence,
            budget_constraint=budget_constraint,
            required_fees=required_fees,
        )

    def apply_command_and_replan(
        self,
        workspace_id: str,
        campaign_id: str,
        command: CampaignCommand,
        *,
        as_of_date: date,
        refreshed_evidence: AdaptedCampaignEvidence | None = None,
    ) -> PlanningExecution:
        application = self.repository.apply_command(
            workspace_id, campaign_id, command
        )
        if application.idempotent_replay:
            current = self.repository.load_campaign(workspace_id, campaign_id)
            ready = self._active_ready(current)
            if ready is not None:
                return PlanningExecution(
                    status="ready",
                    aggregate=current,
                    planning_input=self._parse_input(ready, current.snapshot),
                    plan=ready.attempt.plan,
                    diff=ready.attempt.diff,
                    reuse_manifest=self._parse_manifest(ready),
                    strategy_ref=ready.attempt.strategy_id,
                    idempotent_replay=True,
                )
            return PlanningExecution(
                status="stale", aggregate=current, idempotent_replay=True
            )
        prior = self._active_ready_before(application)
        return self._plan_and_record(
            workspace_id,
            application.aggregate,
            as_of_date=as_of_date,
            invalidation=command.invalidation_class,
            causal_refs=(application.event.event_id, command.type.value),
            prior=prior,
            refreshed_evidence=refreshed_evidence,
        )

    def replan_stale(
        self,
        workspace_id: str,
        campaign_id: str,
        *,
        as_of_date: date,
        invalidation: InvalidationClass = InvalidationClass.C,
        refreshed_evidence: AdaptedCampaignEvidence | None = None,
    ) -> PlanningExecution:
        aggregate = self.repository.load_campaign(workspace_id, campaign_id)
        prior = self._active_ready(aggregate)
        return self._plan_and_record(
            workspace_id,
            aggregate,
            as_of_date=as_of_date,
            invalidation=invalidation,
            causal_refs=("replan:retry",),
            prior=prior,
            refreshed_evidence=refreshed_evidence,
        )

    def plan_in_memory(
        self,
        snapshot: CampaignSnapshot,
        *,
        as_of_date: date,
        invalidation: InvalidationClass,
        prior_input: PlanningInput,
        prior_plan: CampaignPlan,
        causal_refs: tuple[str, ...],
        base_strategy_ref: str,
        hypothetical_strategy_ref: str,
    ) -> tuple[PlanningInput, CampaignPlan, ReuseManifest, StrategyDiff]:
        """Run the same B/C path without touching a repository."""

        planning_input, manifest = self._planning_input(
            snapshot,
            as_of_date=as_of_date,
            invalidation=invalidation,
            prior_input=prior_input,
            refreshed_evidence=None,
            reason=causal_refs[-1] if causal_refs else "scenario",
        )
        plan = self.planner.plan(planning_input)
        diff = self._diff(
            prior_plan,
            plan,
            manifest,
            snapshot.campaign_id,
            prior_input.campaign_version,
            snapshot.campaign_version,
            base_strategy_ref,
            hypothetical_strategy_ref,
            causal_refs,
        )
        return planning_input, plan, manifest, diff

    def _plan_and_record(
        self,
        workspace_id: str,
        aggregate: CampaignAggregate,
        *,
        as_of_date: date,
        invalidation: InvalidationClass | None,
        causal_refs: tuple[str, ...],
        prior: StoredStrategyVersion | None,
        refreshed_evidence: AdaptedCampaignEvidence | None,
        budget_constraint: BudgetConstraint | None = None,
        required_fees: tuple[RequiredFee, ...] = (),
    ) -> PlanningExecution:
        strategy_ref = (
            f"strategy:{aggregate.snapshot.campaign_id}:"
            f"{len(aggregate.strategy_versions) + 1}"
        )
        prior_input: PlanningInput | None = None
        prior_plan: CampaignPlan | None = None
        if prior is not None:
            prior_input = self._parse_input(prior, aggregate.snapshot)
            prior_plan = prior.attempt.plan
        planning_input: PlanningInput | None = None
        manifest: ReuseManifest | None = None
        try:
            planning_input, manifest = self._planning_input(
                aggregate.snapshot,
                as_of_date=as_of_date,
                invalidation=invalidation,
                prior_input=prior_input,
                refreshed_evidence=refreshed_evidence,
                reason=causal_refs[-1] if causal_refs else "replan",
                budget_constraint=budget_constraint,
                required_fees=required_fees,
            )
            plan = self.planner.plan(planning_input)
            diff = None
            if prior_input is not None and prior_plan is not None and manifest is not None:
                diff = self._diff(
                    prior_plan,
                    plan,
                    manifest,
                    aggregate.snapshot.campaign_id,
                    prior_input.campaign_version,
                    aggregate.snapshot.campaign_version,
                    prior.attempt.strategy_id,
                    strategy_ref,
                    causal_refs,
                )
            attempt = StrategyAttempt(
                strategy_id=strategy_ref,
                based_on_campaign_version=aggregate.snapshot.campaign_version,
                outcome="ready",
                input_snapshot_json=planning_input.model_dump(mode="json"),
                input_hash=planning_input.planning_input_hash,
                plan=plan,
                diff=diff,
                trace_json=self._trace(
                    invalidation, manifest, causal_refs, refreshed_evidence
                ),
                reuse_manifest_json=(
                    manifest.model_dump(mode="json") if manifest else {}
                ),
                usage_json={
                    "chat_attempts": (
                        refreshed_evidence.chat_attempts if refreshed_evidence else 0
                    ),
                    "embedding_attempts": (
                        refreshed_evidence.embedding_attempts
                        if refreshed_evidence
                        else 0
                    ),
                },
                policy_versions=planning_input.policy_versions,
                model_versions=(
                    tuple(
                        sorted(
                            {
                                item.creative.model_version
                                for item in planning_input.candidates
                            }
                        )
                    )
                ),
            )
            recorded = self.repository.record_strategy_attempt(
                workspace_id, aggregate.snapshot.campaign_id, attempt
            )
            return PlanningExecution(
                status="ready",
                aggregate=recorded,
                planning_input=planning_input,
                plan=plan,
                diff=diff,
                reuse_manifest=manifest,
                strategy_ref=strategy_ref,
            )
        except CacheMissRequiresRefresh as exc:
            failed = self._record_failure(
                workspace_id,
                aggregate,
                strategy_ref,
                planning_input,
                "cache_miss_requires_refresh",
                {"reasons": list(exc.reasons)},
            )
            return PlanningExecution(
                status="stale",
                aggregate=failed,
                planning_input=planning_input,
                strategy_ref=strategy_ref,
                cache_miss_reasons=exc.reasons,
            )
        except Exception as exc:  # noqa: BLE001 - persist an inspectable failed attempt
            failed = self._record_failure(
                workspace_id,
                aggregate,
                strategy_ref,
                planning_input,
                "planning_failed",
                {"type": type(exc).__name__, "message": str(exc)},
            )
            return PlanningExecution(
                status="failed",
                aggregate=failed,
                planning_input=planning_input,
                reuse_manifest=manifest,
                strategy_ref=strategy_ref,
            )

    def _planning_input(
        self,
        snapshot: CampaignSnapshot,
        *,
        as_of_date: date,
        invalidation: InvalidationClass | None,
        prior_input: PlanningInput | None,
        refreshed_evidence: AdaptedCampaignEvidence | None,
        reason: str,
        budget_constraint: BudgetConstraint | None = None,
        required_fees: tuple[RequiredFee, ...] = (),
    ) -> tuple[PlanningInput, ReuseManifest | None]:
        if invalidation is not None and prior_input is None:
            raise CacheMissRequiresRefresh(("active_strategy_input_missing",))

        candidates = snapshot.candidates
        edges = None
        manifest: ReuseManifest | None = None
        previous_budget = budget_constraint
        previous_fees = required_fees
        mode = PreservationMode.BALANCED
        if prior_input is not None:
            self._verify_cache(snapshot, prior_input, invalidation)
            if (
                invalidation == InvalidationClass.C
                and prior_input.as_of_date != as_of_date
            ):
                raise CacheMissRequiresRefresh(
                    ("as_of_date_changed_requires_domain_refresh",)
                )
            previous_budget = prior_input.budget_constraint
            previous_fees = prior_input.required_fees
            mode = prior_input.preservation_mode

        for constraint in snapshot.constraints:
            if constraint.active and constraint.constraint_type == "preservation_mode":
                try:
                    mode = PreservationMode(str(constraint.value))
                except ValueError as exc:
                    raise CampaignOrchestrationError(
                        "active preservation_mode constraint is invalid"
                    ) from exc

        if invalidation == InvalidationClass.A:
            if refreshed_evidence is None:
                raise CacheMissRequiresRefresh(("identity_change_requires_provider_refresh",))
            if refreshed_evidence.profile.profile_hash != snapshot.profile.profile_hash:
                raise CacheMissRequiresRefresh(("refreshed_identity_hash_mismatch",))
            candidates = refreshed_evidence.candidates
        elif invalidation == InvalidationClass.B:
            candidates = self.adapter.refresh_risk(
                snapshot.candidates,
                snapshot.profile,
                snapshot.premiere_ledger,
                snapshot.screenings,
                as_of_date=as_of_date,
            )
        elif invalidation == InvalidationClass.C:
            assert prior_input is not None
            edges = prior_input.compatibility_edges

        if edges is None:
            edges = self.compatibility.build(
                candidates, snapshot.profile, snapshot.premiere_ledger
            )
        planning_input = self._assemble_input(
            snapshot,
            candidates=tuple(candidates),
            edges=tuple(edges),
            preservation_mode=mode,
            budget_constraint=previous_budget,
            required_fees=previous_fees,
            as_of_date=as_of_date,
        )

        if invalidation is not None and prior_input is not None:
            manifest = self._manifest(
                invalidation,
                prior_input.artifact_keys,
                planning_input.artifact_keys,
                reason,
                chat_attempts=(
                    refreshed_evidence.chat_attempts if refreshed_evidence else 0
                ),
                embedding_attempts=(
                    refreshed_evidence.embedding_attempts
                    if refreshed_evidence
                    else 0
                ),
            )
        return planning_input, manifest

    def _assemble_input(
        self,
        snapshot: CampaignSnapshot,
        *,
        candidates: tuple[Any, ...],
        edges: tuple[Any, ...],
        preservation_mode: PreservationMode,
        budget_constraint: BudgetConstraint | None,
        required_fees: tuple[RequiredFee, ...],
        as_of_date: date,
    ) -> PlanningInput:
        retrieval = _retrieval_key(candidates)
        creative = _aggregate_key(candidates, "creative")
        risk = _risk_aggregate_key(candidates)
        empty_keys = ArtifactKeys(
            identity_hash=snapshot.profile.profile_hash,
            retrieval_key=retrieval,
            creative_key=creative,
            risk_key=risk,
            graph_key="0" * 64,
            plan_key="0" * 64,
        )
        policy_versions = (
            ORCHESTRATOR_POLICY_VERSION,
            PLANNER_POLICY_VERSION,
            BUDGET_POLICY_VERSION,
            GRAPH_POLICY_VERSION,
        )
        existing_opportunities = {
            item.festival_id: item for item in snapshot.opportunities
        }
        planning_opportunities = tuple(
            existing_opportunities.get(candidate.festival_id)
            or CampaignOpportunity(
                opportunity_id=(
                    f"opportunity:{snapshot.campaign_id}:{candidate.festival_id}"
                ),
                festival_id=candidate.festival_id,
                verification_items=candidate.risk.uncertainties,
            )
            for candidate in candidates
        )
        initial = PlanningInput(
            campaign_id=snapshot.campaign_id,
            campaign_version=snapshot.campaign_version,
            profile=snapshot.profile,
            premiere_ledger=snapshot.premiere_ledger,
            candidates=candidates,
            compatibility_edges=edges,
            opportunities=planning_opportunities,
            constraints=snapshot.constraints,
            preservation_mode=preservation_mode,
            budget_constraint=budget_constraint,
            required_fees=required_fees,
            as_of_date=as_of_date,
            policy_versions=policy_versions,
            artifact_keys=empty_keys,
            planning_input_hash="0" * 64,
        )
        with_graph = initial.model_copy(
            update={
                "artifact_keys": empty_keys.model_copy(
                    update={"graph_key": graph_key(initial)}
                )
            }
        )
        final_keys = with_graph.artifact_keys.model_copy(
            update={"plan_key": plan_key(with_graph)}
        )
        unhashed = with_graph.model_copy(update={"artifact_keys": final_keys})
        return unhashed.model_copy(
            update={"planning_input_hash": planning_input_hash(unhashed)}
        )

    @staticmethod
    def _verify_cache(
        snapshot: CampaignSnapshot,
        prior_input: PlanningInput,
        invalidation: InvalidationClass | None,
    ) -> None:
        reasons: list[str] = []
        if planning_input_hash(prior_input) != prior_input.planning_input_hash:
            reasons.append("prior_planning_input_hash_mismatch")
        if snapshot.profile.profile_hash != prior_input.artifact_keys.identity_hash:
            if invalidation != InvalidationClass.A:
                reasons.append("identity_hash_mismatch")
        if (
            invalidation == InvalidationClass.C
            and snapshot.premiere_ledger.ledger_hash
            != prior_input.premiere_ledger.ledger_hash
        ):
            reasons.append("premiere_ledger_changed_on_operational_replan")
        prior_by_id = {item.festival_id: item for item in prior_input.candidates}
        snapshot_by_id = {item.festival_id: item for item in snapshot.candidates}
        if set(prior_by_id) != set(snapshot_by_id):
            reasons.append("candidate_set_cache_mismatch")
        elif invalidation in {InvalidationClass.B, InvalidationClass.C}:
            if any(
                prior_by_id[item].component_hash
                != snapshot_by_id[item].component_hash
                for item in prior_by_id
            ):
                reasons.append("candidate_component_cache_mismatch")
        try:
            if _retrieval_key(prior_input.candidates) != prior_input.artifact_keys.retrieval_key:
                reasons.append("retrieval_key_mismatch")
        except CacheMissRequiresRefresh as exc:
            reasons.extend(exc.reasons)
        if _aggregate_key(prior_input.candidates, "creative") != prior_input.artifact_keys.creative_key:
            reasons.append("creative_key_mismatch")
        if _risk_aggregate_key(prior_input.candidates) != prior_input.artifact_keys.risk_key:
            reasons.append("risk_key_mismatch")
        if graph_key(prior_input) != prior_input.artifact_keys.graph_key:
            reasons.append("graph_key_mismatch")
        if plan_key(prior_input) != prior_input.artifact_keys.plan_key:
            reasons.append("plan_key_mismatch")
        if reasons:
            raise CacheMissRequiresRefresh(tuple(dict.fromkeys(reasons)))

    @staticmethod
    def _manifest(
        invalidation: InvalidationClass,
        prior: ArtifactKeys,
        current: ArtifactKeys,
        reason: str,
        *,
        chat_attempts: int = 0,
        embedding_attempts: int = 0,
    ) -> ReuseManifest:
        if invalidation == InvalidationClass.C:
            reused = (
                ArtifactName.RETRIEVAL,
                ArtifactName.CREATIVE_EVIDENCE,
                ArtifactName.RISK,
                ArtifactName.PREMIERE_LEDGER,
                ArtifactName.COMPATIBILITY_GRAPH,
            )
            rerun = (ArtifactName.PLANNER, ArtifactName.CLARIFICATION)
            invalidated: tuple[ArtifactName, ...] = ()
        elif invalidation == InvalidationClass.B:
            reused = (
                ArtifactName.COMPANY_MEMORY,
                ArtifactName.RETRIEVAL,
                ArtifactName.CREATIVE_EVIDENCE,
            )
            rerun = (
                ArtifactName.PREMIERE_LEDGER,
                ArtifactName.RISK,
                ArtifactName.COMPATIBILITY_GRAPH,
                ArtifactName.PLANNER,
                ArtifactName.CLARIFICATION,
            )
            invalidated = rerun
        else:
            reused = (ArtifactName.COMPANY_MEMORY,)
            rerun = (
                ArtifactName.RETRIEVAL,
                ArtifactName.CREATIVE_EVIDENCE,
                ArtifactName.RISK,
                ArtifactName.PREMIERE_LEDGER,
                ArtifactName.COMPATIBILITY_GRAPH,
                ArtifactName.PLANNER,
                ArtifactName.CLARIFICATION,
            )
            invalidated = (
                ArtifactName.RETRIEVAL,
                ArtifactName.CREATIVE_EVIDENCE,
                ArtifactName.RISK,
                ArtifactName.COMPATIBILITY_GRAPH,
            )
        return ReuseManifest(
            invalidation_class=invalidation,
            prior_artifact_keys=prior,
            current_artifact_keys=current,
            reused_artifacts=reused,
            rerun_artifacts=rerun,
            invalidated_artifacts=invalidated,
            reasons=(reason,),
            chat_attempts=chat_attempts,
            embedding_attempts=embedding_attempts,
        )

    @staticmethod
    def _diff(
        before: CampaignPlan,
        after: CampaignPlan,
        manifest: ReuseManifest,
        campaign_id: str,
        base_campaign_version: int,
        new_campaign_version: int,
        base_strategy_ref: str,
        new_strategy_ref: str,
        causal_refs: tuple[str, ...],
    ) -> StrategyDiff:
        before_routes = {
            before.primary_launch.festival_id,
            *(item.festival_id for item in before.alternative_launches),
            *before.post_premiere_opportunities,
        }
        after_routes = {
            after.primary_launch.festival_id,
            *(item.festival_id for item in after.alternative_launches),
            *after.post_premiere_opportunities,
        }
        before_gates = {item.gate_id for item in before.verification_gates}
        after_gates = {item.gate_id for item in after.verification_gates}
        initial = StrategyDiff(
            campaign_id=campaign_id,
            base_campaign_version=base_campaign_version,
            new_campaign_version=new_campaign_version,
            base_strategy_ref=base_strategy_ref,
            new_strategy_ref=new_strategy_ref,
            primary_before=before.primary_launch.festival_id,
            primary_after=after.primary_launch.festival_id,
            added_route_ids=tuple(sorted(after_routes - before_routes)),
            removed_route_ids=tuple(sorted(before_routes - after_routes)),
            unchanged_route_ids=tuple(sorted(before_routes & after_routes)),
            gate_change_ids=tuple(sorted(before_gates ^ after_gates)),
            budget_state_before=(before.budget.state if before.budget else None),
            budget_state_after=(after.budget.state if after.budget else None),
            preservation_before=before.option_preservation,
            preservation_after=after.option_preservation,
            causal_refs=causal_refs or ("replan",),
            reuse_summary=manifest,
            diff_hash="0" * 64,
        )
        return initial.model_copy(
            update={"diff_hash": strategy_diff_hash(initial)}
        )

    @staticmethod
    def _trace(
        invalidation: InvalidationClass | None,
        manifest: ReuseManifest | None,
        causal_refs: tuple[str, ...],
        evidence: AdaptedCampaignEvidence | None,
    ) -> Mapping[str, Any]:
        provider_trace = list(evidence.trace) if evidence else []
        deterministic = [
            {
                "module": "CampaignOrchestrator",
                "invalidation_class": invalidation.value if invalidation else "initial",
                "causal_refs": list(causal_refs),
            },
            {
                "module": "ReuseDecision",
                "manifest": manifest.model_dump(mode="json") if manifest else None,
            },
            {"module": "CampaignPlanner", "provider_call": False},
            {"module": "ClarificationEngine", "provider_call": False},
            {"module": "StrategyDiff", "provider_call": False},
        ]
        return {
            "provider_trace": provider_trace,
            "deterministic_trace": deterministic,
            "company_memory": (
                dict(evidence.company_memory_summary) if evidence else None
            ),
        }

    def _record_failure(
        self,
        workspace_id: str,
        aggregate: CampaignAggregate,
        strategy_ref: str,
        planning_input: PlanningInput | None,
        code: str,
        details: Mapping[str, Any],
    ) -> CampaignAggregate:
        input_payload = (
            planning_input.model_dump(mode="json")
            if planning_input is not None
            else aggregate.snapshot.model_dump(mode="json")
        )
        input_hash = (
            planning_input.planning_input_hash
            if planning_input is not None
            else aggregate.snapshot.aggregate_hash
        )
        return self.repository.record_strategy_attempt(
            workspace_id,
            aggregate.snapshot.campaign_id,
            StrategyAttempt(
                strategy_id=strategy_ref,
                based_on_campaign_version=aggregate.snapshot.campaign_version,
                outcome="failed",
                input_snapshot_json=input_payload,
                input_hash=input_hash,
                error_json={"code": code, "details": dict(details)},
            ),
        )

    @staticmethod
    def _active_ready(aggregate: CampaignAggregate) -> StoredStrategyVersion | None:
        active = aggregate.snapshot.active_strategy_ref
        return next(
            (
                item
                for item in reversed(aggregate.strategy_versions)
                if item.attempt.strategy_id == active
                and item.attempt.outcome == "ready"
            ),
            None,
        )

    @classmethod
    def _active_ready_before(
        cls, application: CommandApplication
    ) -> StoredStrategyVersion | None:
        active = application.aggregate.snapshot.active_strategy_ref
        return next(
            (
                item
                for item in reversed(application.aggregate.strategy_versions)
                if item.attempt.strategy_id == active
                and item.attempt.outcome == "ready"
            ),
            None,
        )

    @staticmethod
    def _parse_input(
        strategy: StoredStrategyVersion, snapshot: CampaignSnapshot
    ) -> PlanningInput:
        try:
            return PlanningInput.model_validate(
                strategy.attempt.input_snapshot_json,
                context={
                    "known_festival_ids": frozenset(
                        item.festival_id for item in snapshot.opportunities
                    )
                },
            )
        except Exception as exc:  # noqa: BLE001 - normalize persisted cache corruption
            raise CacheMissRequiresRefresh(("active_strategy_input_unreadable",)) from exc

    @staticmethod
    def _parse_manifest(
        strategy: StoredStrategyVersion,
    ) -> ReuseManifest | None:
        if not strategy.attempt.reuse_manifest_json:
            return None
        return ReuseManifest.model_validate(strategy.attempt.reuse_manifest_json)


def snapshot_from_evidence(
    *,
    workspace_id: str,
    campaign_id: str,
    evidence: AdaptedCampaignEvidence,
) -> CampaignSnapshot:
    """Create a version-zero aggregate without performing a durable write."""

    from app.campaign.premiere import PremiereLedger, TrackedPremiereScope

    tracked = {
        TrackedPremiereScope(
            scope=candidate.risk.premiere_constraint.scope,
            territory=candidate.risk.premiere_constraint.territory,
        )
        for candidate in evidence.candidates
        if candidate.risk.premiere_constraint.scope.value
        in {"continental", "territorial"}
        and candidate.risk.premiere_constraint.territory
    }
    screenings = tuple(
        sorted(
            evidence.screenings,
            key=lambda item: (
                item.occurred_at
                or item.scheduled_at
                or datetime.max.replace(tzinfo=timezone.utc),
                item.screening_id,
            ),
        )
    )
    candidates = tuple(
        sorted(
            evidence.candidates,
            key=lambda item: (
                item.retrieved.retrieval_rank,
                item.festival_id,
            ),
        )
    )
    ledger = PremiereLedger().derive(
        evidence.profile, screenings, tracked_scopes=tracked
    )
    initial = CampaignSnapshot(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        campaign_version=0,
        lifecycle=CampaignLifecycle.ACTIVE,
        readiness=CampaignReadiness.STALE,
        profile=evidence.profile,
        premiere_ledger=ledger,
        screenings=screenings,
        opportunities=tuple(
            CampaignOpportunity(
                opportunity_id=f"opportunity:{campaign_id}:{item.festival_id}",
                festival_id=item.festival_id,
                verification_items=item.risk.uncertainties,
            )
            for item in candidates
        ),
        candidates=candidates,
        aggregate_hash="0" * 64,
    )
    return initial.model_copy(
        update={"aggregate_hash": campaign_snapshot_hash(initial)}
    )


__all__ = [
    "CacheMissRequiresRefresh",
    "CampaignOrchestrationError",
    "CampaignOrchestrator",
    "ORCHESTRATOR_POLICY_VERSION",
    "PlanningExecution",
    "snapshot_from_evidence",
]
