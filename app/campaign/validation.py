"""Cross-contract validation for deterministic campaign plans."""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.campaign.contracts import (
    campaign_plan_hash,
    compatibility_edge_hash,
    planning_input_hash,
)
from app.campaign.models import (
    CampaignPlan,
    CompatibilityStatus,
    ConstraintStrength,
    HardBudgetState,
    OpportunityPolicyState,
    PlanningInput,
    PremiereScope,
    SubmissionState,
)


class StrategyValidationError(ValueError):
    """Collect every deterministic plan validation defect in stable order."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = tuple(dict.fromkeys(errors))
        super().__init__("; ".join(self.errors))


class StrategyValidator:
    """Validate plan grounding, policy, budget, and no-fabrication rules."""

    def validate(self, plan: CampaignPlan, planning_input: PlanningInput) -> CampaignPlan:
        if not isinstance(plan, CampaignPlan):
            raise TypeError("StrategyValidator requires a frozen CampaignPlan")
        if not isinstance(planning_input, PlanningInput):
            raise TypeError("StrategyValidator requires a frozen PlanningInput")

        errors: list[str] = []
        known_ids = frozenset(planning_input.candidate_ids)
        try:
            grounded_plan = CampaignPlan.model_validate(
                plan.model_dump(mode="python", by_alias=True),
                context={"known_festival_ids": known_ids},
            )
        except ValueError as exc:
            raise StrategyValidationError((f"campaign plan schema/ID grounding failed: {exc}",)) from exc

        if planning_input.planning_input_hash != planning_input_hash(planning_input):
            errors.append("PlanningInput hash does not match frozen contents")
        if grounded_plan.plan_hash != campaign_plan_hash(grounded_plan):
            errors.append("CampaignPlan hash does not match frozen contents")

        edges = {
            (edge.from_festival_id, edge.to_festival_id): edge
            for edge in planning_input.compatibility_edges
        }
        candidates = {item.festival_id: item for item in planning_input.candidates}
        opportunities = {item.festival_id: item for item in planning_input.opportunities}

        self._validate_edge_evidence(planning_input, errors)
        self._validate_route_ids(grounded_plan, known_ids, errors)
        self._validate_diagnostics(grounded_plan, known_ids, errors)
        self._validate_preservation(grounded_plan, edges, known_ids, errors)
        self._validate_branches(grounded_plan, edges, errors)
        self._validate_hard_policy(
            grounded_plan, planning_input, candidates, opportunities, edges, errors
        )
        self._validate_budget(grounded_plan, planning_input, errors)
        self._validate_prose(grounded_plan, planning_input, errors)

        if errors:
            raise StrategyValidationError(errors)
        return grounded_plan

    @staticmethod
    def _validate_edge_evidence(planning_input: PlanningInput, errors: list[str]) -> None:
        for edge in planning_input.compatibility_edges:
            label = f"{edge.from_festival_id}->{edge.to_festival_id}"
            if not edge.reason_code or not edge.evidence_refs:
                errors.append(f"compatibility edge {label} lacks explicit reason/evidence refs")
            if edge.edge_hash != compatibility_edge_hash(edge):
                errors.append(f"compatibility edge {label} has an invalid edge_hash")
            if (
                edge.status in {
                    CompatibilityStatus.COMPATIBLE,
                    CompatibilityStatus.INCOMPATIBLE,
                }
                and edge.source_confidence.value != "confirmed"
            ):
                errors.append(f"compatibility edge {label} asserts certainty without confirmed evidence")
            if (
                edge.status == CompatibilityStatus.VERIFY
                and edge.source_confidence.value == "confirmed"
            ):
                errors.append(f"verify edge {label} cannot claim confirmed compatibility certainty")

    @staticmethod
    def _validate_route_ids(
        plan: CampaignPlan,
        known_ids: frozenset[str],
        errors: list[str],
    ) -> None:
        route_ids = {
            plan.primary_launch.festival_id,
            *(item.festival_id for item in plan.alternative_launches),
        }
        referenced = set(route_ids)
        referenced.update(plan.post_premiere_opportunities)
        referenced.update(plan.option_preservation.preserved_ids)
        referenced.update(plan.option_preservation.verify_ids)
        referenced.update(plan.option_preservation.destroyed_ids)
        referenced.update(
            action.festival_id for action in plan.next_actions if action.festival_id
        )
        referenced.update(item.festival_id for item in plan.selection_diagnostics)
        if plan.screened_branch:
            referenced.add(plan.screened_branch.at_festival_id)
            referenced.update(plan.screened_branch.post_premiere_opportunity_ids)
        if plan.rejection_branch:
            referenced.add(plan.rejection_branch.of_festival_id)
            referenced.add(plan.rejection_branch.promote_festival_id)
        unknown = referenced - known_ids
        if unknown:
            errors.append("plan references ungrounded festival IDs: " + ", ".join(sorted(unknown)))
        if plan.primary_launch.festival_id in {
            *plan.option_preservation.preserved_ids,
            *plan.option_preservation.verify_ids,
            *plan.option_preservation.destroyed_ids,
        }:
            errors.append("primary launch cannot appear in its downstream preservation sets")
        if not plan.primary_launch.reason_refs:
            errors.append("primary launch requires explicit reason/evidence refs")
        if f"score:{plan.primary_launch.festival_id}" not in plan.primary_launch.reason_refs:
            errors.append("primary launch reason refs must ground its frozen immediate score")

    @staticmethod
    def _validate_diagnostics(
        plan: CampaignPlan,
        known_ids: frozenset[str],
        errors: list[str],
    ) -> None:
        diagnostic_ids = [item.festival_id for item in plan.selection_diagnostics]
        if len(diagnostic_ids) != len(set(diagnostic_ids)):
            errors.append("selection diagnostics contain duplicate festival IDs")
        if set(diagnostic_ids) != known_ids:
            errors.append("selection diagnostics must cover every frozen candidate exactly once")
        by_id = {item.festival_id: item for item in plan.selection_diagnostics}
        selected_ids = {
            plan.primary_launch.festival_id,
            *(item.festival_id for item in plan.alternative_launches),
        }
        for festival_id in selected_ids:
            diagnostic = by_id.get(festival_id)
            if not diagnostic:
                continue
            if not diagnostic.hard_filter.feasible:
                errors.append(f"selected route {festival_id} failed hard filtering")
            if not diagnostic.on_pareto_frontier:
                errors.append(f"selected route {festival_id} is Pareto-dominated")
        primary = by_id.get(plan.primary_launch.festival_id)
        if primary and primary.preservation != plan.option_preservation:
            errors.append("plan preservation diagnostics do not match the selected root")

    @staticmethod
    def _validate_preservation(
        plan: CampaignPlan,
        edges,
        known_ids: frozenset[str],
        errors: list[str],
    ) -> None:
        primary_id = plan.primary_launch.festival_id
        partitions = {
            CompatibilityStatus.COMPATIBLE: plan.option_preservation.preserved_ids,
            CompatibilityStatus.VERIFY: plan.option_preservation.verify_ids,
            CompatibilityStatus.INCOMPATIBLE: plan.option_preservation.destroyed_ids,
        }
        for expected_status, festival_ids in partitions.items():
            for festival_id in festival_ids:
                if festival_id not in known_ids:
                    continue
                edge = edges.get((primary_id, festival_id))
                if not edge or edge.status != expected_status:
                    errors.append(
                        f"preservation claim {primary_id}->{festival_id} is not supported by a "
                        f"{expected_status.value} edge"
                    )
        if set(plan.post_premiere_opportunities) & set(
            plan.option_preservation.destroyed_ids
        ):
            errors.append("destroyed opportunities cannot appear on the post-premiere route")

    @staticmethod
    def _validate_branches(plan: CampaignPlan, edges, errors: list[str]) -> None:
        primary_id = plan.primary_launch.festival_id
        alternative_ids = {item.festival_id for item in plan.alternative_launches}
        if len(plan.alternative_launches) > 2:
            errors.append("CampaignPlan has more than two alternative launches")
        if plan.rejection_branch:
            if plan.rejection_branch.of_festival_id != primary_id:
                errors.append("rejection branch is not grounded in the primary launch")
            if plan.rejection_branch.promote_festival_id not in alternative_ids:
                errors.append("rejection branch does not promote a grounded alternative")
        if plan.screened_branch:
            if plan.screened_branch.at_festival_id != primary_id:
                errors.append("screened branch is not conditional on the primary screening")
            if tuple(plan.screened_branch.post_premiere_opportunity_ids) != tuple(
                plan.post_premiere_opportunities
            ):
                errors.append("screened branch and post-premiere route IDs disagree")
        gate_decisions = {gate.affected_decision for gate in plan.verification_gates}
        for festival_id in plan.post_premiere_opportunities:
            edge = edges.get((primary_id, festival_id))
            if edge is None:
                errors.append(f"post-premiere route {festival_id} has no grounded edge")
            elif edge.status == CompatibilityStatus.INCOMPATIBLE:
                errors.append(f"post-premiere route {festival_id} is incompatible")
            elif (
                edge.status == CompatibilityStatus.VERIFY
                and f"post_premiere:{festival_id}" not in gate_decisions
            ):
                errors.append(f"verify post-premiere route {festival_id} lacks a named gate")

    @staticmethod
    def _validate_hard_policy(
        plan: CampaignPlan,
        planning_input: PlanningInput,
        candidates,
        opportunities,
        edges,
        errors: list[str],
    ) -> None:
        primary_id = plan.primary_launch.festival_id
        primary_candidate = candidates[primary_id]
        primary_opportunity = opportunities[primary_id]
        if not primary_candidate.risk.eligible or primary_candidate.risk.runtime_eligible is False:
            errors.append("primary launch violates confirmed hard eligibility")
        if primary_opportunity.submission_state in {
            SubmissionState.REJECTED,
            SubmissionState.WITHDRAWN,
        }:
            errors.append("primary launch is terminal and not selectable")
        if primary_opportunity.policy_state == OpportunityPolicyState.EXCLUDED:
            errors.append("primary launch is excluded")
        locked_ids = {
            item.festival_id
            for item in planning_input.opportunities
            if item.policy_state == OpportunityPolicyState.LOCKED
            and item.submission_state not in {
                SubmissionState.REJECTED,
                SubmissionState.WITHDRAWN,
            }
        }
        if locked_ids and primary_id not in locked_ids:
            errors.append("primary launch overrides a locked opportunity")

        scope_constraints = {
            "preserve_world_premiere": PremiereScope.WORLD,
            "preserve_international_premiere": PremiereScope.INTERNATIONAL,
            "preserve_continental_premiere": PremiereScope.CONTINENTAL,
            "preserve_territorial_premiere": PremiereScope.TERRITORIAL,
        }
        for constraint in planning_input.constraints:
            binding = constraint.active and (
                constraint.strength == ConstraintStrength.HARD or constraint.locked
            )
            if not binding:
                continue
            if constraint.constraint_type in scope_constraints and bool(constraint.value):
                scope = scope_constraints[constraint.constraint_type]
                if any(
                    edge.from_festival_id == primary_id
                    and edge.scope == scope
                    and edge.status == CompatibilityStatus.INCOMPATIBLE
                    and edge.to_festival_id in plan.option_preservation.destroyed_ids
                    for edge in planning_input.compatibility_edges
                ):
                    errors.append(f"primary launch violates {constraint.constraint_type}")
            elif constraint.constraint_type == "require_festival":
                values = (
                    set(constraint.value)
                    if isinstance(constraint.value, tuple)
                    else {str(constraint.value)}
                )
                if primary_id not in values:
                    errors.append("primary launch violates required festival lock")
            elif constraint.constraint_type == "exclude_festival":
                values = (
                    set(constraint.value)
                    if isinstance(constraint.value, tuple)
                    else {str(constraint.value)}
                )
                if primary_id in values:
                    errors.append("primary launch violates excluded festival constraint")

    @staticmethod
    def _validate_budget(
        plan: CampaignPlan,
        planning_input: PlanningInput,
        errors: list[str],
    ) -> None:
        from app.campaign.planning import assess_budget

        expected = assess_budget(planning_input, plan.primary_launch.festival_id)
        if plan.budget != expected:
            errors.append(
                "plan budget does not equal the selected route's required-now fee assessment"
            )
        if expected is None:
            if any(
                item.budget_assessment is not None
                for item in plan.selection_diagnostics
            ):
                errors.append("diagnostics without a hard budget carry budget assessments")
            if any(
                gate.blocking and gate.affected_decision.startswith("budget")
                for gate in plan.verification_gates
            ):
                errors.append("a plan without a hard budget carries a blocking budget gate")
        elif plan.budget is not None and plan.budget.state == HardBudgetState.KNOWN_INFEASIBLE:
            errors.append("selected route is known infeasible under its hard budget")
        if plan.budget is not None and plan.budget.state == HardBudgetState.VERIFY:
            if not any(
                gate.blocking and gate.affected_decision.startswith("budget")
                for gate in plan.verification_gates
            ):
                errors.append("VERIFY budget lacks a blocking budget gate")
        included = set(plan.budget.included_fee_ids) if plan.budget is not None else set()
        for required in planning_input.required_fees:
            inactive = not required.required_now or required.action_scope.value in {
                "rejection_alternative",
                "screening_branch",
                "post_premiere",
                "hypothetical_later",
            }
            if inactive and required.fee_id in included:
                errors.append(f"inactive branch fee {required.fee_id} was included")
            if (
                required.fee.status.value in {"unknown", "contradicted"}
                and required.fee.amount == 0
            ):
                errors.append(f"unknown fee {required.fee_id} was represented as zero")

    @staticmethod
    def _validate_prose(
        plan: CampaignPlan,
        planning_input: PlanningInput,
        errors: list[str],
    ) -> None:
        prose = "\n".join(
            [
                plan.primary_launch.submission_action,
                *(action.description for action in plan.next_actions),
                *(
                    clarification.question
                    for clarification in plan.clarifications
                    if clarification.question
                ),
            ]
        )
        lowered = prose.casefold()
        if re.search(r"\b(?:probability|probable|odds)\b|\b\d+(?:\.\d+)?%", lowered):
            errors.append("plan prose fabricates a probability")
        if re.search(r"\bwill\s+(?:be\s+)?(?:accepted|rejected|invited|screened)\b", lowered):
            errors.append("plan prose fabricates a future outcome")
        if (
            plan.budget is not None
            and plan.budget.state == HardBudgetState.VERIFY
            and re.search(
            r"\b(?:confirmed|definitely|known)\s+(?:budget[- ]?)?feasible\b|\bwithin budget\b",
            lowered,
            )
        ):
            errors.append("VERIFY budget is described as confirmed feasible")

        grounded_dates = {planning_input.as_of_date.isoformat()}
        grounded_dates.update(
            candidate.risk.deadline.next_deadline.isoformat()
            for candidate in planning_input.candidates
            if candidate.risk.deadline.next_deadline
        )
        for date_text in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", prose):
            if date_text not in grounded_dates:
                errors.append(f"plan prose invents an ungrounded date: {date_text}")


__all__ = ["StrategyValidationError", "StrategyValidator"]
