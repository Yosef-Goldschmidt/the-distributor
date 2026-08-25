"""Immutable Pydantic contracts for Campaign Workspace Phase 0.

The future CampaignPlanner boundary is deliberately narrow: it accepts only a
validated :class:`PlanningInput`. Legacy agent dictionaries are not valid
planner input and translation is reserved for the later LegacyEvidenceAdapter.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Generic, Literal, TypeVar, Union
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    model_validator,
)


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        value = value.replace("Z", "+00:00")
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_festival_id(value: str, info: ValidationInfo) -> str:
    catalog = (info.context or {}).get("known_festival_ids")
    if catalog is not None and value not in set(catalog):
        raise ValueError(f"unknown canonical festival_id: {value}")
    return value


UtcDateTime = Annotated[datetime, BeforeValidator(_utc_datetime)]
HashDigest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CanonicalFestivalId = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
    AfterValidator(_canonical_festival_id),
]
Identifier = Annotated[str, Field(min_length=1, max_length=200)]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=4000)]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Percentage = Annotated[Decimal, Field(ge=0, le=100)]
Score = Annotated[int, Field(ge=0, le=100)]
Rating = Annotated[Decimal, Field(ge=0, le=5)]


class FrozenModel(BaseModel):
    """Strict, immutable base shared by every frozen boundary object."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        str_strip_whitespace=True,
    )


class FactStatus(StrEnum):
    CONFIRMED = "confirmed"
    ASSERTED = "asserted"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    CONTRADICTED = "contradicted"


