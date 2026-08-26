"""Campaign persistence contracts and transactional repository implementations.

Production mutations are deliberately limited to one SQL RPC per transaction.
The in-memory implementation is a deterministic contract double used to prove
version, idempotency, isolation, ordering, and rollback semantics without a
live Supabase project.
"""

from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping, Protocol

from app.campaign.auth import capability_digests_equal
from app.campaign.contracts import canonical_hash, campaign_snapshot_hash
from app.campaign.models import (
    CampaignCommand,
    CampaignEvent,
    CampaignOpportunity,
    CampaignPlan,
    CampaignReadiness,
    CampaignSnapshot,
    FrozenCandidateEvidence,
    StrategyDiff,
)
from app.campaign.state import (
    CampaignStateReducer,
    InvalidTransition,
    StateReduction,
    VersionConflict,
)


class CampaignRepositoryError(RuntimeError):
    """Base persistence error."""


class CampaignNotFound(CampaignRepositoryError):
    """Returned for both absent and out-of-workspace campaigns."""


class WorkspaceNotFound(CampaignRepositoryError):
    """No workspace matches the server-side capability digest."""


class IdempotencyConflict(CampaignRepositoryError):
    """An idempotency key was reused for a different typed command."""


class StrategyActivationConflict(CampaignRepositoryError):
    """A strategy was computed from a campaign version that is no longer current."""


@dataclass(frozen=True, slots=True)
class StrategyAttempt:
    strategy_id: str
    based_on_campaign_version: int
    outcome: Literal["ready", "failed"]
    input_snapshot_json: Mapping[str, Any]
    input_hash: str
    plan: CampaignPlan | None = None
    diff: StrategyDiff | None = None
    trace_json: Mapping[str, Any] = field(default_factory=dict)
    reuse_manifest_json: Mapping[str, Any] = field(default_factory=dict)
    usage_json: Mapping[str, Any] = field(default_factory=dict)
    policy_versions: tuple[str, ...] = ()
    model_versions: tuple[str, ...] = ()
    error_json: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.based_on_campaign_version < 0:
            raise ValueError("based_on_campaign_version must be nonnegative")
        if len(self.input_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.input_hash
        ):
            raise ValueError("input_hash must be a lowercase SHA-256 digest")
        if self.outcome == "ready" and self.plan is None:
            raise ValueError("ready strategies require a validated plan")
        if self.outcome == "failed" and self.plan is not None:
            raise ValueError("failed strategy attempts cannot carry a ready plan")


@dataclass(frozen=True, slots=True)
class StoredStrategyVersion:
    strategy_no: int
    attempt: StrategyAttempt
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CampaignAggregate:
    snapshot: CampaignSnapshot
    events: tuple[CampaignEvent, ...]
    strategy_versions: tuple[StoredStrategyVersion, ...]
    strategy_stale: bool


@dataclass(frozen=True, slots=True)
class CommandApplication:
    aggregate: CampaignAggregate
    event: CampaignEvent
    idempotent_replay: bool = False


class CampaignRepository(Protocol):
    def resolve_workspace(self, capability_digest: str) -> str: ...

    def create_workspace(
        self,
        workspace_id: str,
        capability_digest: str,
        *,
        display_name: str,
        company_id: str | None = None,
    ) -> None: ...

    def save_campaign(self, workspace_id: str, snapshot: CampaignSnapshot) -> None: ...

    def list_campaigns(self, workspace_id: str) -> tuple[CampaignAggregate, ...]: ...

    def load_campaign(self, workspace_id: str, campaign_id: str) -> CampaignAggregate: ...

    def apply_command(
        self, workspace_id: str, campaign_id: str, command: CampaignCommand
    ) -> CommandApplication: ...

    def record_strategy_attempt(
        self, workspace_id: str, campaign_id: str, attempt: StrategyAttempt
    ) -> CampaignAggregate: ...


@dataclass(slots=True)
class _Replay:
    command_hash: str
    snapshot: CampaignSnapshot
    event: CampaignEvent
    event_count: int
    strategy_count: int


@dataclass(slots=True)
class _StoredCampaign:
    snapshot: CampaignSnapshot
    events: list[CampaignEvent] = field(default_factory=list)
    strategies: list[StoredStrategyVersion] = field(default_factory=list)
    idempotency: dict[str, _Replay] = field(default_factory=dict)
    strategy_stale: bool = True


