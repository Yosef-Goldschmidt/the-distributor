"""Deterministic directed compatibility graph construction.

The builder consumes only frozen campaign contracts.  It does not consult the
legacy agent dictionaries, a provider, or a persistence layer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.campaign.contracts import compatibility_edge_hash
from app.campaign.models import (
    CampaignProfile,
    CompatibilityEdge,
    CompatibilityStatus,
    Fact,
    FactStatus,
    FrozenCandidateEvidence,
    PremiereAvailability,
    PremiereLedgerSnapshot,
    PremiereScope,
    PremiereScopeState,
)


GRAPH_POLICY_VERSION = "campaign-compatibility-v1"


class CompatibilityBuildError(ValueError):
    """Raised when frozen evidence cannot form a valid candidate graph."""


def _normalise(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _unique_refs(*groups: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(ref for group in groups for ref in group if ref))


def _confirmed_text(fact: Fact[str]) -> str | None:
    if fact.status != FactStatus.CONFIRMED or not fact.value:
        return None
    return fact.value


class CompatibilityBuilder:
    """Build the complete, evidence-grounded directed tri-state graph.

    An edge ``A -> B`` answers whether B remains pursuable as represented if A
    becomes the film's first confirmed public screening.
    """

    def __init__(self, graph_policy_version: str = GRAPH_POLICY_VERSION) -> None:
        if not graph_policy_version:
            raise ValueError("graph_policy_version must be explicit")
        self.graph_policy_version = graph_policy_version

    def build(
        self,
        candidates: Sequence[FrozenCandidateEvidence],
        profile: CampaignProfile,
        premiere_ledger: PremiereLedgerSnapshot,
    ) -> tuple[CompatibilityEdge, ...]:
        """Return every directed non-self edge in stable festival-ID order."""

        frozen_candidates = tuple(candidates)
        if not 1 <= len(frozen_candidates) <= 12:
            raise CompatibilityBuildError("compatibility graph requires 1 to 12 candidates")
        ids = [candidate.festival_id for candidate in frozen_candidates]
        if len(ids) != len(set(ids)):
            raise CompatibilityBuildError("compatibility graph candidate IDs must be unique")
        if not isinstance(profile, CampaignProfile):
            raise TypeError("profile must be a frozen CampaignProfile")
        if not isinstance(premiere_ledger, PremiereLedgerSnapshot):
            raise TypeError("premiere_ledger must be a frozen PremiereLedgerSnapshot")

        ordered = sorted(frozen_candidates, key=lambda candidate: candidate.festival_id)
        return tuple(
            self._edge(source, target, profile, premiere_ledger)
            for source in ordered
            for target in ordered
            if source.festival_id != target.festival_id
        )

    def _edge(
        self,
        source: FrozenCandidateEvidence,
        target: FrozenCandidateEvidence,
        profile: CampaignProfile,
        ledger: PremiereLedgerSnapshot,
    ) -> CompatibilityEdge:
        constraint = target.risk.premiere_constraint
        scope = constraint.scope
        ledger_state = self._ledger_state(ledger, scope, constraint.territory)
        evidence_refs = self._evidence_refs(source, target, ledger, ledger_state)

        status, reason_code = self._classify(
            source=source,
            target=target,
            profile=profile,
            ledger_state=ledger_state,
        )
        confidence = (
            FactStatus.CONFIRMED
            if status != CompatibilityStatus.VERIFY
            else self._verify_confidence(source, target, profile, ledger_state)
        )
        initial = CompatibilityEdge(
            from_festival_id=source.festival_id,
            to_festival_id=target.festival_id,
            status=status,
            scope=scope,
            territory=constraint.territory,
            reason_code=reason_code,
            evidence_refs=evidence_refs,
            source_confidence=confidence,
            graph_policy_version=self.graph_policy_version,
            edge_hash="0" * 64,
        )
        return initial.model_copy(update={"edge_hash": compatibility_edge_hash(initial)})

    @staticmethod
    def _ledger_state(
        ledger: PremiereLedgerSnapshot,
        scope: PremiereScope,
        territory: str | None,
    ) -> PremiereScopeState | None:
        if scope == PremiereScope.NONE:
            return None
        exact = [
            item
            for item in ledger.scopes
            if item.scope == scope
            and (
                scope not in {PremiereScope.CONTINENTAL, PremiereScope.TERRITORIAL}
                or _normalise(item.territory) == _normalise(territory)
            )
        ]
        return exact[0] if len(exact) == 1 else None

    @staticmethod
    def _evidence_refs(
        source: FrozenCandidateEvidence,
        target: FrozenCandidateEvidence,
        ledger: PremiereLedgerSnapshot,
        ledger_state: PremiereScopeState | None,
    ) -> tuple[str, ...]:
        constraint = target.risk.premiere_constraint
        return _unique_refs(
            (f"ledger:{ledger.ledger_hash}",),
            ledger_state.evidence_refs if ledger_state else (),
            constraint.evidence_refs,
            target.retrieved.premiere_rule.source_refs,
            source.retrieved.identity.country.source_refs,
            source.retrieved.identity.region.source_refs,
            (f"candidate:{source.component_hash}", f"candidate:{target.component_hash}"),
        )

    @staticmethod
    def _verify_confidence(
        source: FrozenCandidateEvidence,
        target: FrozenCandidateEvidence,
        profile: CampaignProfile,
        ledger_state: PremiereScopeState | None,
    ) -> FactStatus:
        statuses = {
            target.risk.premiere_constraint.rule_status,
            source.retrieved.identity.country.status,
            source.retrieved.identity.region.status,
            profile.country.status,
        }
        if ledger_state and ledger_state.contradiction:
            return FactStatus.CONTRADICTED
        if FactStatus.CONTRADICTED in statuses:
            return FactStatus.CONTRADICTED
        return FactStatus.UNKNOWN

    def _classify(
        self,
        *,
        source: FrozenCandidateEvidence,
        target: FrozenCandidateEvidence,
        profile: CampaignProfile,
        ledger_state: PremiereScopeState | None,
    ) -> tuple[CompatibilityStatus, str]:
        constraint = target.risk.premiere_constraint
        scope = constraint.scope

        if constraint.rule_status != FactStatus.CONFIRMED:
            return CompatibilityStatus.VERIFY, "target_premiere_rule_not_confirmed"
        if scope == PremiereScope.UNKNOWN:
            return CompatibilityStatus.VERIFY, "target_premiere_scope_unknown"
        if scope == PremiereScope.NONE:
            return CompatibilityStatus.COMPATIBLE, "target_has_no_premiere_restriction"
        if ledger_state is None:
            return CompatibilityStatus.VERIFY, "relevant_ledger_scope_missing"
        if ledger_state.contradiction or ledger_state.availability == PremiereAvailability.UNKNOWN:
            return CompatibilityStatus.VERIFY, "relevant_ledger_scope_unresolved"
        if ledger_state.availability == PremiereAvailability.CONSUMED:
            return CompatibilityStatus.INCOMPATIBLE, "relevant_premiere_scope_already_consumed"
        if scope == PremiereScope.WORLD:
            return CompatibilityStatus.INCOMPATIBLE, "first_public_screening_consumes_world_premiere"

        source_country = _confirmed_text(source.retrieved.identity.country)
        if scope == PremiereScope.INTERNATIONAL:
            film_country = _confirmed_text(profile.country)
            if not source_country or not film_country:
                return CompatibilityStatus.VERIFY, "international_territory_unresolved"
            if _normalise(source_country) == _normalise(film_country):
                return CompatibilityStatus.COMPATIBLE, "domestic_screening_preserves_international_premiere"
            return CompatibilityStatus.INCOMPATIBLE, "foreign_screening_consumes_international_premiere"

        if scope == PremiereScope.CONTINENTAL:
            source_region = _confirmed_text(source.retrieved.identity.region)
            if not source_region or not constraint.territory:
                return CompatibilityStatus.VERIFY, "continental_territory_unresolved"
            if _normalise(source_region) == _normalise(constraint.territory):
                return CompatibilityStatus.INCOMPATIBLE, "screening_consumes_continental_premiere"
            return CompatibilityStatus.COMPATIBLE, "screening_outside_continental_premiere_territory"

        if scope == PremiereScope.TERRITORIAL:
            if not source_country or not constraint.territory:
                return CompatibilityStatus.VERIFY, "territorial_scope_unresolved"
            if _normalise(source_country) == _normalise(constraint.territory):
                return CompatibilityStatus.INCOMPATIBLE, "screening_consumes_territorial_premiere"
            return CompatibilityStatus.COMPATIBLE, "screening_outside_premiere_territory"

        return CompatibilityStatus.VERIFY, "compatibility_not_established"


__all__ = [
    "GRAPH_POLICY_VERSION",
    "CompatibilityBuildError",
    "CompatibilityBuilder",
]
