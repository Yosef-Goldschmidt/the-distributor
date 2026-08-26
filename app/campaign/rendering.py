"""Deterministic output models for the campaign API and compact workspace UI."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from pydantic import Field

from app.campaign.models import (
    CampaignPlan,
    CampaignSnapshot,
    FrozenModel,
    HardBudgetState,
    ReuseManifest,
    StrategyDiff,
)


class FestivalRouteView(FrozenModel):
    festival_id: str
    festival_name: str
    score: int
    decision_grade: str
    role: str


class EvidenceReuseView(FrozenModel):
    evidence_reused: bool
    no_new_llm: bool
    chat_attempts: int
    embedding_attempts: int
    reused_artifacts: tuple[str, ...] = ()
    rerun_artifacts: tuple[str, ...] = ()
    cache_status: str = "valid"


class StrategyChangeView(FrozenModel):
    primary_changed: bool
    primary_before: str | None = None
    primary_after: str | None = None
    added_routes: tuple[str, ...] = ()
    removed_routes: tuple[str, ...] = ()
    unchanged_routes: tuple[str, ...] = ()
    causal_refs: tuple[str, ...] = ()


class CampaignWorkspaceView(FrozenModel):
    campaign_id: str
    campaign_version: int
    lifecycle: str
    readiness: str
    strategy_ref: str | None = None
    primary: FestivalRouteView | None = None
    alternatives: tuple[FestivalRouteView, ...] = ()
    post_premiere: tuple[FestivalRouteView, ...] = ()
    known_preserved_pct: Decimal | None = None
    possible_additional_pct: Decimal | None = None
    known_destroyed_pct: Decimal | None = None
    verification_gates: tuple[Mapping[str, Any], ...] = ()
    clarifications: tuple[Mapping[str, Any], ...] = ()
    budget_state: HardBudgetState | None = None
    reuse: EvidenceReuseView | None = None
    change: StrategyChangeView | None = None
    company_memory: Mapping[str, Any] = Field(default_factory=dict)


class CampaignRenderer:
    """Project server-authoritative state and plan without generative prose."""

    def render(
        self,
        snapshot: CampaignSnapshot,
        plan: CampaignPlan | None,
        *,
        strategy_ref: str | None = None,
        diff: StrategyDiff | None = None,
        reuse_manifest: ReuseManifest | None = None,
        company_memory: Mapping[str, Any] | None = None,
        cache_status: str = "valid",
    ) -> CampaignWorkspaceView:
        candidates = {item.festival_id: item for item in snapshot.candidates}

        def route(festival_id: str, role: str) -> FestivalRouteView:
            candidate = candidates[festival_id]
            return FestivalRouteView(
                festival_id=festival_id,
                festival_name=candidate.retrieved.identity.festival_name,
                score=candidate.score_breakdown.score,
                decision_grade=candidate.decision_grade.value,
                role=role,
            )

        reuse = None
        if reuse_manifest is not None:
            reuse = EvidenceReuseView(
                evidence_reused=bool(reuse_manifest.reused_artifacts),
                no_new_llm=(
                    reuse_manifest.chat_attempts == 0
                    and reuse_manifest.embedding_attempts == 0
                ),
                chat_attempts=reuse_manifest.chat_attempts,
                embedding_attempts=reuse_manifest.embedding_attempts,
                reused_artifacts=tuple(
                    item.value for item in reuse_manifest.reused_artifacts
                ),
                rerun_artifacts=tuple(
                    item.value for item in reuse_manifest.rerun_artifacts
                ),
                cache_status=cache_status,
            )
        change = None
        if diff is not None:
            change = StrategyChangeView(
                primary_changed=diff.primary_before != diff.primary_after,
                primary_before=diff.primary_before,
                primary_after=diff.primary_after,
                added_routes=diff.added_route_ids,
                removed_routes=diff.removed_route_ids,
                unchanged_routes=diff.unchanged_route_ids,
                causal_refs=diff.causal_refs,
            )
        return CampaignWorkspaceView(
            campaign_id=snapshot.campaign_id,
            campaign_version=snapshot.campaign_version,
            lifecycle=snapshot.lifecycle.value,
            readiness=snapshot.readiness.value,
            strategy_ref=strategy_ref or snapshot.active_strategy_ref,
            primary=(
                route(plan.primary_launch.festival_id, "primary") if plan else None
            ),
            alternatives=(
                tuple(
                    route(item.festival_id, "alternative")
                    for item in plan.alternative_launches
                )
                if plan
                else ()
            ),
            post_premiere=(
                tuple(route(item, "post_premiere") for item in plan.post_premiere_opportunities)
                if plan
                else ()
            ),
            known_preserved_pct=(
                plan.option_preservation.known_preserved_pct if plan else None
            ),
            possible_additional_pct=(
                plan.option_preservation.possible_additional_pct if plan else None
            ),
            known_destroyed_pct=(
                plan.option_preservation.known_destroyed_pct if plan else None
            ),
            verification_gates=(
                tuple(item.model_dump(mode="json", by_alias=True) for item in plan.verification_gates)
                if plan
                else ()
            ),
            clarifications=(
                tuple(item.model_dump(mode="json") for item in plan.clarifications)
                if plan
                else ()
            ),
            budget_state=plan.budget.state if plan and plan.budget else None,
            reuse=reuse,
            change=change,
            company_memory=company_memory or {},
        )


__all__ = [
    "CampaignRenderer",
    "CampaignWorkspaceView",
    "EvidenceReuseView",
    "FestivalRouteView",
    "StrategyChangeView",
]