class InMemoryCampaignRepository:
    """Serializable local implementation of the production RPC contract."""

    def __init__(
        self,
        *,
        reducer: CampaignStateReducer | None = None,
        clock: Callable[[], datetime] | None = None,
        before_commit: Callable[[StateReduction], None] | None = None,
    ) -> None:
        from threading import RLock

        self._reducer = reducer or CampaignStateReducer()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._before_commit = before_commit
        self._lock = RLock()
        self._workspace_digests: dict[str, str] = {}
        self._campaigns: dict[str, _StoredCampaign] = {}

    def register_workspace(self, workspace_id: str, capability_digest: str) -> None:
        with self._lock:
            if workspace_id in self._workspace_digests:
                raise CampaignRepositoryError(f"workspace already exists: {workspace_id}")
            if any(
                capability_digests_equal(capability_digest, existing)
                for existing in self._workspace_digests.values()
            ):
                raise CampaignRepositoryError("capability digest must be unique")
            self._workspace_digests[workspace_id] = capability_digest

    def create_workspace(
        self,
        workspace_id: str,
        capability_digest: str,
        *,
        display_name: str,
        company_id: str | None = None,
    ) -> None:
        del display_name, company_id
        self.register_workspace(workspace_id, capability_digest)

    def resolve_workspace(self, capability_digest: str) -> str:
        with self._lock:
            for workspace_id, stored in self._workspace_digests.items():
                if capability_digests_equal(stored, capability_digest):
                    return workspace_id
        raise WorkspaceNotFound("workspace capability is not recognized")

    def save_campaign(self, workspace_id: str, snapshot: CampaignSnapshot) -> None:
        """Persist an initial aggregate projection; later changes use commands only."""

        with self._lock:
            self._require_workspace(workspace_id)
            if snapshot.workspace_id != workspace_id:
                raise CampaignNotFound("campaign not found")
            if snapshot.campaign_id in self._campaigns:
                raise CampaignRepositoryError(
                    f"campaign already exists: {snapshot.campaign_id}"
                )
            if campaign_snapshot_hash(snapshot) != snapshot.aggregate_hash:
                raise CampaignRepositoryError("cannot save an invalid aggregate hash")
            self._campaigns[snapshot.campaign_id] = _StoredCampaign(
                snapshot=self._clone_snapshot(snapshot),
                strategy_stale=snapshot.readiness == CampaignReadiness.STALE,
            )

    def load_campaign(self, workspace_id: str, campaign_id: str) -> CampaignAggregate:
        with self._lock:
            stored = self._stored_for_workspace(workspace_id, campaign_id)
            return self._aggregate(stored)

    def list_campaigns(self, workspace_id: str) -> tuple[CampaignAggregate, ...]:
        with self._lock:
            self._require_workspace(workspace_id)
            return tuple(
                self._aggregate(stored)
                for campaign_id, stored in sorted(self._campaigns.items())
                if stored.snapshot.workspace_id == workspace_id
            )

    def apply_command(
        self, workspace_id: str, campaign_id: str, command: CampaignCommand
    ) -> CommandApplication:
        with self._lock:
            stored = self._stored_for_workspace(workspace_id, campaign_id)
            command_hash = canonical_hash(command)
            replay = stored.idempotency.get(command.idempotency_key)
            if replay is not None:
                if not capability_digests_equal(replay.command_hash, command_hash):
                    raise IdempotencyConflict(
                        "idempotency key was already committed for another command"
                    )
                replay_aggregate = CampaignAggregate(
                    snapshot=self._clone_snapshot(replay.snapshot),
                    events=tuple(stored.events[: replay.event_count]),
                    strategy_versions=tuple(stored.strategies[: replay.strategy_count]),
                    strategy_stale=True,
                )
                return CommandApplication(
                    aggregate=replay_aggregate,
                    event=replay.event,
                    idempotent_replay=True,
                )

            if command.expected_version != stored.snapshot.campaign_version:
                raise VersionConflict(
                    command.expected_version, stored.snapshot.campaign_version
                )

            prior_event = None
            if command.type.value == "correct_record":
                prior_event = next(
                    (
                        event
                        for event in stored.events
                        if event.event_id == command.payload.prior_ref
                    ),
                    None,
                )
            sequence_no = len(stored.events) + 1
            reduction = self._reducer.reduce(
                stored.snapshot,
                command,
                event_id=f"event:{campaign_id}:{sequence_no:06d}",
                sequence_no=sequence_no,
                occurred_at=self._clock(),
                prior_event=prior_event,
            )

            # Failure before this commit point leaves every stored projection intact.
            if self._before_commit is not None:
                self._before_commit(reduction)

            stored.snapshot = self._clone_snapshot(reduction.snapshot)
            stored.events.append(reduction.event)
            stored.strategy_stale = True
            stored.idempotency[command.idempotency_key] = _Replay(
                command_hash=command_hash,
                snapshot=self._clone_snapshot(reduction.snapshot),
                event=reduction.event,
                event_count=len(stored.events),
                strategy_count=len(stored.strategies),
            )
            return CommandApplication(
                aggregate=self._aggregate(stored),
                event=reduction.event,
            )

    def record_strategy_attempt(
        self, workspace_id: str, campaign_id: str, attempt: StrategyAttempt
    ) -> CampaignAggregate:
        with self._lock:
            stored = self._stored_for_workspace(workspace_id, campaign_id)
            if attempt.based_on_campaign_version != stored.snapshot.campaign_version:
                raise StrategyActivationConflict(
                    "campaign changed after strategy input was captured"
                )
            if any(
                item.attempt.strategy_id == attempt.strategy_id
                for item in stored.strategies
            ):
                raise CampaignRepositoryError(
                    f"strategy already exists: {attempt.strategy_id}"
                )
            version = StoredStrategyVersion(
                strategy_no=len(stored.strategies) + 1,
                attempt=deepcopy(attempt),
                created_at=self._clock(),
            )
            stored.strategies.append(version)
            if attempt.outcome == "ready":
                candidates, opportunities = self._strategy_projection(
                    attempt, stored.snapshot
                )
                provisional = stored.snapshot.model_copy(
                    update={
                        "active_strategy_ref": attempt.strategy_id,
                        "readiness": CampaignReadiness.READY,
                        "candidates": candidates,
                        "opportunities": opportunities,
                        "aggregate_hash": "0" * 64,
                    }
                )
                stored.snapshot = provisional.model_copy(
                    update={"aggregate_hash": campaign_snapshot_hash(provisional)}
                )
                stored.strategy_stale = False
            else:
                stored.strategy_stale = True
            return self._aggregate(stored)

    @staticmethod
    def _strategy_projection(
        attempt: StrategyAttempt, snapshot: CampaignSnapshot
    ) -> tuple[
        tuple[FrozenCandidateEvidence, ...],
        tuple[CampaignOpportunity, ...],
    ]:
        raw_candidates = attempt.input_snapshot_json.get("candidates")
        if raw_candidates is None:
            return snapshot.candidates, snapshot.opportunities
        raw_ids = frozenset(
            str(item.get("festival_id"))
            for item in raw_candidates
            if isinstance(item, Mapping) and item.get("festival_id")
        )
        try:
            candidates = tuple(
                FrozenCandidateEvidence.model_validate(
                    item, context={"known_festival_ids": raw_ids}
                )
                for item in raw_candidates
            )
        except Exception as exc:  # noqa: BLE001 - reject a corrupt activation projection
            raise CampaignRepositoryError(
                "ready strategy carries an invalid candidate projection"
            ) from exc
        if not candidates or {item.festival_id for item in candidates} != raw_ids:
            raise CampaignRepositoryError(
                "ready strategy candidate projection has invalid canonical IDs"
            )
        raw_opportunities = attempt.input_snapshot_json.get("opportunities")
        if raw_opportunities is None:
            if not raw_ids <= {
                item.festival_id for item in snapshot.opportunities
            }:
                raise CampaignRepositoryError(
                    "new candidate projections require typed opportunities"
                )
            return candidates, snapshot.opportunities
        try:
            current_opportunities = tuple(
                CampaignOpportunity.model_validate(
                    item, context={"known_festival_ids": raw_ids}
                )
                for item in raw_opportunities
            )
        except Exception as exc:  # noqa: BLE001
            raise CampaignRepositoryError(
                "ready strategy carries an invalid opportunity projection"
            ) from exc
        if {item.festival_id for item in current_opportunities} != raw_ids:
            raise CampaignRepositoryError(
                "strategy candidates and current opportunities must share IDs"
            )
        merged = {item.festival_id: item for item in snapshot.opportunities}
        merged.update(
            {item.festival_id: item for item in current_opportunities}
        )
        return candidates, tuple(merged[item] for item in sorted(merged))

    def _require_workspace(self, workspace_id: str) -> None:
        if workspace_id not in self._workspace_digests:
            raise WorkspaceNotFound("workspace capability is not recognized")

    def _stored_for_workspace(
        self, workspace_id: str, campaign_id: str
    ) -> _StoredCampaign:
        self._require_workspace(workspace_id)
        stored = self._campaigns.get(campaign_id)
        if stored is None or stored.snapshot.workspace_id != workspace_id:
            raise CampaignNotFound("campaign not found")
        return stored

    @staticmethod
    def _clone_snapshot(snapshot: CampaignSnapshot) -> CampaignSnapshot:
        return CampaignSnapshot.model_validate(snapshot.model_dump(mode="python"))

    def _aggregate(self, stored: _StoredCampaign) -> CampaignAggregate:
        events = tuple(
            CampaignEvent.model_validate(event.model_dump(mode="python"))
            for event in stored.events
        )
        return CampaignAggregate(
            snapshot=self._clone_snapshot(stored.snapshot),
            events=events,
            strategy_versions=tuple(deepcopy(stored.strategies)),
            strategy_stale=stored.strategy_stale,
        )


