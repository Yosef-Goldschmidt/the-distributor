"""Deterministic, plan-dependent campaign clarification ranking."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.campaign.models import (
    CampaignPlan,
    Clarification,
    ClarificationImpact,
    FactStatus,
    HardBudgetState,
    PlanningInput,
    PremiereAvailability,
    PremiereScope,
)


@dataclass(frozen=True, slots=True)
class _CatalogEntry:
    domain_priority: int
    tested_states: tuple[str, ...]


STATIC_CATALOG: Final[dict[str, _CatalogEntry]] = {
    "premiere": _CatalogEntry(0, ("available", "consumed")),
    "screening": _CatalogEntry(1, ("public", "private")),
    "fee": _CatalogEntry(2, ("known_within_budget", "known_over_budget")),
    "identity.format_country": _CatalogEntry(3, ("current_candidates_only",)),
    "runtime": _CatalogEntry(4, ("eligible", "ineligible")),
    "preservation_mode": _CatalogEntry(5, ("balanced", "strict", "opportunistic")),
    "credits.composer": _CatalogEntry(99, ()),
}

_IMPACT_RANK: Final[dict[ClarificationImpact, int]] = {
    ClarificationImpact.BLOCKING_HARD_DECISION: 0,
    ClarificationImpact.CHANGES_PRIMARY: 1,
    ClarificationImpact.CHANGES_ALTERNATIVE_OR_POST: 2,
    ClarificationImpact.CHANGES_VERIFICATION_OR_ORDER: 3,
    ClarificationImpact.NON_DECISION_CHANGING: 4,
}


def _catalog_entry(fact_key: str) -> _CatalogEntry:
    for prefix, entry in STATIC_CATALOG.items():
        if fact_key == prefix or fact_key.startswith(f"{prefix}."):
            return entry
    return _CatalogEntry(50, ())


class ClarificationEngine:
    """Apply the static catalog, plan dependency, and finite sensitivity layers."""

    def generate(
        self,
        planning_input: PlanningInput,
        plan: CampaignPlan,
        *,
        include_suppressed: bool = False,
    ) -> tuple[Clarification, ...]:
        if not isinstance(planning_input, PlanningInput):
            raise TypeError("ClarificationEngine requires a frozen PlanningInput")
        if not isinstance(plan, CampaignPlan):
            raise TypeError("ClarificationEngine requires a frozen CampaignPlan")

        clarifications: list[Clarification] = []
        clarifications.extend(self._premiere_clarifications(planning_input, plan))
        clarifications.extend(self._budget_clarifications(planning_input, plan))
        clarifications.extend(self._gate_clarifications(planning_input, plan))
        identity = self._identity_clarification(planning_input)
        if identity:
            clarifications.append(identity)
        if include_suppressed:
            clarifications.append(
                self.assess_fact(
                    "credits.composer", FactStatus.UNKNOWN, planning_input, plan
                )
            )
        return tuple(
            sorted(
                {item.clarification_id: item for item in clarifications}.values(),
                key=lambda item: self._rank_key(item, planning_input),
            )
        )

    def assess_fact(
        self,
        fact_key: str,
        status: FactStatus,
        planning_input: PlanningInput,
        plan: CampaignPlan,
    ) -> Clarification:
        """Return an auditable visible or suppressed catalog decision."""

        if fact_key == "credits.composer":
            return Clarification(
                clarification_id="clarify-composer",
                fact_key=fact_key,
                question=None,
                impact=ClarificationImpact.NON_DECISION_CHANGING,
                affected_decisions=(),
                blocking=False,
                contradiction=status == FactStatus.CONTRADICTED,
                tested_states=(),
                candidate_set_lower_bound=False,
                suppressed=True,
                suppression_reason="no_current_plan_dependency",
            )
        dependency = tuple(
            gate.affected_decision
            for gate in plan.verification_gates
            if gate.fact_key == fact_key
        )
        if not dependency:
            return Clarification(
                clarification_id=f"clarify-{fact_key.replace('.', '-')}",
                fact_key=fact_key,
                question=None,
                impact=ClarificationImpact.NON_DECISION_CHANGING,
                blocking=False,
                contradiction=status == FactStatus.CONTRADICTED,
                tested_states=_catalog_entry(fact_key).tested_states,
                suppressed=True,
                suppression_reason="no_current_plan_dependency",
            )
        blocking = any(
            gate.blocking for gate in plan.verification_gates if gate.fact_key == fact_key
        )
        return Clarification(
            clarification_id=f"clarify-{fact_key.replace('.', '-')}",
            fact_key=fact_key,
            question=f"Confirm {fact_key} for the affected campaign decision.",
            impact=(
                ClarificationImpact.BLOCKING_HARD_DECISION
                if blocking
                else ClarificationImpact.CHANGES_VERIFICATION_OR_ORDER
            ),
            affected_decisions=dependency,
            blocking=blocking,
            contradiction=status == FactStatus.CONTRADICTED,
            tested_states=_catalog_entry(fact_key).tested_states,
        )

    @staticmethod
    def _premiere_clarifications(
        planning_input: PlanningInput,
        plan: CampaignPlan,
    ) -> list[Clarification]:
        results: list[Clarification] = []
        primary_id = plan.primary_launch.festival_id
        primary_candidate = next(
            item for item in planning_input.candidates if item.festival_id == primary_id
        )
        relevant_scopes = {
            primary_candidate.risk.premiere_constraint.scope,
            *(
                edge.scope
                for edge in planning_input.compatibility_edges
                if edge.from_festival_id == primary_id
                and edge.to_festival_id
                in set(plan.option_preservation.verify_ids)
            ),
        }
        primary_blocking_scopes = {primary_candidate.risk.premiere_constraint.scope}
        for constraint in planning_input.constraints:
            if constraint.active and constraint.constraint_type.startswith("preserve_"):
                scope_name = constraint.constraint_type.removeprefix("preserve_").removesuffix(
                    "_premiere"
                )
                try:
                    constraint_scope = PremiereScope(scope_name)
                    relevant_scopes.add(constraint_scope)
                    if constraint.locked or constraint.strength.value == "hard":
                        primary_blocking_scopes.add(constraint_scope)
                except ValueError:
                    pass
        for state in planning_input.premiere_ledger.scopes:
            if state.scope not in relevant_scopes:
                continue
            unresolved = state.availability == PremiereAvailability.UNKNOWN
            if not unresolved and not state.contradiction:
                continue
            fact_key = f"premiere.{state.scope.value}"
            if state.contradiction:
                results.append(
                    Clarification(
                        clarification_id=(
                            "clarify-premiere-contradiction"
                            if state.scope == PremiereScope.WORLD
                            else f"clarify-{state.scope.value}-premiere-contradiction"
                        ),
                        fact_key=fact_key,
                        question="Has the film had any confirmed public screening?",
                        impact=ClarificationImpact.BLOCKING_HARD_DECISION,
                        affected_decisions=("primary_launch",),
                        blocking=True,
                        contradiction=True,
                        tested_states=("available", "consumed"),
                    )
                )
            else:
                affects_primary = state.scope in primary_blocking_scopes
                results.append(
                    Clarification(
                        clarification_id=(
                            "clarify-premiere-primary"
                            if state.scope == PremiereScope.WORLD
                            else f"clarify-{state.scope.value}-premiere"
                        ),
                        fact_key=fact_key,
                        question=(
                            "Is the world premiere still available?"
                            if state.scope == PremiereScope.WORLD
                            else f"Is the {state.scope.value} premiere still available?"
                        ),
                        impact=(
                            ClarificationImpact.CHANGES_PRIMARY
                            if affects_primary
                            else ClarificationImpact.CHANGES_VERIFICATION_OR_ORDER
                        ),
                        affected_decisions=(
                            (f"primary_launch:{primary_id}",)
                            if affects_primary
                            else ("post_premiere_gate",)
                        ),
                        blocking=affects_primary,
                        contradiction=False,
                        tested_states=("available", "consumed"),
                    )
                )
        return results

    @staticmethod
    def _budget_clarifications(
        planning_input: PlanningInput,
        plan: CampaignPlan,
    ) -> list[Clarification]:
        if plan.budget.state != HardBudgetState.VERIFY:
            return []
        unknown = set(plan.budget.unknown_fee_ids)
        required = [
            item
            for item in planning_input.required_fees
            if item.fee_id in unknown
        ]
        multiple = len(required) > 1
        results: list[Clarification] = []
        candidates = {item.festival_id: item for item in planning_input.candidates}
        hard = bool(
            planning_input.budget_constraint and planning_input.budget_constraint.hard
        )
        for item in sorted(required, key=lambda value: value.fee_id):
            name_fact = candidates[item.festival_id].retrieved.identity.festival_name
            name = name_fact
            results.append(
                Clarification(
                    clarification_id=(
                        f"clarify-required-fee-{item.fee_id}"
                        if multiple
                        else "clarify-required-fee"
                    ),
                    fact_key=f"fee.{item.festival_id}",
                    question=(
                        f"What is the exact current {name} submission fee in "
                        f"{plan.budget.hard_limit.currency}?"
                    ),
                    impact=(
                        ClarificationImpact.BLOCKING_HARD_DECISION
                        if hard
                        else ClarificationImpact.CHANGES_VERIFICATION_OR_ORDER
                    ),
                    affected_decisions=("budget:primary_launch",),
                    blocking=hard,
                    tested_states=("known_within_budget", "known_over_budget"),
                )
            )
        return results

    @staticmethod
    def _gate_clarifications(
        planning_input: PlanningInput,
        plan: CampaignPlan,
    ) -> list[Clarification]:
        del planning_input
        results: list[Clarification] = []
        for gate in plan.verification_gates:
            if gate.affected_decision.startswith("budget"):
                continue
            if gate.fact_key == "screening.future.access":
                results.append(
                    Clarification(
                        clarification_id="clarify-later-access",
                        fact_key=gate.fact_key,
                        question="Will the later industry screening be public?",
                        impact=ClarificationImpact.CHANGES_VERIFICATION_OR_ORDER,
                        affected_decisions=("post_premiere_gate",),
                        blocking=False,
                        tested_states=("public", "private"),
                    )
                )
                continue
            later_only = gate.affected_decision.startswith("post_premiere")
            results.append(
                Clarification(
                    clarification_id=f"clarify-{gate.gate_id}",
                    fact_key=gate.fact_key,
                    question=f"Verify {gate.fact_key} before changing the affected route.",
                    impact=(
                        ClarificationImpact.CHANGES_VERIFICATION_OR_ORDER
                        if later_only or not gate.blocking
                        else ClarificationImpact.BLOCKING_HARD_DECISION
                    ),
                    affected_decisions=(gate.affected_decision,),
                    blocking=False if later_only else gate.blocking,
                    tested_states=_catalog_entry(gate.fact_key).tested_states,
                )
            )
        return results

    @staticmethod
    def _identity_clarification(
        planning_input: PlanningInput,
    ) -> Clarification | None:
        format_unresolved = planning_input.profile.format.status in {
            FactStatus.UNKNOWN,
            FactStatus.CONTRADICTED,
        }
        country_unresolved = planning_input.profile.country.status in {
            FactStatus.UNKNOWN,
            FactStatus.CONTRADICTED,
        }
        if not (format_unresolved or country_unresolved):
            return None
        return Clarification(
            clarification_id="clarify-format-country",
            fact_key="identity.format_country",
            question="Confirm the film format and origin country before refreshing candidates.",
            impact=ClarificationImpact.CHANGES_PRIMARY,
            affected_decisions=(
                "current_candidate_eligibility",
                "territorial_compatibility",
            ),
            blocking=False,
            contradiction=(
                planning_input.profile.format.status == FactStatus.CONTRADICTED
                or planning_input.profile.country.status == FactStatus.CONTRADICTED
            ),
            tested_states=("current_candidates_only",),
            candidate_set_lower_bound=True,
        )

    @staticmethod
    def _rank_key(
        clarification: Clarification,
        planning_input: PlanningInput,
    ) -> tuple[int, Decimal, int, int, int, str, str]:
        from app.campaign.planning import future_quality

        affected_ids = {
            candidate.festival_id
            for candidate in planning_input.candidates
            if any(
                candidate.festival_id in decision
                for decision in clarification.affected_decisions
            )
        }
        affected_quality = sum(
            (
                future_quality(candidate)
                for candidate in planning_input.candidates
                if candidate.festival_id in affected_ids
            ),
            Decimal(0),
        )
        return (
            _IMPACT_RANK[clarification.impact],
            -affected_quality,
            -len(clarification.affected_decisions),
            0 if clarification.contradiction else 1,
            _catalog_entry(clarification.fact_key).domain_priority,
            clarification.fact_key,
            clarification.clarification_id,
        )


__all__ = ["STATIC_CATALOG", "ClarificationEngine"]