class CampaignLifecycle(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    POST_PREMIERE = "post_premiere"
    CLOSED = "closed"


class CampaignReadiness(StrEnum):
    NEEDS_IDENTITY = "needs_identity"
    NEEDS_PREMIERE_CLARIFICATION = "needs_premiere_clarification"
    READY = "ready"
    STALE = "stale"


class SubmissionState(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    INVITED = "invited"
    WITHDRAWN = "withdrawn"


class OfferState(StrEnum):
    NONE = "none"
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class OpportunityPolicyState(StrEnum):
    NORMAL = "normal"
    LOCKED = "locked"
    EXCLUDED = "excluded"


class ScreeningState(StrEnum):
    SCHEDULED = "scheduled"
    OCCURRED = "occurred"
    CANCELLED = "cancelled"


class ScreeningAccess(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    UNKNOWN = "unknown"


class PremiereAvailability(StrEnum):
    AVAILABLE = "available"
    CONSUMED = "consumed"
    UNKNOWN = "unknown"


class CompatibilityStatus(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    VERIFY = "verify"


class PreservationMode(StrEnum):
    BALANCED = "balanced"
    STRICT = "strict"
    OPPORTUNISTIC = "opportunistic"


class InvalidationClass(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class HardBudgetState(StrEnum):
    KNOWN_FEASIBLE = "KNOWN_FEASIBLE"
    KNOWN_INFEASIBLE = "KNOWN_INFEASIBLE"
    VERIFY = "VERIFY"


class DecisionGrade(StrEnum):
    """Exact serialized buckets returned by app.agent.scoring.assign_bucket."""

    SUBMIT_FIRST = "submit_first"
    PRIORITIZE_NEXT = "prioritize_next"
    LEVERAGE = "leverage"
    HOLD_AVOID = "hold_avoid"


class DecisionGradeGroup(StrEnum):
    """Balanced-policy grouping; never serialized as a baseline decision grade."""

    LAUNCH_READY = "launch_ready"
    VIABLE_NEXT = "viable_next"
    NOT_A_LAUNCH_ROOT = "not_a_launch_root"


class ConstraintStrength(StrEnum):
    HARD = "hard"
    PREFERENCE = "preference"


class PremiereScope(StrEnum):
    WORLD = "world"
    INTERNATIONAL = "international"
    CONTINENTAL = "continental"
    TERRITORIAL = "territorial"
    NONE = "none"
    UNKNOWN = "unknown"


class DeadlineStatus(StrEnum):
    OPEN = "open"
    CLOSING_SOON = "closing_soon"
    UPCOMING = "upcoming"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class FeeActionScope(StrEnum):
    CURRENT_ROOT = "current_root"
    LOCKED_CONCURRENT = "locked_concurrent"
    REJECTION_ALTERNATIVE = "rejection_alternative"
    SCREENING_BRANCH = "screening_branch"
    POST_PREMIERE = "post_premiere"
    HYPOTHETICAL_LATER = "hypothetical_later"


class ArtifactName(StrEnum):
    COMPANY_MEMORY = "company_memory"
    RETRIEVAL = "retrieval"
    CREATIVE_EVIDENCE = "creative_evidence"
    RISK = "risk"
    PREMIERE_LEDGER = "premiere_ledger"
    COMPATIBILITY_GRAPH = "compatibility_graph"
    PLANNER = "planner"
    CLARIFICATION = "clarification"


class ClarificationImpact(StrEnum):
    BLOCKING_HARD_DECISION = "blocking_hard_decision"
    CHANGES_PRIMARY = "changes_primary"
    CHANGES_ALTERNATIVE_OR_POST = "changes_alternative_or_post"
    CHANGES_VERIFICATION_OR_ORDER = "changes_verification_or_order"
    NON_DECISION_CHANGING = "non_decision_changing"


T = TypeVar("T")


class Fact(FrozenModel, Generic[T]):
    """A sourced fact envelope; uncertainty is never flattened into a value."""

    value: T | None = None
    status: FactStatus
    source_refs: tuple[Identifier, ...] = ()
    observed_at: UtcDateTime

    @model_validator(mode="after")
    def validate_epistemic_state(self) -> Fact[T]:
        if self.status in {FactStatus.UNKNOWN, FactStatus.CONTRADICTED}:
            if self.value is not None:
                raise ValueError(f"{self.status.value} facts must not carry a normalized value")
        elif self.value is None:
            raise ValueError(f"{self.status.value} facts require a value")
        if self.status != FactStatus.UNKNOWN and not self.source_refs:
            raise ValueError("known or contradicted facts require explicit source_refs")
        if self.status == FactStatus.CONTRADICTED and len(self.source_refs) < 2:
            raise ValueError("contradicted facts require at least two conflicting source_refs")
        return self


class EvidenceReference(FrozenModel):
    ref_id: Identifier
    source_type: Identifier
    source_locator: str | None = Field(default=None, max_length=2000)
    observed_at: UtcDateTime | None = None


class Money(FrozenModel):
    amount: Decimal = Field(ge=0)
    currency: CurrencyCode


class FeeFact(FrozenModel):
    amount: Decimal | None = Field(default=None, ge=0)
    currency: CurrencyCode | None = None
    status: FactStatus
    source_refs: tuple[Identifier, ...] = ()
    observed_at: UtcDateTime

    @model_validator(mode="after")
    def validate_fee_state(self) -> FeeFact:
        unknown_like = self.status in {FactStatus.UNKNOWN, FactStatus.CONTRADICTED}
        if unknown_like and (self.amount is not None or self.currency is not None):
            raise ValueError("unknown or contradicted fees cannot serialize an amount or currency")
        if not unknown_like and (self.amount is None or self.currency is None):
            raise ValueError("known fees require exact amount and currency")
        if self.status != FactStatus.UNKNOWN and not self.source_refs:
            raise ValueError("known or contradicted fees require source_refs")
        if self.status == FactStatus.CONTRADICTED and len(self.source_refs) < 2:
            raise ValueError("contradicted fees require two conflicting source_refs")
        return self


class BudgetConstraint(FrozenModel):
    constraint_id: Identifier
    limit: Money
    hard: bool = True


class RequiredFee(FrozenModel):
    fee_id: Identifier
    action_id: Identifier
    festival_id: CanonicalFestivalId
    action_scope: FeeActionScope
    required_now: bool
    fee: FeeFact

    @model_validator(mode="after")
    def validate_required_now_scope(self) -> RequiredFee:
        eligible = self.action_scope in {
            FeeActionScope.CURRENT_ROOT,
            FeeActionScope.LOCKED_CONCURRENT,
        }
        if self.required_now != eligible:
            raise ValueError(
                "required_now is true only for the current root or locked/noncontingent concurrent actions"
            )
        return self


class BudgetAssessment(FrozenModel):
    state: HardBudgetState
    hard_limit: Money
    known_total: Money
    unknown_fee_ids: tuple[Identifier, ...] = ()
    required_action_ids: tuple[Identifier, ...] = ()
    included_fee_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_budget_state(self) -> BudgetAssessment:
        if self.known_total.currency != self.hard_limit.currency:
            raise ValueError("known total and hard limit must be directly comparable")
        over = self.known_total.amount > self.hard_limit.amount
        if self.state == HardBudgetState.KNOWN_INFEASIBLE:
            if not over:
                raise ValueError("KNOWN_INFEASIBLE requires known total above the hard limit")
        elif self.state == HardBudgetState.KNOWN_FEASIBLE:
            if over or self.unknown_fee_ids:
                raise ValueError("KNOWN_FEASIBLE requires within-limit total and no unknown fees")
        elif over or not self.unknown_fee_ids:
            raise ValueError("VERIFY requires within-limit known total and at least one unknown fee")
        if len(self.unknown_fee_ids) != len(set(self.unknown_fee_ids)):
            raise ValueError("unknown fee IDs must be unique")
        return self


class CampaignProfile(FrozenModel):
    schema_version: Literal[1] = 1
    title: Fact[str]
    synopsis: Fact[str]
    format: Fact[str]
    country: Fact[str]
    themes: Fact[tuple[str, ...]]
    runtime_minutes: Fact[int]
    premiere_assertions: tuple[Fact[str], ...] = ()
    target_regions: tuple[str, ...] = ()
    profile_hash: HashDigest


class RetrievalInput(FrozenModel):
    schema_version: Literal[1] = 1
    profile_hash: HashDigest
    semantic_query: Annotated[str, Field(min_length=3, max_length=2000)]
    format: str
    country: str
    themes: tuple[str, ...]
    target_regions: tuple[str, ...] = ()
    retrieval_policy_version: Identifier
    embedding_model: Identifier
    corpus_identity_version: Identifier
    as_of_date: date
    retrieval_key: HashDigest


class FestivalIdentitySnapshot(FrozenModel):
    festival_name: NonEmptyText
    country: Fact[str]
    region: Fact[str]
    tier: Fact[str]
    accepts: Fact[tuple[str, ...]]


class RetrievedFestivalEvidence(FrozenModel):
    schema_version: Literal[1] = 1
    festival_id: CanonicalFestivalId
    identity: FestivalIdentitySnapshot
    festival_facts_hash: HashDigest
    retrieval_rank: int = Field(ge=1, le=12)
    semantic_score: Decimal = Field(ge=0, le=1)
    lexical_score: Decimal = Field(ge=0, le=1)
    retrieval_sources: tuple[Identifier, ...]
    retrieval_backend: Identifier
    source_confidence: FactStatus
    premiere_rule: Fact[str]
    deadline_fact: Fact[str]
    fee_fact: FeeFact
    retrieval_key: HashDigest


class DimensionEvidence(FrozenModel):
    dimension: Literal[
        "thematic_fit",
        "genre_fit",
        "lineup_similarity",
        "company_relationship",
        "strategic_value",
        "deadline_urgency",
    ]
    raw_rating: Rating
    guarded_rating: Rating
    points: Decimal = Field(ge=0)
    evidence_refs: tuple[Identifier, ...]


class CompanyRelationshipEvidence(FrozenModel):
    rating: Rating
    screenings: int = Field(ge=0)
    latest_year: int | None = Field(default=None, ge=1888, le=2200)
    award_count: int = Field(ge=0)
    evidence_refs: tuple[Identifier, ...] = ()


class CandidateCreativeEvidence(FrozenModel):
    schema_version: Literal[1] = 1
    festival_id: CanonicalFestivalId
    profile_hash: HashDigest
    festival_facts_hash: HashDigest
    dimensions: tuple[DimensionEvidence, ...]
    guardrail_adjustments: tuple[Identifier, ...] = ()
    company_relationship: CompanyRelationshipEvidence
    prompt_version: Identifier
    model_version: Identifier
    guardrail_version: Identifier
    creative_key: HashDigest

    @model_validator(mode="after")
    def unique_dimensions(self) -> CandidateCreativeEvidence:
        names = [item.dimension for item in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("creative dimensions must be unique")
        return self


class PremiereConstraint(FrozenModel):
    scope: PremiereScope
    territory: str | None = None
    rule_status: FactStatus
    evidence_refs: tuple[Identifier, ...] = ()


class DeadlineAssessment(FrozenModel):
    status: DeadlineStatus
    next_deadline: date | None = None
    confidence: FactStatus
    material_eligible: bool | None = None
    evidence_refs: tuple[Identifier, ...] = ()


class VerificationItem(FrozenModel):
    item_id: Identifier
    fact_key: Identifier
    status: FactStatus
    blocking: bool
    source_refs: tuple[Identifier, ...] = ()


class CandidateRiskEvidence(FrozenModel):
    schema_version: Literal[1] = 1
    festival_id: CanonicalFestivalId
    profile_hash: HashDigest
    festival_facts_hash: HashDigest
    eligible: bool
    hard_eligibility_reason: Identifier | None = None
    premiere_constraint: PremiereConstraint
    premiere_risk: Literal["none", "low", "medium", "high"]
    deadline: DeadlineAssessment
    runtime_eligible: bool | None = None
    uncertainties: tuple[VerificationItem, ...] = ()
    as_of_date: date
    risk_policy_version: Identifier
    risk_input_hash: HashDigest
    risk_key: HashDigest


class ScoreBreakdown(FrozenModel):
    score: Score
    base_score: Decimal = Field(ge=0, le=100)
    premiere_penalty: Decimal = Field(ge=0, le=100)
    dimensions: tuple[DimensionEvidence, ...]


class FrozenCandidateEvidence(FrozenModel):
    schema_version: Literal[1] = 1
    festival_id: CanonicalFestivalId
    retrieved: RetrievedFestivalEvidence
    creative: CandidateCreativeEvidence
    risk: CandidateRiskEvidence
    score_breakdown: ScoreBreakdown
    decision_grade: DecisionGrade
    future_quality: Percentage
    component_hash: HashDigest

    @model_validator(mode="after")
    def validate_component_identity(self) -> FrozenCandidateEvidence:
        ids = {
            self.festival_id,
            self.retrieved.festival_id,
            self.creative.festival_id,
            self.risk.festival_id,
        }
        if len(ids) != 1:
            raise ValueError("retrieved, creative, and risk evidence must share festival_id")
        fact_hashes = {
            self.retrieved.festival_facts_hash,
            self.creative.festival_facts_hash,
            self.risk.festival_facts_hash,
        }
        if len(fact_hashes) != 1:
            raise ValueError("candidate components must share festival_facts_hash")
        profile_hashes = {self.creative.profile_hash, self.risk.profile_hash}
        if len(profile_hashes) != 1:
            raise ValueError("creative and risk evidence must share profile_hash")
        return self


class PremiereScopeState(FrozenModel):
    scope: PremiereScope
    territory: str | None = None
    availability: PremiereAvailability
    contradiction: bool = False
    reason_code: Identifier
    evidence_refs: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def contradictions_are_unknown(self) -> PremiereScopeState:
        if self.contradiction and self.availability != PremiereAvailability.UNKNOWN:
            raise ValueError("contradicted ledger scopes must be unknown")
        return self


class PremiereLedgerSnapshot(FrozenModel):
    schema_version: Literal[1] = 1
    scopes: tuple[PremiereScopeState, ...]
    derivation_policy_version: Identifier
    input_hash: HashDigest
    ledger_hash: HashDigest


class ScreeningSnapshot(FrozenModel):
    screening_id: Identifier
    festival_id: CanonicalFestivalId | None = None
    state: ScreeningState
    access: ScreeningAccess
    country: str | None = None
    region: str | None = None
    scheduled_at: UtcDateTime | None = None
    occurred_at: UtcDateTime | None = None
    source_refs: tuple[Identifier, ...] = ()


ConstraintValue = Union[str, int, bool, tuple[str, ...], PreservationMode]
FactValue = Union[str, int, bool, tuple[str, ...]]


class CampaignConstraint(FrozenModel):
    constraint_id: Identifier
    constraint_type: Identifier
    strength: ConstraintStrength
    value: ConstraintValue
    locked: bool = False
    active: bool = True
    candidate_expanding: bool = False
    source_ref: Identifier


class CampaignOpportunity(FrozenModel):
    opportunity_id: Identifier
    festival_id: CanonicalFestivalId
    submission_state: SubmissionState = SubmissionState.NOT_SUBMITTED
    offer_state: OfferState = OfferState.NONE
    policy_state: OpportunityPolicyState = OpportunityPolicyState.NORMAL
    verification_items: tuple[VerificationItem, ...] = ()


class CampaignSnapshot(FrozenModel):
    schema_version: Literal[1] = 1
    workspace_id: Identifier
    campaign_id: Identifier
    campaign_version: int = Field(ge=0)
    lifecycle: CampaignLifecycle
    readiness: CampaignReadiness
    profile: CampaignProfile
    premiere_ledger: PremiereLedgerSnapshot
    screenings: tuple[ScreeningSnapshot, ...] = ()
    constraints: tuple[CampaignConstraint, ...] = ()
    opportunities: tuple[CampaignOpportunity, ...]
    candidates: tuple[FrozenCandidateEvidence, ...]
    active_strategy_ref: Identifier | None = None
    aggregate_hash: HashDigest


class CompatibilityEdge(FrozenModel):
    schema_version: Literal[1] = 1
    from_festival_id: CanonicalFestivalId
    to_festival_id: CanonicalFestivalId
    status: CompatibilityStatus
    scope: PremiereScope
    territory: str | None = None
    reason_code: Identifier
    evidence_refs: tuple[Identifier, ...]
    source_confidence: FactStatus
    graph_policy_version: Identifier
    edge_hash: HashDigest

    @model_validator(mode="after")
    def reject_self_edge(self) -> CompatibilityEdge:
        if self.from_festival_id == self.to_festival_id:
            raise ValueError("compatibility self-edges are not allowed")
        return self


class ArtifactKeys(FrozenModel):
    identity_hash: HashDigest
    retrieval_key: HashDigest
    creative_key: HashDigest
    risk_key: HashDigest
    graph_key: HashDigest
    plan_key: HashDigest


class PlanningInput(FrozenModel):
    """The exact and only future CampaignPlanner input contract."""

    schema_version: Literal[1] = 1
    campaign_id: Identifier
    campaign_version: int = Field(ge=0)
    profile: CampaignProfile
    premiere_ledger: PremiereLedgerSnapshot
    candidates: tuple[FrozenCandidateEvidence, ...] = Field(min_length=1, max_length=12)
    compatibility_edges: tuple[CompatibilityEdge, ...]
    opportunities: tuple[CampaignOpportunity, ...]
    constraints: tuple[CampaignConstraint, ...] = ()
    preservation_mode: PreservationMode = PreservationMode.BALANCED
    budget_constraint: BudgetConstraint | None = None
    required_fees: tuple[RequiredFee, ...] = ()
    as_of_date: date
    policy_versions: tuple[Identifier, ...]
    artifact_keys: ArtifactKeys
    planning_input_hash: HashDigest

    @model_validator(mode="after")
    def validate_candidate_graph(self) -> PlanningInput:
        ids = [candidate.festival_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("PlanningInput candidate festival_ids must be unique")
        known = set(ids)
        opportunity_ids = [item.festival_id for item in self.opportunities]
        if len(opportunity_ids) != len(set(opportunity_ids)):
            raise ValueError("PlanningInput opportunity festival_ids must be unique")
        if set(opportunity_ids) != known:
            raise ValueError("PlanningInput requires exactly one opportunity per candidate")
        directed = [(edge.from_festival_id, edge.to_festival_id) for edge in self.compatibility_edges]
        if len(directed) != len(set(directed)):
            raise ValueError("duplicate directed compatibility edge")
        expected = {(source, target) for source in known for target in known if source != target}
        if set(directed) != expected:
            raise ValueError("PlanningInput requires the complete directed candidate edge set")
        fee_ids = [fee.fee_id for fee in self.required_fees]
        if len(fee_ids) != len(set(fee_ids)):
            raise ValueError("required fee IDs must be unique")
        if any(fee.festival_id not in known for fee in self.required_fees):
            raise ValueError("required fees must reference PlanningInput candidates")
        if self.required_fees and self.budget_constraint is None:
            raise ValueError("required fees require an explicit budget constraint")
        if self.profile.profile_hash != self.artifact_keys.identity_hash:
            raise ValueError("profile_hash must equal artifact identity_hash")
        return self

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate.festival_id for candidate in self.candidates)


class HardFilterResult(FrozenModel):
    feasible: bool
    reason_codes: tuple[Identifier, ...] = ()
    constraint_refs: tuple[Identifier, ...] = ()


class VerificationBurden(FrozenModel):
    blocking_gate_count: int = Field(ge=0)
    verify_edge_quality_pct: Percentage
    total_gate_count: int = Field(ge=0)


class PreservationDiagnostics(FrozenModel):
    mode: PreservationMode
    known_preserved_pct: Percentage
    possible_additional_pct: Percentage
    known_destroyed_pct: Percentage
    preserved_ids: tuple[CanonicalFestivalId, ...] = ()
    verify_ids: tuple[CanonicalFestivalId, ...] = ()
    destroyed_ids: tuple[CanonicalFestivalId, ...] = ()

    @model_validator(mode="after")
    def validate_partitions(self) -> PreservationDiagnostics:
        groups = [set(self.preserved_ids), set(self.verify_ids), set(self.destroyed_ids)]
        if any(groups[i] & groups[j] for i in range(3) for j in range(i + 1, 3)):
            raise ValueError("preserved, verify, and destroyed IDs must be disjoint")
        return self


class PlannerCandidateDiagnostic(FrozenModel):
    festival_id: CanonicalFestivalId
    hard_filter: HardFilterResult
    on_pareto_frontier: bool
    immediate_utility: Score
    decision_grade: DecisionGrade
    decision_grade_group: DecisionGradeGroup
    preservation: PreservationDiagnostics
    verification_burden: VerificationBurden
    budget_assessment: BudgetAssessment
    immediate_rank: int | None = Field(default=None, ge=1)
    preservation_rank: int | None = Field(default=None, ge=1)
    soft_budget_preference_rank: int = Field(default=0, ge=0)
    deterministic_tie_break_id: CanonicalFestivalId

    @model_validator(mode="after")
    def tie_break_matches(self) -> PlannerCandidateDiagnostic:
        if self.deterministic_tie_break_id != self.festival_id:
            raise ValueError("deterministic tie-break ID must be the canonical festival_id")
        return self


class ModeSelection(FrozenModel):
    mode: PreservationMode
    selected_festival_id: CanonicalFestivalId
    ordered_frontier_ids: tuple[CanonicalFestivalId, ...]


class ExpectedPolicyResult(FrozenModel):
    case_id: Identifier
    naive_selected_id: CanonicalFestivalId
    expected_frontier_ids: tuple[CanonicalFestivalId, ...]
    diagnostics: tuple[PlannerCandidateDiagnostic, ...]
    mode_selections: tuple[ModeSelection, ...]
    result_hash: HashDigest

    @model_validator(mode="after")
    def validate_policy_ids(self) -> ExpectedPolicyResult:
        diagnostic_ids = {item.festival_id for item in self.diagnostics}
        if not set(self.expected_frontier_ids) <= diagnostic_ids:
            raise ValueError("frontier IDs require diagnostics")
        if self.naive_selected_id not in diagnostic_ids:
            raise ValueError("naive selection requires diagnostics")
        modes = [item.mode for item in self.mode_selections]
        if set(modes) != set(PreservationMode):
            raise ValueError("expected result must freeze all preservation modes")
        for selection in self.mode_selections:
            if selection.selected_festival_id not in set(self.expected_frontier_ids):
                raise ValueError("mode selection must be on the expected frontier")
        return self


class PrimaryLaunch(FrozenModel):
    festival_id: CanonicalFestivalId
    submission_action: NonEmptyText
    screening_gate: Identifier
    reason_refs: tuple[Identifier, ...]


class AlternativeLaunch(FrozenModel):
    festival_id: CanonicalFestivalId
    activates_on: Literal["primary_rejected_or_withdrawn"]


class RejectionBranch(FrozenModel):
    of_festival_id: CanonicalFestivalId
    promote_festival_id: CanonicalFestivalId


class PremiereEffect(FrozenModel):
    world: PremiereAvailability
    international: PremiereAvailability


class ScreenedBranch(FrozenModel):
    at_festival_id: CanonicalFestivalId
    premiere_effect: PremiereEffect
    post_premiere_opportunity_ids: tuple[CanonicalFestivalId, ...]


class VerificationGate(FrozenModel):
    gate_id: Identifier = Field(alias="id")
    fact_key: Identifier
    affected_decision: Identifier
    blocking: bool = False
    source_refs: tuple[Identifier, ...] = ()

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class Clarification(FrozenModel):
    clarification_id: Identifier
    fact_key: Identifier
    question: NonEmptyText | None = None
    impact: ClarificationImpact
    affected_decisions: tuple[Identifier, ...] = ()
    blocking: bool
    contradiction: bool = False
    tested_states: tuple[str, ...] = ()
    candidate_set_lower_bound: bool = False
    suppressed: bool = False
    suppression_reason: Identifier | None = None

    @model_validator(mode="after")
    def validate_suppression(self) -> Clarification:
        if self.suppressed and not self.suppression_reason:
            raise ValueError("suppressed clarifications require a reason")
        if not self.suppressed and self.question is None:
            raise ValueError("visible clarifications require a question")
        return self


class NextAction(FrozenModel):
    action_id: Identifier
    festival_id: CanonicalFestivalId | None = None
    description: NonEmptyText
    required_now: bool
    gated_by: Identifier | None = None


class CampaignPlan(FrozenModel):
    schema_version: Literal[2] = 2
    primary_launch: PrimaryLaunch
    alternative_launches: tuple[AlternativeLaunch, ...] = Field(default=(), max_length=2)
    rejection_branch: RejectionBranch | None = None
    screened_branch: ScreenedBranch | None = None
    verification_gates: tuple[VerificationGate, ...] = ()
    budget: BudgetAssessment
    post_premiere_opportunities: tuple[CanonicalFestivalId, ...] = ()
    option_preservation: PreservationDiagnostics
    clarifications: tuple[Clarification, ...] = ()
    next_actions: tuple[NextAction, ...] = ()
    selection_diagnostics: tuple[PlannerCandidateDiagnostic, ...] = ()
    plan_hash: HashDigest

    @model_validator(mode="after")
    def validate_routes(self, info: ValidationInfo) -> CampaignPlan:
        alternatives = [item.festival_id for item in self.alternative_launches]
        route_ids = [self.primary_launch.festival_id, *alternatives]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("primary and alternative route IDs must be unique")
        if len(alternatives) != len(set(alternatives)):
            raise ValueError("alternative route IDs must be unique")
        if self.rejection_branch:
            if self.rejection_branch.of_festival_id != self.primary_launch.festival_id:
                raise ValueError("rejection branch must be grounded in the primary launch")
            if self.rejection_branch.promote_festival_id not in alternatives:
                raise ValueError("rejection branch promotion must be a grounded alternative")
        if len(self.post_premiere_opportunities) != len(set(self.post_premiere_opportunities)):
            raise ValueError("post-premiere opportunity IDs must be unique")
        if self.budget.state == HardBudgetState.KNOWN_INFEASIBLE:
            raise ValueError("a selected CampaignPlan cannot be KNOWN_INFEASIBLE")
        if self.budget.state == HardBudgetState.VERIFY and not any(
            gate.blocking and gate.affected_decision.startswith("budget")
            for gate in self.verification_gates
        ):
            raise ValueError("a hard-budget VERIFY plan requires a blocking budget gate")
        # Catalog membership is enforced by CanonicalFestivalId validation context.
        preservation_ids = (
            set(self.option_preservation.preserved_ids)
            | set(self.option_preservation.verify_ids)
            | set(self.option_preservation.destroyed_ids)
        )
        context_ids = set((info.context or {}).get("known_festival_ids", ()))
        if context_ids and not preservation_ids <= context_ids:
            raise ValueError("preservation diagnostics must reference known candidate IDs")
        return self


class ReuseManifest(FrozenModel):
    schema_version: Literal[1] = 1
    invalidation_class: InvalidationClass
    prior_artifact_keys: ArtifactKeys
    current_artifact_keys: ArtifactKeys
    reused_artifacts: tuple[ArtifactName, ...]
    rerun_artifacts: tuple[ArtifactName, ...]
    invalidated_artifacts: tuple[ArtifactName, ...]
    reasons: tuple[Identifier, ...]
    chat_attempts: int = Field(ge=0)
    embedding_attempts: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_artifact_sets(self) -> ReuseManifest:
        reused, rerun, invalidated = map(
            set, (self.reused_artifacts, self.rerun_artifacts, self.invalidated_artifacts)
        )
        if reused & (rerun | invalidated):
            raise ValueError("an artifact cannot be both reused and rerun/invalidated")
        if len(self.reused_artifacts) != len(reused) or len(self.rerun_artifacts) != len(rerun):
            raise ValueError("artifact lists must not contain duplicates")
        return self


class StrategyDiff(FrozenModel):
    schema_version: Literal[1] = 1
    campaign_id: Identifier
    base_campaign_version: int = Field(ge=0)
    new_campaign_version: int = Field(ge=0)
    base_strategy_ref: Identifier
    new_strategy_ref: Identifier
    primary_before: CanonicalFestivalId | None = None
    primary_after: CanonicalFestivalId | None = None
    added_route_ids: tuple[CanonicalFestivalId, ...] = ()
    removed_route_ids: tuple[CanonicalFestivalId, ...] = ()
    unchanged_route_ids: tuple[CanonicalFestivalId, ...] = ()
    gate_change_ids: tuple[Identifier, ...] = ()
    budget_state_before: HardBudgetState
    budget_state_after: HardBudgetState
    preservation_before: PreservationDiagnostics
    preservation_after: PreservationDiagnostics
    causal_refs: tuple[Identifier, ...]
    reuse_summary: ReuseManifest
    diff_hash: HashDigest


class ProfileFactKey(StrEnum):
    TITLE = "title"
    SYNOPSIS = "synopsis"
    FORMAT = "format"
    COUNTRY = "country"
    THEMES = "themes"
    RUNTIME_MINUTES = "runtime_minutes"
    PREMIERE_ASSERTION = "premiere_assertion"
    TARGET_REGION = "target_region"


class CorrectionDomain(StrEnum):
    IDENTITY = "identity"
    DOMAIN_EVIDENCE = "domain_evidence"
    OPERATIONAL_POLICY = "operational_policy"


class ActorKind(StrEnum):
    HUMAN = "human"


class CommandActor(FrozenModel):
    kind: Literal[ActorKind.HUMAN] = ActorKind.HUMAN
    actor_ref: Identifier


class UpdateProfileFactPayload(FrozenModel):
    fact_key: ProfileFactKey
    fact: Fact[FactValue]

    @model_validator(mode="after")
    def validate_fact_value_type(self) -> UpdateProfileFactPayload:
        value = self.fact.value
        if value is None:
            return self
        if self.fact_key == ProfileFactKey.RUNTIME_MINUTES:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("runtime_minutes requires an integer fact value")
        elif self.fact_key == ProfileFactKey.THEMES:
            if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
                raise ValueError("themes requires a tuple of strings")
        elif not isinstance(value, str):
            raise ValueError(f"{self.fact_key.value} requires a string fact value")
        return self


class SetConstraintPayload(FrozenModel):
    constraint: CampaignConstraint


class RemoveConstraintPayload(FrozenModel):
    constraint_id: Identifier
    explicit_unlock: bool = False


class OpportunityPayload(FrozenModel):
    festival_id: CanonicalFestivalId


class OpportunityDecisionPayload(FrozenModel):
    festival_id: CanonicalFestivalId
    source_refs: tuple[Identifier, ...]


class InvitationPayload(OpportunityDecisionPayload):
    offer_ref: Identifier


class ScreeningSchedulePayload(FrozenModel):
    screening_id: Identifier
    festival_id: CanonicalFestivalId | None = None
    venue: str | None = Field(default=None, max_length=500)
    country: str | None = None
    region: str | None = None
    scheduled_at: UtcDateTime | None = None
    access: ScreeningAccess = ScreeningAccess.UNKNOWN
    source_refs: tuple[Identifier, ...] = ()


class ScreeningConfirmPayload(FrozenModel):
    screening_id: Identifier
    occurred_at: UtcDateTime
    access: ScreeningAccess
    country: str | None = None
    region: str | None = None
    source_refs: tuple[Identifier, ...] = Field(min_length=1)


class ScreeningIdPayload(FrozenModel):
    screening_id: Identifier
    correction: bool = False


class VerifyOpportunityFactPayload(FrozenModel):
    festival_id: CanonicalFestivalId
    verification_item_id: Identifier
    result: Fact[FactValue]


class CorrectRecordPayload(FrozenModel):
    prior_ref: Identifier
    corrected_domain: CorrectionDomain
    replacement: Fact[FactValue]


class CloseCampaignPayload(FrozenModel):
    reason_ref: Identifier


class CampaignCommandType(StrEnum):
    UPDATE_PROFILE_FACT = "update_profile_fact"
    SET_CONSTRAINT = "set_constraint"
    REMOVE_CONSTRAINT = "remove_constraint"
    LOCK_OPPORTUNITY = "lock_opportunity"
    UNLOCK_OPPORTUNITY = "unlock_opportunity"
    EXCLUDE_OPPORTUNITY = "exclude_opportunity"
    INCLUDE_OPPORTUNITY = "include_opportunity"
    MARK_SUBMITTED = "mark_submitted"
    RECORD_REJECTION = "record_rejection"
    RECORD_INVITATION = "record_invitation"
    ACCEPT_OFFER = "accept_offer"
    DECLINE_OFFER = "decline_offer"
    WITHDRAW = "withdraw"
    SCHEDULE_SCREENING = "schedule_screening"
    CONFIRM_SCREENING = "confirm_screening"
    CANCEL_SCREENING = "cancel_screening"
    VERIFY_OPPORTUNITY_FACT = "verify_opportunity_fact"
    CORRECT_RECORD = "correct_record"
    CLOSE_CAMPAIGN = "close_campaign"


class CommandBase(FrozenModel):
    expected_version: int = Field(ge=0)
    idempotency_key: Annotated[str, Field(min_length=8, max_length=200)]
    actor: CommandActor


class UpdateProfileFactCommand(CommandBase):
    type: Literal[CampaignCommandType.UPDATE_PROFILE_FACT]
    payload: UpdateProfileFactPayload
    invalidation_class: Literal[InvalidationClass.A] = InvalidationClass.A


class SetConstraintCommand(CommandBase):
    type: Literal[CampaignCommandType.SET_CONSTRAINT]
    payload: SetConstraintPayload
    invalidation_class: InvalidationClass

    @model_validator(mode="after")
    def validate_invalidation(self) -> SetConstraintCommand:
        expected = (
            InvalidationClass.A
            if self.payload.constraint.candidate_expanding
            else InvalidationClass.C
        )
        if self.invalidation_class != expected:
            raise ValueError("constraint invalidation must reflect candidate expansion metadata")
        return self


class RemoveConstraintCommand(CommandBase):
    type: Literal[CampaignCommandType.REMOVE_CONSTRAINT]
    payload: RemoveConstraintPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class LockOpportunityCommand(CommandBase):
    type: Literal[CampaignCommandType.LOCK_OPPORTUNITY]
    payload: OpportunityPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class UnlockOpportunityCommand(CommandBase):
    type: Literal[CampaignCommandType.UNLOCK_OPPORTUNITY]
    payload: OpportunityPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class ExcludeOpportunityCommand(CommandBase):
    type: Literal[CampaignCommandType.EXCLUDE_OPPORTUNITY]
    payload: OpportunityPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class IncludeOpportunityCommand(CommandBase):
    type: Literal[CampaignCommandType.INCLUDE_OPPORTUNITY]
    payload: OpportunityPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class MarkSubmittedCommand(CommandBase):
    type: Literal[CampaignCommandType.MARK_SUBMITTED]
    payload: OpportunityDecisionPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class RecordRejectionCommand(CommandBase):
    type: Literal[CampaignCommandType.RECORD_REJECTION]
    payload: OpportunityDecisionPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class RecordInvitationCommand(CommandBase):
    type: Literal[CampaignCommandType.RECORD_INVITATION]
    payload: InvitationPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class AcceptOfferCommand(CommandBase):
    type: Literal[CampaignCommandType.ACCEPT_OFFER]
    payload: OpportunityDecisionPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class DeclineOfferCommand(CommandBase):
    type: Literal[CampaignCommandType.DECLINE_OFFER]
    payload: OpportunityDecisionPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class WithdrawCommand(CommandBase):
    type: Literal[CampaignCommandType.WITHDRAW]
    payload: OpportunityDecisionPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class ScheduleScreeningCommand(CommandBase):
    type: Literal[CampaignCommandType.SCHEDULE_SCREENING]
    payload: ScreeningSchedulePayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class ConfirmScreeningCommand(CommandBase):
    type: Literal[CampaignCommandType.CONFIRM_SCREENING]
    payload: ScreeningConfirmPayload
    invalidation_class: Literal[InvalidationClass.B] = InvalidationClass.B


class CancelScreeningCommand(CommandBase):
    type: Literal[CampaignCommandType.CANCEL_SCREENING]
    payload: ScreeningIdPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


class VerifyOpportunityFactCommand(CommandBase):
    type: Literal[CampaignCommandType.VERIFY_OPPORTUNITY_FACT]
    payload: VerifyOpportunityFactPayload
    invalidation_class: Literal[InvalidationClass.B] = InvalidationClass.B


class CorrectRecordCommand(CommandBase):
    type: Literal[CampaignCommandType.CORRECT_RECORD]
    payload: CorrectRecordPayload
    invalidation_class: InvalidationClass

    @model_validator(mode="after")
    def validate_correction_invalidation(self) -> CorrectRecordCommand:
        expected = {
            CorrectionDomain.IDENTITY: InvalidationClass.A,
            CorrectionDomain.DOMAIN_EVIDENCE: InvalidationClass.B,
            CorrectionDomain.OPERATIONAL_POLICY: InvalidationClass.C,
        }[self.payload.corrected_domain]
        if self.invalidation_class != expected:
            raise ValueError("correction invalidation must match corrected_domain")
        return self


class CloseCampaignCommand(CommandBase):
    type: Literal[CampaignCommandType.CLOSE_CAMPAIGN]
    payload: CloseCampaignPayload
    invalidation_class: Literal[InvalidationClass.C] = InvalidationClass.C


CampaignCommand = Annotated[
    Union[
        UpdateProfileFactCommand,
        SetConstraintCommand,
        RemoveConstraintCommand,
        LockOpportunityCommand,
        UnlockOpportunityCommand,
        ExcludeOpportunityCommand,
        IncludeOpportunityCommand,
        MarkSubmittedCommand,
        RecordRejectionCommand,
        RecordInvitationCommand,
        AcceptOfferCommand,
        DeclineOfferCommand,
        WithdrawCommand,
        ScheduleScreeningCommand,
        ConfirmScreeningCommand,
        CancelScreeningCommand,
        VerifyOpportunityFactCommand,
        CorrectRecordCommand,
        CloseCampaignCommand,
    ],
    Field(discriminator="type"),
]


class CampaignEventType(StrEnum):
    PROFILE_FACT_UPDATED = "profile_fact_updated"
    CONSTRAINT_SET = "constraint_set"
    CONSTRAINT_REMOVED = "constraint_removed"
    OPPORTUNITY_LOCKED = "opportunity_locked"
    OPPORTUNITY_UNLOCKED = "opportunity_unlocked"
    OPPORTUNITY_EXCLUDED = "opportunity_excluded"
    OPPORTUNITY_INCLUDED = "opportunity_included"
    SUBMISSION_MARKED = "submission_marked"
    REJECTION_RECORDED = "rejection_recorded"
    INVITATION_RECORDED = "invitation_recorded"
    OFFER_ACCEPTED = "offer_accepted"
    OFFER_DECLINED = "offer_declined"
    OPPORTUNITY_WITHDRAWN = "opportunity_withdrawn"
    SCREENING_SCHEDULED = "screening_scheduled"
    SCREENING_CONFIRMED = "screening_confirmed"
    SCREENING_CANCELLED = "screening_cancelled"
    OPPORTUNITY_FACT_VERIFIED = "opportunity_fact_verified"
    RECORD_CORRECTED = "record_corrected"
    CAMPAIGN_CLOSED = "campaign_closed"


class CampaignEvent(FrozenModel):
    event_id: Identifier
    campaign_id: Identifier
    sequence_no: int = Field(ge=1)
    type: CampaignEventType
    command: CampaignCommand
    before_aggregate_hash: HashDigest
    after_aggregate_hash: HashDigest
    occurred_at: UtcDateTime

    @model_validator(mode="after")
    def validate_event_type(self) -> CampaignEvent:
        # Local import avoids a model/contracts import cycle at module import time.
        from app.campaign.contracts import COMMAND_EVENT_TYPES

        expected = COMMAND_EVENT_TYPES[self.command.type]
        if self.type != expected:
            raise ValueError("event type must match its typed command")
        return self


class ApiError(FrozenModel):
    code: Identifier
    message: NonEmptyText
    current_version: int | None = Field(default=None, ge=0)
    details: tuple[str, ...] = ()


class BootstrapResponse(FrozenModel):
    workspace_id: Identifier
    capability_in_body: Literal[False] = False


class CampaignCreationRequest(FrozenModel):
    structured_profile: CampaignProfile | None = None
    free_text: str | None = Field(default=None, min_length=1, max_length=12000)

    @model_validator(mode="after")
    def exactly_one_input(self) -> CampaignCreationRequest:
        if (self.structured_profile is None) == (self.free_text is None):
            raise ValueError("provide exactly one of structured_profile or free_text")
        return self


class CampaignCreationResponse(FrozenModel):
    campaign_id: Identifier
    campaign_version: Literal[0] = 0
    strategy_status: Literal["ready", "failed", "stale"]


class CampaignAggregateResponse(FrozenModel):
    snapshot: CampaignSnapshot
    active_plan: CampaignPlan | None = None
    latest_diff: StrategyDiff | None = None


class CommandResponse(FrozenModel):
    campaign_id: Identifier
    campaign_version: int = Field(ge=1)
    strategy_status: Literal["ready", "failed", "stale"]
    idempotent_replay: bool = False


class ReplanRequest(FrozenModel):
    expected_version: int = Field(ge=0)


class ReplanResponse(FrozenModel):
    campaign_id: Identifier
    campaign_version: int = Field(ge=0)
    strategy_status: Literal["ready", "failed", "stale"]
    state_event_created: Literal[False] = False


class SimulateRequest(FrozenModel):
    commands: tuple[CampaignCommand, ...] = Field(min_length=1, max_length=3)


class SimulateResponse(FrozenModel):
    base_campaign_version: int = Field(ge=0)
    hypothetical_plan: CampaignPlan | None = None
    mutated_campaign: Literal[False] = False
    requires_provider_refresh: bool = False


class StrategyHistoryResponse(FrozenModel):
    campaign_id: Identifier
    strategy_no: int = Field(ge=1)
    based_on_campaign_version: int = Field(ge=0)
    input_hash: HashDigest
    plan: CampaignPlan | None = None
    diff: StrategyDiff | None = None
    reuse_manifest: ReuseManifest | None = None


class CapabilityContract(FrozenModel):
    entropy_bits: Literal[256] = 256
    encoding: Literal["base64url"] = "base64url"
    cookie_only_after_bootstrap: Literal[True] = True
    secure: Literal[True] = True
    http_only: Literal[True] = True
    same_site: Literal["Lax"] = "Lax"
    persisted_digest: Literal["SHA-256(raw_capability)"] = "SHA-256(raw_capability)"
    extra_hmac_secret: Literal[False] = False
    mutations_json_only: Literal[True] = True
    exact_origin_allowlist: tuple[NonEmptyText, ...]
    forbidden_trace_values: tuple[Literal["capability", "cookie", "digest"], ...] = (
        "capability",
        "cookie",
        "digest",
    )
    campaign_id_authorizes_access: Literal[False] = False

    @model_validator(mode="after")
    def validate_exact_origins(self) -> CapabilityContract:
        if len(self.exact_origin_allowlist) != len(set(self.exact_origin_allowlist)):
            raise ValueError("Origin allowlist entries must be unique")
        for origin in self.exact_origin_allowlist:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
                or origin == "null"
            ):
                raise ValueError("Origin allowlist entries must be exact HTTP(S) origins")
        return self


class BoundaryContractDescriptor(FrozenModel):
    model_name: Identifier
    producer: NonEmptyText
    consumers: tuple[NonEmptyText, ...]
    hash_field: Identifier
    immutable_in_strategy: Literal[True] = True
    provenance_rule: NonEmptyText
    llm_rule: NonEmptyText


class PersistenceTableContract(FrozenModel):
    table_name: Identifier
    essential_fields: tuple[Identifier, ...]
    constraint_summary: NonEmptyText
