"""Pure deterministic Campaign Workspace planner.

Only :class:`~app.campaign.models.PlanningInput` crosses the planner boundary.
No legacy dictionaries, provider calls, persistence, or probabilistic weights
are used here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from app.campaign.contracts import (
    DECISION_GRADE_GROUPS,
    campaign_plan_hash,
    planning_input_hash,
)
from app.campaign.models import (
    AlternativeLaunch,
    BudgetAssessment,
    CampaignConstraint,
    CampaignOpportunity,
    CampaignPlan,
    CompatibilityEdge,
    CompatibilityStatus,
    ConstraintStrength,
    DecisionGradeGroup,
    DeadlineStatus,
    FactStatus,
    FeeActionScope,
    FrozenCandidateEvidence,
    HardBudgetState,
    HardFilterResult,
    Money,
    NextAction,
    OpportunityPolicyState,
    PlannerCandidateDiagnostic,
    PlanningInput,
    PremiereAvailability,
    PremiereEffect,
    PremiereScope,
    PreservationDiagnostics,
    PreservationMode,
    PrimaryLaunch,
    RejectionBranch,
    ScreenedBranch,
    SubmissionState,
    VerificationBurden,
    VerificationGate,
    VerificationItem,
)


FUTURE_QUALITY_DIMENSIONS: Final[frozenset[str]] = frozenset(
    {
        "thematic_fit",
        "genre_fit",
        "lineup_similarity",
        "company_relationship",
        "strategic_value",
    }
)
SUPPORTED_HARD_CONSTRAINTS: Final[frozenset[str]] = frozenset(
    {
        "preserve_world_premiere",
        "preserve_international_premiere",
        "preserve_continental_premiere",
        "preserve_territorial_premiere",
        "require_festival",
        "exclude_festival",
        "minimum_score",
        "preservation_mode",
    }
)


class PlannerError(ValueError):
    """Base error for a deterministic but unrepresentable planning result."""


class PlannerInputError(PlannerError):
    """Raised when a frozen input is internally inconsistent for planning."""


class NoFeasibleLaunchError(PlannerError):
    """Raised because the frozen CampaignPlan requires a selected primary."""


@dataclass(frozen=True, slots=True)
class _RootEvaluation:
    candidate: FrozenCandidateEvidence
    opportunity: CampaignOpportunity
    hard_filter: HardFilterResult
    budget: BudgetAssessment
    preservation: PreservationDiagnostics
    burden: VerificationBurden
    soft_budget_rank: int
    on_frontier: bool = False
    immediate_rank: int | None = None
    preservation_rank: int | None = None

    @property
    def festival_id(self) -> str:
        return self.candidate.festival_id

    @property
    def immediate_utility(self) -> int:
        return self.candidate.score_breakdown.score

    @property
    def grade_group(self) -> DecisionGradeGroup:
        return DECISION_GRADE_GROUPS[self.candidate.decision_grade]

    @property
    def burden_tuple(self) -> tuple[int, Decimal, int]:
        return (
            self.burden.blocking_gate_count,
            self.burden.verify_edge_quality_pct,
            self.burden.total_gate_count,
        )


def future_quality(candidate: FrozenCandidateEvidence) -> Decimal:
    """Calculate enduring downstream quality exactly from the five v2 points.

    Deadline urgency and premiere penalty are intentionally absent.
    """

    points = {
        dimension.dimension: dimension.points
        for dimension in candidate.score_breakdown.dimensions
        if dimension.dimension in FUTURE_QUALITY_DIMENSIONS
    }
    missing = FUTURE_QUALITY_DIMENSIONS - set(points)
    if missing:
        raise PlannerInputError(
            f"candidate {candidate.festival_id} lacks future-quality dimensions: "
            + ", ".join(sorted(missing))
        )
    return Decimal(100) * sum(points.values(), Decimal(0)) / Decimal(90)


def _budget_fees_for_root(planning_input: PlanningInput, festival_id: str):
    return tuple(
        sorted(
            (
                required
                for required in planning_input.required_fees
                if required.required_now
                and (
                    (
                        required.action_scope == FeeActionScope.CURRENT_ROOT
                        and required.festival_id == festival_id
                    )
                    or required.action_scope == FeeActionScope.LOCKED_CONCURRENT
                )
            ),
            key=lambda required: required.fee_id,
        )
    )


def assess_budget(planning_input: PlanningInput, festival_id: str) -> BudgetAssessment:
    """Assess only required-now actions for one mutually exclusive root."""

    constraint = planning_input.budget_constraint
    if constraint is None:
        raise PlannerInputError(
            "PlanningInput permits no budget_constraint but frozen CampaignPlan.budget "
            "requires a hard_limit; planning cannot invent one"
        )
    currency = constraint.limit.currency
    known_total = Decimal(0)
    unknown_ids: list[str] = []
    included_ids: list[str] = []
    action_ids: list[str] = []
    for required in _budget_fees_for_root(planning_input, festival_id):
        included_ids.append(required.fee_id)
        if required.action_id not in action_ids:
            action_ids.append(required.action_id)
        fee = required.fee
        known_status = fee.status in {FactStatus.CONFIRMED, FactStatus.ASSERTED}
        comparable = fee.currency == currency
        if known_status and comparable and fee.amount is not None:
            known_total += fee.amount
        else:
            unknown_ids.append(required.fee_id)

    if known_total > constraint.limit.amount:
        state = HardBudgetState.KNOWN_INFEASIBLE
    elif unknown_ids:
        state = HardBudgetState.VERIFY
    else:
        state = HardBudgetState.KNOWN_FEASIBLE
    return BudgetAssessment(
        state=state,
        hard_limit=constraint.limit,
        known_total=Money(amount=known_total, currency=currency),
        unknown_fee_ids=tuple(unknown_ids),
        required_action_ids=tuple(action_ids),
        included_fee_ids=tuple(included_ids),
    )


def _soft_budget_rank(planning_input: PlanningInput, budget: BudgetAssessment) -> int:
    constraint = planning_input.budget_constraint
    if constraint is None or constraint.hard:
        return 0
    if budget.state == HardBudgetState.KNOWN_FEASIBLE:
        return 0
    if budget.state == HardBudgetState.VERIFY:
        return 1
    return 2


def _terminal_or_unavailable(
    candidate: FrozenCandidateEvidence,
    opportunity: CampaignOpportunity,
) -> bool:
    if opportunity.submission_state in {SubmissionState.REJECTED, SubmissionState.WITHDRAWN}:
        return True
    if opportunity.policy_state == OpportunityPolicyState.EXCLUDED:
        return True
    if candidate.risk.eligible:
        return False
    reason = (candidate.risk.hard_eligibility_reason or "").casefold()
    return "premiere" not in reason


def _preservation_universe(
    root_id: str,
    candidates: dict[str, FrozenCandidateEvidence],
    opportunities: dict[str, CampaignOpportunity],
) -> tuple[str, ...]:
    return tuple(
        festival_id
        for festival_id in sorted(candidates)
        if festival_id != root_id
        and not _terminal_or_unavailable(candidates[festival_id], opportunities[festival_id])
    )


def _preservation(
    planning_input: PlanningInput,
    root_id: str,
    candidates: dict[str, FrozenCandidateEvidence],
    opportunities: dict[str, CampaignOpportunity],
    edges: dict[tuple[str, str], CompatibilityEdge],
) -> PreservationDiagnostics:
    universe = _preservation_universe(root_id, candidates, opportunities)
    preserved: list[str] = []
    verify: list[str] = []
    destroyed: list[str] = []
    weights: dict[str, Decimal] = {}
    for target_id in universe:
        weights[target_id] = future_quality(candidates[target_id])
        status = edges[(root_id, target_id)].status
        if status == CompatibilityStatus.COMPATIBLE:
            preserved.append(target_id)
        elif status == CompatibilityStatus.VERIFY:
            verify.append(target_id)
        else:
            destroyed.append(target_id)

    total = sum(weights.values(), Decimal(0))
    if not universe or total == 0:
        known_preserved = Decimal(100)
        possible = Decimal(0)
        known_destroyed = Decimal(0)
    else:
        known_preserved = Decimal(100) * sum(
            (weights[item] for item in preserved), Decimal(0)
        ) / total
        possible = Decimal(100) * sum(
            (weights[item] for item in verify), Decimal(0)
        ) / total
        known_destroyed = Decimal(100) * sum(
            (weights[item] for item in destroyed), Decimal(0)
        ) / total
    return PreservationDiagnostics(
        mode=planning_input.preservation_mode,
        known_preserved_pct=known_preserved,
        possible_additional_pct=possible,
        known_destroyed_pct=known_destroyed,
        preserved_ids=tuple(preserved),
        verify_ids=tuple(verify),
        destroyed_ids=tuple(destroyed),
    )


def _verification_items(
    candidate: FrozenCandidateEvidence,
    opportunity: CampaignOpportunity,
) -> tuple[VerificationItem, ...]:
    by_id: dict[str, VerificationItem] = {}
    for item in (*candidate.risk.uncertainties, *opportunity.verification_items):
        by_id[item.item_id] = item
    return tuple(by_id[item_id] for item_id in sorted(by_id))


def _verification_burden(
    planning_input: PlanningInput,
    candidate: FrozenCandidateEvidence,
    opportunity: CampaignOpportunity,
    preservation: PreservationDiagnostics,
    budget: BudgetAssessment,
) -> VerificationBurden:
    items = _verification_items(candidate, opportunity)
    verify_edges = len(preservation.verify_ids)
    hard_budget_gate = int(
        bool(planning_input.budget_constraint and planning_input.budget_constraint.hard)
        and budget.state == HardBudgetState.VERIFY
    )
    return VerificationBurden(
        blocking_gate_count=(
            verify_edges + sum(1 for item in items if item.blocking) + hard_budget_gate
        ),
        verify_edge_quality_pct=preservation.possible_additional_pct,
        total_gate_count=len(items) + verify_edges + int(budget.state == HardBudgetState.VERIFY),
    )


def _constraint_values(constraint: CampaignConstraint) -> tuple[str, ...]:
    value = constraint.value
    if isinstance(value, tuple):
        return tuple(str(item) for item in value)
    return (str(value),)


def _constraint_is_binding(constraint: CampaignConstraint) -> bool:
    return constraint.active and (
        constraint.strength == ConstraintStrength.HARD or constraint.locked
    )


def _hard_filter(
    planning_input: PlanningInput,
    evaluation_candidate: FrozenCandidateEvidence,
    opportunity: CampaignOpportunity,
    preservation: PreservationDiagnostics,
    edges: dict[tuple[str, str], CompatibilityEdge],
    locked_opportunity_id: str | None,
) -> HardFilterResult:
    reason_codes: list[str] = []
    constraint_refs: list[str] = []
    festival_id = evaluation_candidate.festival_id
    risk = evaluation_candidate.risk

    if not risk.eligible or risk.runtime_eligible is False:
        reason_codes.append(risk.hard_eligibility_reason or "hard_ineligible")
    if opportunity.submission_state == SubmissionState.REJECTED:
        reason_codes.append("opportunity_rejected")
    if opportunity.submission_state == SubmissionState.WITHDRAWN:
        reason_codes.append("opportunity_withdrawn")
    if opportunity.policy_state == OpportunityPolicyState.EXCLUDED:
        reason_codes.append("opportunity_excluded")
    if (
        DECISION_GRADE_GROUPS[evaluation_candidate.decision_grade]
        == DecisionGradeGroup.NOT_A_LAUNCH_ROOT
    ):
        reason_codes.append("decision_grade_not_launch_root")
    if risk.deadline.status == DeadlineStatus.CLOSED:
        reason_codes.append("deadline_closed_current_cycle")
    if locked_opportunity_id and festival_id != locked_opportunity_id:
        reason_codes.append("locked_opportunity_requires_other_root")
        constraint_refs.append(f"opportunity-lock:{locked_opportunity_id}")

    scope_constraints = {
        "preserve_world_premiere": PremiereScope.WORLD,
        "preserve_international_premiere": PremiereScope.INTERNATIONAL,
        "preserve_continental_premiere": PremiereScope.CONTINENTAL,
        "preserve_territorial_premiere": PremiereScope.TERRITORIAL,
    }
    for constraint in sorted(planning_input.constraints, key=lambda item: item.constraint_id):
        if not _constraint_is_binding(constraint):
            continue
        if constraint.constraint_type not in SUPPORTED_HARD_CONSTRAINTS:
            raise PlannerInputError(
                f"unsupported active hard/locked constraint: {constraint.constraint_type}"
            )
        reason: str | None = None
        if constraint.constraint_type in scope_constraints and bool(constraint.value):
            scope = scope_constraints[constraint.constraint_type]
            if any(
                edges[(festival_id, target_id)].status == CompatibilityStatus.INCOMPATIBLE
                and edges[(festival_id, target_id)].scope == scope
                for target_id in preservation.destroyed_ids
            ):
                reason = f"violates_{constraint.constraint_type}"
        elif constraint.constraint_type == "require_festival":
            if festival_id not in _constraint_values(constraint):
                reason = "violates_required_festival"
        elif constraint.constraint_type == "exclude_festival":
            if festival_id in _constraint_values(constraint):
                reason = "violates_excluded_festival"
        elif constraint.constraint_type == "minimum_score":
            try:
                minimum = int(constraint.value)
            except (TypeError, ValueError) as exc:
                raise PlannerInputError("minimum_score constraint must be an integer") from exc
            if evaluation_candidate.score_breakdown.score < minimum:
                reason = "below_minimum_score"
        elif constraint.constraint_type == "preservation_mode":
            if str(constraint.value) != planning_input.preservation_mode.value:
                reason = "locked_preservation_mode_conflict"
        if reason:
            reason_codes.append(reason)
            constraint_refs.append(constraint.constraint_id)

    constraint = planning_input.budget_constraint
    budget = assess_budget(planning_input, festival_id)
    if constraint and constraint.hard and budget.state == HardBudgetState.KNOWN_INFEASIBLE:
        reason_codes.append("hard_budget_known_infeasible")
        constraint_refs.append(constraint.constraint_id)
    return HardFilterResult(
        feasible=not reason_codes,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        constraint_refs=tuple(dict.fromkeys(constraint_refs)),
    )


def _dominates(left: _RootEvaluation, right: _RootEvaluation) -> bool:
    no_worse = (
        left.immediate_utility >= right.immediate_utility
        and left.preservation.known_preserved_pct >= right.preservation.known_preserved_pct
        and left.burden_tuple <= right.burden_tuple
    )
    strict = (
        left.immediate_utility > right.immediate_utility
        or left.preservation.known_preserved_pct > right.preservation.known_preserved_pct
        or left.burden_tuple < right.burden_tuple
    )
    return no_worse and strict


def _dense_ranks(values: list[Decimal | int]) -> dict[Decimal | int, int]:
    return {value: index for index, value in enumerate(sorted(set(values), reverse=True), 1)}


def _rank_frontier(evaluations: list[_RootEvaluation]) -> list[_RootEvaluation]:
    feasible = [item for item in evaluations if item.hard_filter.feasible]
    frontier = [
        item
        for item in feasible
        if not any(_dominates(other, item) for other in feasible if other is not item)
    ]
    immediate_ranks = _dense_ranks([item.immediate_utility for item in frontier])
    preservation_ranks = _dense_ranks(
        [item.preservation.known_preserved_pct for item in frontier]
    )
    frontier_ids = {item.festival_id for item in frontier}
    return [
        replace(
            item,
            on_frontier=item.festival_id in frontier_ids,
            immediate_rank=(
                immediate_ranks[item.immediate_utility]
                if item.festival_id in frontier_ids
                else None
            ),
            preservation_rank=(
                preservation_ranks[item.preservation.known_preserved_pct]
                if item.festival_id in frontier_ids
                else None
            ),
        )
        for item in evaluations
    ]


def _selection_key(item: _RootEvaluation, mode: PreservationMode):
    burden = item.burden_tuple
    if mode == PreservationMode.STRICT:
        return (
            -item.preservation.known_preserved_pct,
            burden,
            -item.immediate_utility,
            item.soft_budget_rank,
            item.festival_id,
        )
    if mode == PreservationMode.OPPORTUNISTIC:
        return (
            -item.immediate_utility,
            -item.preservation.known_preserved_pct,
            burden,
            item.soft_budget_rank,
            item.festival_id,
        )
    group_rank = {
        DecisionGradeGroup.LAUNCH_READY: 0,
        DecisionGradeGroup.VIABLE_NEXT: 1,
        DecisionGradeGroup.NOT_A_LAUNCH_ROOT: 2,
    }[item.grade_group]
    if item.immediate_rank is None or item.preservation_rank is None:
        raise PlannerInputError("balanced ranking requires dense frontier ranks")
    return (
        group_rank,
        max(item.immediate_rank, item.preservation_rank),
        item.immediate_rank + item.preservation_rank,
        item.preservation_rank,
        burden,
        item.soft_budget_rank,
        item.festival_id,
    )


def _diagnostic(item: _RootEvaluation) -> PlannerCandidateDiagnostic:
    return PlannerCandidateDiagnostic(
        festival_id=item.festival_id,
        hard_filter=item.hard_filter,
        on_pareto_frontier=item.on_frontier,
        immediate_utility=item.immediate_utility,
        decision_grade=item.candidate.decision_grade,
        decision_grade_group=item.grade_group,
        preservation=item.preservation,
        verification_burden=item.burden,
        budget_assessment=item.budget,
        immediate_rank=item.immediate_rank,
        preservation_rank=item.preservation_rank,
        soft_budget_preference_rank=item.soft_budget_rank,
        deterministic_tie_break_id=item.festival_id,
    )


class CampaignPlanner:
    """Implement the approved v2 Pareto and preservation-mode policy."""

    def plan(self, planning_input: PlanningInput) -> CampaignPlan:
        if not isinstance(planning_input, PlanningInput):
            raise TypeError("CampaignPlanner accepts only the frozen PlanningInput contract")
        if planning_input.planning_input_hash != planning_input_hash(planning_input):
            raise PlannerInputError("PlanningInput hash does not match its frozen contents")

        candidates = {item.festival_id: item for item in planning_input.candidates}
        opportunities = {item.festival_id: item for item in planning_input.opportunities}
        edges = {
            (item.from_festival_id, item.to_festival_id): item
            for item in planning_input.compatibility_edges
        }
        locked_ids = sorted(
            item.festival_id
            for item in planning_input.opportunities
            if item.policy_state == OpportunityPolicyState.LOCKED
            and item.submission_state not in {SubmissionState.REJECTED, SubmissionState.WITHDRAWN}
        )
        if len(locked_ids) > 1:
            raise PlannerInputError(
                "multiple locked opportunity roots cannot be represented by one primary launch"
            )
        locked_id = locked_ids[0] if locked_ids else None

        evaluations: list[_RootEvaluation] = []
        for festival_id in sorted(candidates):
            candidate = candidates[festival_id]
            opportunity = opportunities[festival_id]
            budget = assess_budget(planning_input, festival_id)
            preservation = _preservation(
                planning_input, festival_id, candidates, opportunities, edges
            )
            hard_filter = _hard_filter(
                planning_input,
                candidate,
                opportunity,
                preservation,
                edges,
                locked_id,
            )
            evaluations.append(
                _RootEvaluation(
                    candidate=candidate,
                    opportunity=opportunity,
                    hard_filter=hard_filter,
                    budget=budget,
                    preservation=preservation,
                    burden=_verification_burden(
                        planning_input, candidate, opportunity, preservation, budget
                    ),
                    soft_budget_rank=_soft_budget_rank(planning_input, budget),
                )
            )

        evaluations = _rank_frontier(evaluations)
        frontier = [item for item in evaluations if item.on_frontier]
        if not frontier:
            raise NoFeasibleLaunchError("no feasible launch root survives hard filtering")
        ordered = sorted(
            frontier,
            key=lambda item: _selection_key(item, planning_input.preservation_mode),
        )
        primary = ordered[0]
        alternatives = ordered[1:3]
        constraint = planning_input.budget_constraint
        if constraint and not constraint.hard and primary.budget.state != HardBudgetState.KNOWN_FEASIBLE:
            raise PlannerInputError(
                "frozen CampaignPlan cannot serialize a selected soft-budget route that is "
                "unknown or over preference without mislabeling it as a hard-budget result"
            )

        plan = self._construct_plan(
            planning_input,
            primary,
            alternatives,
            evaluations,
            candidates,
            opportunities,
            edges,
        )
        from app.campaign.validation import StrategyValidator

        return StrategyValidator().validate(plan, planning_input)

    def _construct_plan(
        self,
        planning_input: PlanningInput,
        primary: _RootEvaluation,
        alternatives: list[_RootEvaluation],
        evaluations: list[_RootEvaluation],
        candidates: dict[str, FrozenCandidateEvidence],
        opportunities: dict[str, CampaignOpportunity],
        edges: dict[tuple[str, str], CompatibilityEdge],
    ) -> CampaignPlan:
        gates = self._gates(planning_input, primary, candidates, edges)
        post_ids = self._post_premiere_ids(primary, candidates, opportunities)
        alternative_models = tuple(
            AlternativeLaunch(
                festival_id=item.festival_id,
                activates_on="primary_rejected_or_withdrawn",
            )
            for item in alternatives
        )
        rejection = (
            RejectionBranch(
                of_festival_id=primary.festival_id,
                promote_festival_id=alternatives[0].festival_id,
            )
            if alternatives
            else None
        )
        blocking_primary_gates = [
            gate
            for gate in gates
            if gate.blocking
            and (
                gate.affected_decision.startswith("budget")
                or gate.affected_decision.startswith("primary_launch")
            )
        ]
        next_actions = [
            NextAction(
                action_id=f"submit-{primary.festival_id}",
                festival_id=primary.festival_id,
                description=f"Submit {primary.festival_id} as the primary launch route.",
                required_now=True,
                gated_by=(blocking_primary_gates[0].gate_id if blocking_primary_gates else None),
            )
        ]
        for gate in gates:
            next_actions.append(
                NextAction(
                    action_id=f"action-{gate.gate_id}",
                    festival_id=self._gate_festival_id(gate, candidates),
                    description=f"Resolve verification gate {gate.gate_id} before its affected decision.",
                    required_now=gate in blocking_primary_gates,
                    gated_by=gate.gate_id,
                )
            )

        reason_refs = [
            f"score:{primary.festival_id}",
            f"candidate:{primary.candidate.component_hash}",
        ]
        reason_refs.extend(
            f"edge:{edges[(primary.festival_id, target_id)].edge_hash}"
            for target_id in (
                *primary.preservation.preserved_ids,
                *primary.preservation.verify_ids,
                *primary.preservation.destroyed_ids,
            )
        )
        for constraint_ref in primary.hard_filter.constraint_refs:
            reason_refs.append(f"constraint:{constraint_ref}")

        draft = CampaignPlan(
            primary_launch=PrimaryLaunch(
                festival_id=primary.festival_id,
                submission_action=f"Submit {primary.festival_id} as the primary launch route.",
                screening_gate=f"confirmed-public-screening:{primary.festival_id}",
                reason_refs=tuple(dict.fromkeys(reason_refs)),
            ),
            alternative_launches=alternative_models,
            rejection_branch=rejection,
            screened_branch=ScreenedBranch(
                at_festival_id=primary.festival_id,
                premiere_effect=self._premiere_effect(planning_input, primary.candidate),
                post_premiere_opportunity_ids=post_ids,
            ),
            verification_gates=gates,
            budget=primary.budget,
            post_premiere_opportunities=post_ids,
            option_preservation=primary.preservation,
            clarifications=(),
            next_actions=tuple(next_actions),
            selection_diagnostics=tuple(_diagnostic(item) for item in evaluations),
            plan_hash="0" * 64,
        )
        from app.campaign.clarification import ClarificationEngine

        clarifications = ClarificationEngine().generate(planning_input, draft)
        initial = draft.model_copy(update={"clarifications": clarifications})
        return initial.model_copy(update={"plan_hash": campaign_plan_hash(initial)})

    @staticmethod
    def _post_premiere_ids(
        primary: _RootEvaluation,
        candidates: dict[str, FrozenCandidateEvidence],
        opportunities: dict[str, CampaignOpportunity],
    ) -> tuple[str, ...]:
        eligible_ids = (
            *primary.preservation.preserved_ids,
            *primary.preservation.verify_ids,
        )
        actionable = [
            festival_id
            for festival_id in eligible_ids
            if not _terminal_or_unavailable(candidates[festival_id], opportunities[festival_id])
            and candidates[festival_id].risk.deadline.status != DeadlineStatus.CLOSED
        ]
        return tuple(
            sorted(actionable, key=lambda item: (-future_quality(candidates[item]), item))
        )

    @staticmethod
    def _premiere_effect(
        planning_input: PlanningInput,
        candidate: FrozenCandidateEvidence,
    ) -> PremiereEffect:
        film_country = planning_input.profile.country
        festival_country = candidate.retrieved.identity.country
        if (
            film_country.status == FactStatus.CONFIRMED
            and festival_country.status == FactStatus.CONFIRMED
            and film_country.value
            and festival_country.value
        ):
            international = (
                PremiereAvailability.AVAILABLE
                if film_country.value.casefold() == festival_country.value.casefold()
                else PremiereAvailability.CONSUMED
            )
        else:
            international = PremiereAvailability.UNKNOWN
        return PremiereEffect(
            world=PremiereAvailability.CONSUMED,
            international=international,
        )

    @staticmethod
    def _gates(
        planning_input: PlanningInput,
        primary: _RootEvaluation,
        candidates: dict[str, FrozenCandidateEvidence],
        edges: dict[tuple[str, str], CompatibilityEdge],
    ) -> tuple[VerificationGate, ...]:
        gates: list[VerificationGate] = []
        for target_id in primary.preservation.verify_ids:
            edge = edges[(primary.festival_id, target_id)]
            gates.append(
                VerificationGate(
                    id=f"verify-edge-{primary.festival_id}-to-{target_id}",
                    fact_key=f"compatibility.{primary.festival_id}.{target_id}",
                    affected_decision=f"post_premiere:{target_id}",
                    blocking=False,
                    source_refs=edge.evidence_refs,
                )
            )
        for item in _verification_items(primary.candidate, primary.opportunity):
            gates.append(
                VerificationGate(
                    id=f"verify-item-{item.item_id}",
                    fact_key=item.fact_key,
                    affected_decision=f"primary_launch:{primary.festival_id}",
                    blocking=item.blocking,
                    source_refs=item.source_refs,
                )
            )
        if primary.budget.state == HardBudgetState.VERIFY:
            source_refs = tuple(
                dict.fromkeys(
                    ref
                    for required in _budget_fees_for_root(planning_input, primary.festival_id)
                    if required.fee_id in primary.budget.unknown_fee_ids
                    for ref in required.fee.source_refs
                )
            )
            gates.append(
                VerificationGate(
                    id="verify-budget-required-fees",
                    fact_key="budget.required_fees",
                    affected_decision="budget:primary_launch",
                    blocking=bool(
                        planning_input.budget_constraint
                        and planning_input.budget_constraint.hard
                    ),
                    source_refs=source_refs,
                )
            )
        return tuple(sorted(gates, key=lambda gate: gate.gate_id))

    @staticmethod
    def _gate_festival_id(
        gate: VerificationGate,
        candidates: dict[str, FrozenCandidateEvidence],
    ) -> str | None:
        for festival_id in sorted(candidates):
            if festival_id in gate.affected_decision or festival_id in gate.fact_key:
                return festival_id
        return None


__all__ = [
    "FUTURE_QUALITY_DIMENSIONS",
    "CampaignPlanner",
    "NoFeasibleLaunchError",
    "PlannerError",
    "PlannerInputError",
    "assess_budget",
    "future_quality",
]
