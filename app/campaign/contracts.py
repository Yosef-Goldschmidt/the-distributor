"""Canonical serialization and frozen Phase 0 policy/protocol constants.

Nothing in this module performs campaign runtime work. It owns contract parsing,
hashing, the baseline decision-grade declaration, and capability primitives that
later phases must consume unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Final, Protocol, TypeVar

from pydantic import BaseModel, TypeAdapter

from app.campaign.models import (
    BoundaryContractDescriptor,
    CandidateCreativeEvidence,
    CandidateRiskEvidence,
    CampaignCommand,
    CampaignCommandType,
    CampaignEventType,
    CampaignPlan,
    CampaignProfile,
    CampaignSnapshot,
    CompatibilityEdge,
    DecisionGrade,
    DecisionGradeGroup,
    ExpectedPolicyResult,
    FactStatus,
    FrozenModel,
    FrozenCandidateEvidence,
    HardBudgetState,
    PlanningInput,
    PersistenceTableContract,
    RetrievalInput,
    ReuseManifest,
    RetrievedFestivalEvidence,
    StrategyDiff,
)


class LegacyEvidenceAdapterProtocol(Protocol):
    """Future choke-point contract; no Phase 0 adapter implementation exists."""

    def build_planning_input(self, snapshot: CampaignSnapshot) -> PlanningInput: ...


class CampaignPlannerProtocol(Protocol):
    """The planner cannot accept repository rows or legacy dictionaries."""

    def plan(self, planning_input: PlanningInput) -> CampaignPlan: ...


class FestivalSearchAdapterProtocol(Protocol):
    def retrieve(self, retrieval_input: RetrievalInput) -> tuple[RetrievedFestivalEvidence, ...]: ...


class MatchScorerAdapterProtocol(Protocol):
    def score(
        self,
        profile: CampaignProfile,
        candidates: tuple[RetrievedFestivalEvidence, ...],
    ) -> tuple[CandidateCreativeEvidence, ...]: ...


class RiskCheckerAdapterProtocol(Protocol):
    def assess(
        self,
        profile: CampaignProfile,
        candidates: tuple[RetrievedFestivalEvidence, ...],
    ) -> tuple[CandidateRiskEvidence, ...]: ...


CAMPAIGN_RUNTIME_ENABLED: Final[bool] = False
CONTRACT_SCHEMA_VERSION: Final[str] = "campaign-contracts-v1"
PLANNER_POLICY_VERSION: Final[str] = "campaign-planner-policy-v2"
BUDGET_POLICY_VERSION: Final[str] = "hard-budget-required-now-v1"
CAPABILITY_ENTROPY_BYTES: Final[int] = 32

DECISION_GRADE_SOURCE: Final[str] = "app.agent.scoring.assign_bucket"
DECISION_GRADE_RULES: Final[tuple[str, ...]] = (
    "ineligible or score < 45 -> hold_avoid",
    "high premiere risk without premiere_opportunity -> hold_avoid",
    "upcoming and opening unknown or more than 42 days away -> prioritize_next",
    "closed and score < 70 -> hold_avoid; closed and score >= 70 -> prioritize_next",
    "otherwise score >= 72, or tier A/B+ and score >= 65 -> submit_first",
    "otherwise relationship >= 4 and score >= 55 -> leverage",
    "otherwise -> prioritize_next",
)
DECISION_GRADE_IS_LLM_LABEL: Final[bool] = False
DECISION_GRADE_GROUPS: Final[dict[DecisionGrade, DecisionGradeGroup]] = {
    DecisionGrade.SUBMIT_FIRST: DecisionGradeGroup.LAUNCH_READY,
    DecisionGrade.PRIORITIZE_NEXT: DecisionGradeGroup.VIABLE_NEXT,
    DecisionGrade.LEVERAGE: DecisionGradeGroup.VIABLE_NEXT,
    DecisionGrade.HOLD_AVOID: DecisionGradeGroup.NOT_A_LAUNCH_ROOT,
}

COMMAND_EVENT_TYPES: Final[dict[CampaignCommandType, CampaignEventType]] = {
    CampaignCommandType.UPDATE_PROFILE_FACT: CampaignEventType.PROFILE_FACT_UPDATED,
    CampaignCommandType.SET_CONSTRAINT: CampaignEventType.CONSTRAINT_SET,
    CampaignCommandType.REMOVE_CONSTRAINT: CampaignEventType.CONSTRAINT_REMOVED,
    CampaignCommandType.LOCK_OPPORTUNITY: CampaignEventType.OPPORTUNITY_LOCKED,
    CampaignCommandType.UNLOCK_OPPORTUNITY: CampaignEventType.OPPORTUNITY_UNLOCKED,
    CampaignCommandType.EXCLUDE_OPPORTUNITY: CampaignEventType.OPPORTUNITY_EXCLUDED,
    CampaignCommandType.INCLUDE_OPPORTUNITY: CampaignEventType.OPPORTUNITY_INCLUDED,
    CampaignCommandType.MARK_SUBMITTED: CampaignEventType.SUBMISSION_MARKED,
    CampaignCommandType.RECORD_REJECTION: CampaignEventType.REJECTION_RECORDED,
    CampaignCommandType.RECORD_INVITATION: CampaignEventType.INVITATION_RECORDED,
    CampaignCommandType.ACCEPT_OFFER: CampaignEventType.OFFER_ACCEPTED,
    CampaignCommandType.DECLINE_OFFER: CampaignEventType.OFFER_DECLINED,
    CampaignCommandType.WITHDRAW: CampaignEventType.OPPORTUNITY_WITHDRAWN,
    CampaignCommandType.SCHEDULE_SCREENING: CampaignEventType.SCREENING_SCHEDULED,
    CampaignCommandType.CONFIRM_SCREENING: CampaignEventType.SCREENING_CONFIRMED,
    CampaignCommandType.CANCEL_SCREENING: CampaignEventType.SCREENING_CANCELLED,
    CampaignCommandType.VERIFY_OPPORTUNITY_FACT: CampaignEventType.OPPORTUNITY_FACT_VERIFIED,
    CampaignCommandType.CORRECT_RECORD: CampaignEventType.RECORD_CORRECTED,
    CampaignCommandType.CLOSE_CAMPAIGN: CampaignEventType.CAMPAIGN_CLOSED,
}

CAMPAIGN_COMMAND_ADAPTER = TypeAdapter(CampaignCommand)

BOUNDARY_CONTRACTS: Final[tuple[BoundaryContractDescriptor, ...]] = (
    BoundaryContractDescriptor(
        model_name="CampaignProfile",
        producer="FilmAnalyzer/profile-command adapter",
        consumers=("retrieval", "risk", "snapshot"),
        hash_field="profile_hash",
        provenance_rule="Every identity and eligibility value is a typed Fact with source refs.",
        llm_rule="LLM-derived values are allowed only inside sourced Fact envelopes.",
    ),
    BoundaryContractDescriptor(
        model_name="RetrievalInput",
        producer="LegacyEvidenceAdapter",
        consumers=("FestivalSearch adapter",),
        hash_field="retrieval_key",
        provenance_rule="Profile hash, policies, model, corpus version, and as-of date are explicit.",
        llm_rule="The query may originate from FilmAnalyzer but is length and field validated.",
    ),
    BoundaryContractDescriptor(
        model_name="RetrievedFestivalEvidence",
        producer="FestivalSearch adapter",
        consumers=("candidate-evidence assembler",),
        hash_field="festival_facts_hash",
        provenance_rule="Retrieval sources, backend, confidence, rules, dates, and fees are explicit.",
        llm_rule="Contains no chat judgment.",
    ),
    BoundaryContractDescriptor(
        model_name="CandidateCreativeEvidence",
        producer="MatchScorer adapter plus CompanyMemory",
        consumers=("candidate-evidence assembler",),
        hash_field="creative_key",
        provenance_rule="Every dimension carries guarded rating, points, and evidence refs.",
        llm_rule="LLM ratings are allowed only after schema, range, evidence, ID, and guardrail validation.",
    ),
    BoundaryContractDescriptor(
        model_name="CandidateRiskEvidence",
        producer="RiskChecker adapter",
        consumers=("candidate-evidence assembler", "CompatibilityBuilder"),
        hash_field="risk_key",
        provenance_rule="Rule, deadline, eligibility, uncertainty, policy, and input hashes are explicit.",
        llm_rule="No LLM values are allowed.",
    ),
    BoundaryContractDescriptor(
        model_name="FrozenCandidateEvidence",
        producer="candidate-evidence assembler",
        consumers=("opportunity projection", "PlanningInput"),
        hash_field="component_hash",
        provenance_rule="Exactly one ID-matched retrieved, creative, and risk record is frozen.",
        llm_rule="Only guarded creative evidence may contain prior LLM judgments.",
    ),
    BoundaryContractDescriptor(
        model_name="CampaignSnapshot",
        producer="repository/state reducer",
        consumers=("orchestrator", "scenario engine"),
        hash_field="aggregate_hash",
        provenance_rule="Complete current aggregate and evidence references are frozen by version.",
        llm_rule="LLM values may occur only in typed profile and creative evidence.",
    ),
    BoundaryContractDescriptor(
        model_name="CompatibilityEdge",
        producer="CompatibilityBuilder",
        consumers=("PlanningInput",),
        hash_field="edge_hash",
        provenance_rule="Reason code, affected scope, evidence refs, confidence, and policy are explicit.",
        llm_rule="Fully deterministic; no LLM values.",
    ),
    BoundaryContractDescriptor(
        model_name="PlanningInput",
        producer="CampaignOrchestrator",
        consumers=("CampaignPlanner", "ClarificationEngine", "StrategyValidator"),
        hash_field="planning_input_hash",
        provenance_rule="Complete candidate, graph, policy, budget, state, constraint, and as-of snapshot.",
        llm_rule="CampaignPlanner accepts this model only and never consumes raw legacy dictionaries.",
    ),
    BoundaryContractDescriptor(
        model_name="CampaignPlan",
        producer="CampaignPlanner",
        consumers=("validator", "renderer", "persistence/API"),
        hash_field="plan_hash",
        provenance_rule="Route decisions cite reason/evidence refs and typed diagnostics.",
        llm_rule="No newly generated LLM values are allowed.",
    ),
    BoundaryContractDescriptor(
        model_name="ReuseManifest",
        producer="CampaignOrchestrator",
        consumers=("strategy version", "trace"),
        hash_field="prior_artifact_keys/current_artifact_keys",
        provenance_rule="Every reuse/rerun decision names existing prior/current artifact hashes.",
        llm_rule="Fully deterministic; provider-attempt counts are factual.",
    ),
    BoundaryContractDescriptor(
        model_name="StrategyDiff",
        producer="deterministic diff engine",
        consumers=("strategy version", "API/UI"),
        hash_field="diff_hash",
        provenance_rule="Causal event/evidence refs and reuse summary are explicit.",
        llm_rule="No prose comparison is authoritative and no new LLM value is allowed.",
    ),
)

PERSISTENCE_TABLE_CONTRACTS: Final[tuple[PersistenceTableContract, ...]] = (
    PersistenceTableContract(
        table_name="workspaces",
        essential_fields=("id", "company_id", "capability_digest", "display_name", "created_at", "last_seen_at"),
        constraint_summary="Unique capability digest; browser roles cannot select rows.",
    ),
    PersistenceTableContract(
        table_name="film_projects",
        essential_fields=("id", "workspace_id", "title", "profile_json", "profile_hash", "created_at", "updated_at"),
        constraint_summary="Workspace-scoped current typed-fact profile projection.",
    ),
    PersistenceTableContract(
        table_name="campaigns",
        essential_fields=("id", "film_project_id", "lifecycle", "version", "readiness", "premiere_ledger_json", "ledger_hash", "active_strategy_version_id", "strategy_stale"),
        constraint_summary="One active campaign per film in v2; version increments per accepted command.",
    ),
    PersistenceTableContract(
        table_name="campaign_constraints",
        essential_fields=("id", "campaign_id", "type", "strength", "payload_json", "locked", "active", "source"),
        constraint_summary="Known types only; locked constraints require an explicit human unlock.",
    ),
    PersistenceTableContract(
        table_name="campaign_events",
        essential_fields=("id", "campaign_id", "sequence_no", "type", "payload_json", "actor", "idempotency_key", "before_aggregate_hash", "after_aggregate_hash", "created_at"),
        constraint_summary="Append-only; campaign sequence and idempotency keys are unique.",
    ),
    PersistenceTableContract(
        table_name="campaign_opportunities",
        essential_fields=("id", "campaign_id", "festival_id", "submission_state", "offer_state", "policy_state", "evidence_json", "creative_scores_json", "risk_json", "verification_items_json"),
        constraint_summary="Unique campaign plus canonical festivals.id; current operational projection only.",
    ),
    PersistenceTableContract(
        table_name="screenings",
        essential_fields=("id", "campaign_id", "opportunity_id", "festival_id", "venue", "country", "region", "scheduled_at", "occurred_at", "state", "access", "source_refs"),
        constraint_summary="Multiple occurrence rows are allowed; only confirmed occurred public evidence can consume a premiere.",
    ),
    PersistenceTableContract(
        table_name="strategy_versions",
        essential_fields=("id", "campaign_id", "strategy_no", "based_on_campaign_version", "outcome", "input_snapshot_json", "input_hash", "plan_json", "diff_json", "trace_json", "reuse_manifest_json", "usage_json", "created_at"),
        constraint_summary="Immutable attempts; unique strategy number; campaign points to an active ready version.",
    ),
)

VOLATILE_DISPLAY_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "submission_action",
        "question",
        "description",
        "message",
        "display_name",
        "festival_name",
    }
)


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return "0" if normalized == 0 else format(normalized, "f")


def _canonical_value(value: Any, excluded: frozenset[str]) -> Any:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", by_alias=True)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item, excluded)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key) not in excluded
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item, excluded) for item in value]
    if isinstance(value, (set, frozenset)):
        rendered = [_canonical_value(item, excluded) for item in value]
        return sorted(rendered, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def canonical_json(value: Any, *, exclude_fields: frozenset[str] = frozenset()) -> str:
    """Return deterministic UTF-8 JSON with normalized enums, decimals, and UTC."""

    canonical = _canonical_value(value, exclude_fields)
    return json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any, *, exclude_fields: frozenset[str] = frozenset()) -> str:
    return hashlib.sha256(canonical_json(value, exclude_fields=exclude_fields).encode()).hexdigest()


def campaign_profile_hash(profile: CampaignProfile) -> str:
    payload = profile.model_dump(mode="python")
    payload["themes"]["value"] = sorted(payload["themes"]["value"] or ())
    payload["target_regions"] = sorted(payload["target_regions"])
    return canonical_hash(
        payload,
        exclude_fields=frozenset({"profile_hash", "source_refs", "observed_at"}),
    )


def identity_hash(profile: CampaignProfile) -> str:
    return campaign_profile_hash(profile)


def retrieval_input_hash(retrieval: RetrievalInput) -> str:
    payload = retrieval.model_dump(mode="python")
    payload["themes"] = sorted(payload["themes"])
    payload["target_regions"] = sorted(payload["target_regions"])
    return canonical_hash(payload, exclude_fields=frozenset({"retrieval_key"}))


def retrieval_key(retrieval: RetrievalInput) -> str:
    return retrieval_input_hash(retrieval)


def creative_evidence_hash(evidence: CandidateCreativeEvidence) -> str:
    payload = {
        "schema_version": evidence.schema_version,
        "festival_id": evidence.festival_id,
        "profile_hash": evidence.profile_hash,
        "festival_facts_hash": evidence.festival_facts_hash,
        "company_relationship": evidence.company_relationship,
        "prompt_version": evidence.prompt_version,
        "model_version": evidence.model_version,
        "guardrail_version": evidence.guardrail_version,
    }
    return canonical_hash(payload)


def creative_key(evidence: CandidateCreativeEvidence) -> str:
    return creative_evidence_hash(evidence)


def risk_evidence_hash(evidence: CandidateRiskEvidence) -> str:
    payload = {
        "schema_version": evidence.schema_version,
        "festival_id": evidence.festival_id,
        "profile_hash": evidence.profile_hash,
        "festival_facts_hash": evidence.festival_facts_hash,
        "as_of_date": evidence.as_of_date,
        "risk_policy_version": evidence.risk_policy_version,
        "risk_input_hash": evidence.risk_input_hash,
    }
    return canonical_hash(payload)


def risk_key(evidence: CandidateRiskEvidence) -> str:
    return risk_evidence_hash(evidence)


def frozen_candidate_hash(candidate: FrozenCandidateEvidence) -> str:
    return canonical_hash(
        candidate,
        exclude_fields=frozenset({"component_hash"}) | VOLATILE_DISPLAY_FIELDS,
    )


def compatibility_edge_hash(edge: CompatibilityEdge) -> str:
    return canonical_hash(edge, exclude_fields=frozenset({"edge_hash"}))


def planning_input_hash(planning_input: PlanningInput) -> str:
    payload = planning_input.model_dump(mode="python")
    payload["candidates"] = sorted(payload["candidates"], key=lambda item: item["festival_id"])
    payload["compatibility_edges"] = sorted(
        payload["compatibility_edges"],
        key=lambda item: (item["from_festival_id"], item["to_festival_id"]),
    )
    payload["opportunities"] = sorted(
        payload["opportunities"], key=lambda item: item["festival_id"]
    )
    payload["constraints"] = sorted(
        payload["constraints"], key=lambda item: item["constraint_id"]
    )
    payload["required_fees"] = sorted(payload["required_fees"], key=lambda item: item["fee_id"])
    payload["policy_versions"] = sorted(payload["policy_versions"])
    return canonical_hash(
        payload,
        exclude_fields=frozenset({"planning_input_hash"}) | VOLATILE_DISPLAY_FIELDS,
    )


def graph_key(planning_input: PlanningInput) -> str:
    payload = {
        "ledger_hash": planning_input.premiere_ledger.ledger_hash,
        "risk_keys": sorted(
            (candidate.festival_id, candidate.risk.risk_key)
            for candidate in planning_input.candidates
        ),
        "candidate_ids": sorted(planning_input.candidate_ids),
        "edge_hashes": sorted(edge.edge_hash for edge in planning_input.compatibility_edges),
        "graph_policy_versions": sorted(
            {edge.graph_policy_version for edge in planning_input.compatibility_edges}
        ),
    }
    return canonical_hash(payload)


def plan_key(planning_input: PlanningInput) -> str:
    payload = {
        "graph_key": graph_key(planning_input),
        "candidate_scores": sorted(
            (
                candidate.festival_id,
                candidate.score_breakdown.score,
                candidate.decision_grade,
                candidate.future_quality,
            )
            for candidate in planning_input.candidates
        ),
        "opportunities": sorted(
            (
                item.festival_id,
                item.submission_state,
                item.offer_state,
                item.policy_state,
            )
            for item in planning_input.opportunities
        ),
        "constraints": sorted(
            (item.constraint_id, item.model_dump(mode="python"))
            for item in planning_input.constraints
        ),
        "preservation_mode": planning_input.preservation_mode,
        "budget_constraint": planning_input.budget_constraint,
        "required_fees": sorted(
            (item.fee_id, item.model_dump(mode="python"))
            for item in planning_input.required_fees
        ),
        "as_of_date": planning_input.as_of_date,
        "policy_versions": sorted(planning_input.policy_versions),
    }
    return canonical_hash(payload)


def campaign_snapshot_hash(snapshot: CampaignSnapshot) -> str:
    payload = snapshot.model_dump(mode="python")
    payload["screenings"] = sorted(payload["screenings"], key=lambda item: item["screening_id"])
    payload["constraints"] = sorted(
        payload["constraints"], key=lambda item: item["constraint_id"]
    )
    payload["opportunities"] = sorted(
        payload["opportunities"], key=lambda item: item["festival_id"]
    )
    payload["candidates"] = sorted(payload["candidates"], key=lambda item: item["festival_id"])
    return canonical_hash(
        payload,
        exclude_fields=frozenset({"aggregate_hash"}) | VOLATILE_DISPLAY_FIELDS,
    )


def campaign_plan_hash(plan: CampaignPlan) -> str:
    return canonical_hash(
        plan,
        exclude_fields=frozenset({"plan_hash"}) | VOLATILE_DISPLAY_FIELDS,
    )


def strategy_diff_hash(diff: StrategyDiff) -> str:
    return canonical_hash(diff, exclude_fields=frozenset({"diff_hash"}))


def policy_result_hash(result: ExpectedPolicyResult) -> str:
    return canonical_hash(result, exclude_fields=frozenset({"result_hash"}))


def reuse_manifest_hash(manifest: ReuseManifest) -> str:
    return canonical_hash(manifest)


ModelT = TypeVar("ModelT", bound=FrozenModel)


def validate_with_festival_catalog(
    model_type: type[ModelT], payload: Any, known_festival_ids: set[str] | frozenset[str]
) -> ModelT:
    """Validate canonical festival IDs against a frozen catalog (never a live lookup)."""

    return model_type.model_validate(
        payload,
        context={"known_festival_ids": frozenset(known_festival_ids)},
    )


def validate_plan_against_input(payload: Any, planning_input: PlanningInput) -> CampaignPlan:
    """Apply Phase 0 cross-contract grounding without implementing StrategyValidator."""

    plan = CampaignPlan.model_validate(
        payload,
        context={"known_festival_ids": frozenset(planning_input.candidate_ids)},
    )
    hard_budget = (
        planning_input.budget_constraint
        if planning_input.budget_constraint is not None
        and planning_input.budget_constraint.hard
        else None
    )
    if hard_budget is None:
        if plan.budget is not None:
            raise ValueError("a plan without a hard budget cannot carry a hard-budget assessment")
        if any(item.budget_assessment is not None for item in plan.selection_diagnostics):
            raise ValueError("diagnostics without a hard budget cannot carry hard-budget assessments")
    else:
        if plan.budget is None:
            raise ValueError("a hard budget requires a plan budget assessment")
        if plan.budget.hard_limit != hard_budget.limit:
            raise ValueError("plan hard limit must match the PlanningInput hard budget")
        required_now = tuple(item for item in planning_input.required_fees if item.required_now)
        known_total = Decimal("0")
        unknown_fee_ids: list[str] = []
        for item in required_now:
            fee = item.fee
            if (
                fee.status in {FactStatus.UNKNOWN, FactStatus.CONTRADICTED}
                or fee.amount is None
                or fee.currency != hard_budget.limit.currency
            ):
                unknown_fee_ids.append(item.fee_id)
            else:
                known_total += fee.amount
        if known_total > hard_budget.limit.amount:
            expected_state = HardBudgetState.KNOWN_INFEASIBLE
        elif unknown_fee_ids:
            expected_state = HardBudgetState.VERIFY
        else:
            expected_state = HardBudgetState.KNOWN_FEASIBLE
        if plan.budget.state != expected_state:
            raise ValueError("plan budget state must agree with required-now fee facts")
        if plan.budget.known_total.amount != known_total:
            raise ValueError("plan known total must equal comparable required-now fees")
        if set(plan.budget.unknown_fee_ids) != set(unknown_fee_ids):
            raise ValueError("plan unknown fee IDs must match required-now fee facts")
        if set(plan.budget.included_fee_ids) != {item.fee_id for item in required_now}:
            raise ValueError("plan included fee IDs must match required-now fees")
        if set(plan.budget.required_action_ids) != {item.action_id for item in required_now}:
            raise ValueError("plan required action IDs must match required-now fees")
    edge_by_pair = {
        (edge.from_festival_id, edge.to_festival_id): edge
        for edge in planning_input.compatibility_edges
    }
    for festival_id in plan.post_premiere_opportunities:
        edge = edge_by_pair[(plan.primary_launch.festival_id, festival_id)]
        if edge.status.value == "incompatible":
            raise ValueError("post-premiere opportunity is grounded by an incompatible edge")
        if edge.status.value == "verify" and not any(
            gate.affected_decision == f"post_premiere:{festival_id}"
            for gate in plan.verification_gates
        ):
            raise ValueError("verify post-premiere opportunities require a named gate")
    return plan


def parse_campaign_command(
    payload: Any, known_festival_ids: set[str] | frozenset[str] | None = None
) -> CampaignCommand:
    context = (
        {"known_festival_ids": frozenset(known_festival_ids)}
        if known_festival_ids is not None
        else None
    )
    return CAMPAIGN_COMMAND_ADAPTER.validate_python(payload, context=context)


def generate_raw_capability() -> str:
    """Generate exactly 256 random bits and encode them as unpadded base64url."""

    return base64.urlsafe_b64encode(secrets.token_bytes(CAPABILITY_ENTROPY_BYTES)).rstrip(b"=").decode()


def capability_digest(raw_capability: str) -> str:
    """Return SHA-256(raw capability); no additional HMAC secret is introduced."""

    return hashlib.sha256(raw_capability.encode("ascii")).hexdigest()