class SupabaseCampaignRepository:
    """Server-only adapter that delegates each write to one transactional RPC."""

    def __init__(self, client: Any) -> None:
        if client is None:
            raise ValueError("a server-role Supabase client is required")
        self._client = client

    @classmethod
    def from_environment(cls) -> SupabaseCampaignRepository:
        url = os.getenv("SUPABASE_URL")
        service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not service_key:
            raise CampaignRepositoryError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required; "
                "campaign writes never fall back to the public anonymous key"
            )
        from supabase import create_client

        return cls(create_client(url, service_key))

    def resolve_workspace(self, capability_digest: str) -> str:
        response = (
            self._client.table("workspaces")
            .select("id")
            .eq("capability_digest", capability_digest)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise WorkspaceNotFound("workspace capability is not recognized")
        return str(rows[0]["id"])

    def create_workspace(
        self,
        workspace_id: str,
        capability_digest: str,
        *,
        display_name: str,
        company_id: str | None = None,
    ) -> None:
        self._client.table("workspaces").insert(
            {
                "id": workspace_id,
                "company_id": company_id,
                "capability_digest": capability_digest,
                "display_name": display_name,
            }
        ).execute()

    def save_campaign(self, workspace_id: str, snapshot: CampaignSnapshot) -> None:
        self._rpc(
            "create_campaign_from_snapshot",
            {
                "p_workspace_id": workspace_id,
                "p_snapshot": snapshot.model_dump(mode="json"),
            },
        )

    def list_campaigns(self, workspace_id: str) -> tuple[CampaignAggregate, ...]:
        projects = (
            self._client.table("film_projects")
            .select("id")
            .eq("workspace_id", workspace_id)
            .execute()
        ).data or []
        project_ids = [str(item["id"]) for item in projects]
        if not project_ids:
            return ()
        campaigns = (
            self._client.table("campaigns")
            .select("id")
            .in_("film_project_id", project_ids)
            .order("created_at")
            .execute()
        ).data or []
        return tuple(
            self.load_campaign(workspace_id, str(item["id"]))
            for item in campaigns
        )

    def load_campaign(self, workspace_id: str, campaign_id: str) -> CampaignAggregate:
        payload = self._rpc(
            "get_campaign_aggregate",
            {"p_workspace_id": workspace_id, "p_campaign_id": campaign_id},
        )
        return self._parse_aggregate(payload)

    def apply_command(
        self, workspace_id: str, campaign_id: str, command: CampaignCommand
    ) -> CommandApplication:
        payload = self._rpc(
            "apply_campaign_command",
            {
                "p_workspace_id": workspace_id,
                "p_campaign_id": campaign_id,
                "p_expected_version": command.expected_version,
                "p_idempotency_key": command.idempotency_key,
                "p_command": command.model_dump(mode="json"),
            },
        )
        aggregate = self._parse_aggregate(payload["aggregate"])
        event = CampaignEvent.model_validate(payload["event"])
        return CommandApplication(
            aggregate=aggregate,
            event=event,
            idempotent_replay=bool(payload.get("idempotent_replay", False)),
        )

    def record_strategy_attempt(
        self, workspace_id: str, campaign_id: str, attempt: StrategyAttempt
    ) -> CampaignAggregate:
        payload = self._rpc(
            "activate_campaign_strategy",
            {
                "p_workspace_id": workspace_id,
                "p_campaign_id": campaign_id,
                "p_based_on_campaign_version": attempt.based_on_campaign_version,
                "p_attempt": self._attempt_payload(attempt),
            },
        )
        return self._parse_aggregate(payload)

    def _rpc(self, name: str, params: Mapping[str, Any]) -> Any:
        try:
            response = self._client.rpc(name, dict(params)).execute()
        except Exception as exc:  # noqa: BLE001 - normalize PostgREST SQL errors
            payload = exc.args[0] if exc.args and isinstance(exc.args[0], Mapping) else {}
            message = str(payload.get("message") or getattr(exc, "message", "") or exc)
            detail = str(payload.get("details") or getattr(exc, "details", "") or "")
            if "workspace_not_found" in message:
                raise WorkspaceNotFound("workspace capability is not recognized") from exc
            if "campaign_not_found" in message:
                raise CampaignNotFound("campaign not found") from exc
            if "idempotency_conflict" in message:
                raise IdempotencyConflict(
                    "idempotency key was already committed for another command"
                ) from exc
            if "version_conflict" in message:
                expected = int(params.get("p_expected_version", -1))
                current = int(detail) if detail.isdigit() else max(expected, 0)
                raise VersionConflict(expected, current) from exc
            if "strategy_activation_conflict" in message:
                raise StrategyActivationConflict(
                    "campaign changed after strategy input was captured"
                ) from exc
            transition_codes = (
                "campaign_closed",
                "constraint_deactivation_requires_remove",
                "locked_constraint",
                "constraint_not_removable",
                "invalid_opportunity_policy_transition",
                "invalid_submission_transition",
                "invalid_rejection_transition",
                "invalid_invitation_transition",
                "invalid_offer_transition",
                "invalid_withdrawal_transition",
                "invalid_screening_confirmation",
                "invalid_screening_cancellation",
                "verification_item_not_found",
                "correction_target_not_found",
                "correction_screening_not_found",
                "ambiguous_profile_correction",
                "ambiguous_correction_target",
            )
            transition = next(
                (code for code in transition_codes if code in message), None
            )
            if transition:
                raise InvalidTransition(transition) from exc
            raise CampaignRepositoryError(f"{name} failed") from exc
        if response.data is None:
            raise CampaignRepositoryError(f"{name} returned no authoritative aggregate")
        return response.data

    @staticmethod
    def _attempt_payload(attempt: StrategyAttempt) -> dict[str, Any]:
        return {
            "strategy_id": attempt.strategy_id,
            "outcome": attempt.outcome,
            "input_snapshot_json": deepcopy(dict(attempt.input_snapshot_json)),
            "input_hash": attempt.input_hash,
            "plan_json": attempt.plan.model_dump(mode="json") if attempt.plan else None,
            "diff_json": attempt.diff.model_dump(mode="json") if attempt.diff else None,
            "trace_json": deepcopy(dict(attempt.trace_json)),
            "reuse_manifest_json": deepcopy(dict(attempt.reuse_manifest_json)),
            "usage_json": deepcopy(dict(attempt.usage_json)),
            "policy_versions": list(attempt.policy_versions),
            "model_versions": list(attempt.model_versions),
            "error_json": deepcopy(dict(attempt.error_json)) if attempt.error_json else None,
        }

    @staticmethod
    def _parse_aggregate(payload: Mapping[str, Any]) -> CampaignAggregate:
        snapshot = CampaignSnapshot.model_validate(payload["snapshot"])
        events = tuple(
            CampaignEvent.model_validate(item) for item in payload.get("events", ())
        )
        strategies: list[StoredStrategyVersion] = []
        for item in payload.get("strategy_versions", ()):
            attempt = StrategyAttempt(
                strategy_id=item["strategy_id"],
                based_on_campaign_version=item["based_on_campaign_version"],
                outcome=item["outcome"],
                input_snapshot_json=item["input_snapshot_json"],
                input_hash=item["input_hash"],
                plan=CampaignPlan.model_validate(item["plan_json"])
                if item.get("plan_json")
                else None,
                diff=StrategyDiff.model_validate(item["diff_json"])
                if item.get("diff_json")
                else None,
                trace_json=item.get("trace_json") or {},
                reuse_manifest_json=item.get("reuse_manifest_json") or {},
                usage_json=item.get("usage_json") or {},
                policy_versions=tuple(item.get("policy_versions") or ()),
                model_versions=tuple(item.get("model_versions") or ()),
                error_json=item.get("error_json"),
            )
            created_at = item["created_at"]
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            strategies.append(
                StoredStrategyVersion(
                    strategy_no=item["strategy_no"],
                    attempt=attempt,
                    created_at=created_at,
                )
            )
        return CampaignAggregate(
            snapshot=snapshot,
            events=events,
            strategy_versions=tuple(strategies),
            strategy_stale=bool(payload["strategy_stale"]),
        )


__all__ = [
    "CampaignAggregate",
    "CampaignNotFound",
    "CampaignRepository",
    "CampaignRepositoryError",
    "CommandApplication",
    "IdempotencyConflict",
    "InMemoryCampaignRepository",
    "StoredStrategyVersion",
    "StrategyActivationConflict",
    "StrategyAttempt",
    "SupabaseCampaignRepository",
    "WorkspaceNotFound",
]
