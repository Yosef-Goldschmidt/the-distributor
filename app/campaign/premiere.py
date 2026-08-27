"""Deterministic, evidence-linked premiere ledger derivation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.campaign.contracts import canonical_hash
from app.campaign.models import (
    CampaignProfile,
    FactStatus,
    PremiereAvailability,
    PremiereLedgerSnapshot,
    PremiereScope,
    PremiereScopeState,
    ScreeningAccess,
    ScreeningSnapshot,
    ScreeningState,
)


PREMIERE_LEDGER_POLICY_VERSION = "premiere-ledger-v1"


@dataclass(frozen=True, slots=True)
class TrackedPremiereScope:
    """A territorial or continental scope that the current campaign tracks."""

    scope: PremiereScope
    territory: str

    def __post_init__(self) -> None:
        if self.scope not in {PremiereScope.CONTINENTAL, PremiereScope.TERRITORIAL}:
            raise ValueError("tracked scopes must be continental or territorial")
        if not self.territory.strip():
            raise ValueError("tracked premiere scopes require a territory")


_UNSCREENED_PATTERNS = (
    re.compile(r"\bno\s+prior\s+public\s+(?:screening|exhibition)\b", re.I),
    re.compile(r"\bnever\s+(?:publicly\s+)?(?:screened|premiered|shown|released)\b", re.I),
    re.compile(r"\bworld\s+premiere(?:\s+(?:status|rights))?\s+(?:is\s+|remains\s+)?available\b", re.I),
    re.compile(r"\bunscreened\b", re.I),
)

_DOMESTIC_ONLY_PATTERNS = (
    re.compile(
        r"\bonly\s+prior\s+public\s+(?:screening|exhibition).{0,40}\bhome\s+country\b",
        re.I,
    ),
    re.compile(
        r"\bno\s+prior\s+international\s+public\s+(?:screening|exhibition)\b",
        re.I,
    ),
)

_COUNTRY_ALIASES = {
    "u s": "united states",
    "u s a": "united states",
    "usa": "united states",
    "us": "united states",
    "united states of america": "united states",
    "uk": "united kingdom",
    "u k": "united kingdom",
}

_COUNTRY_CONTINENTS = {
    "austria": "europe",
    "canada": "north america",
    "estonia": "europe",
    "france": "europe",
    "germany": "europe",
    "greece": "europe",
    "israel": "asia",
    "italy": "europe",
    "netherlands": "europe",
    "spain": "europe",
    "united kingdom": "europe",
    "united states": "north america",
}


def _normalise(value: str | None) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    return _COUNTRY_ALIASES.get(normalized, normalized)


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in items if item))


def _screening_refs(screenings: Iterable[ScreeningSnapshot]) -> tuple[str, ...]:
    refs: list[str] = []
    for screening in screenings:
        refs.extend(screening.source_refs or (f"screening:{screening.screening_id}",))
    return _unique(refs)


def _is_unscreened_assertion(value: str | None) -> bool:
    return bool(value and any(pattern.search(value) for pattern in _UNSCREENED_PATTERNS))


def _assertion_evidence(profile: CampaignProfile) -> tuple[tuple[datetime, tuple[str, ...]], ...]:
    assertions: list[tuple[datetime, tuple[str, ...]]] = []
    for fact in profile.premiere_assertions:
        if (
            fact.status in {FactStatus.CONFIRMED, FactStatus.ASSERTED}
            and _is_unscreened_assertion(fact.value)
        ):
            assertions.append((fact.observed_at, fact.source_refs))
    return tuple(sorted(assertions, key=lambda item: item[0]))


def _domestic_only_assertion_evidence(
    profile: CampaignProfile,
) -> tuple[tuple[datetime, tuple[str, ...]], ...]:
    assertions: list[tuple[datetime, tuple[str, ...]]] = []
    for fact in profile.premiere_assertions:
        if (
            fact.status in {FactStatus.CONFIRMED, FactStatus.ASSERTED}
            and fact.value
            and any(pattern.search(fact.value) for pattern in _DOMESTIC_ONLY_PATTERNS)
        ):
            assertions.append((fact.observed_at, fact.source_refs))
    return tuple(sorted(assertions, key=lambda item: item[0]))


def _has_assertion_contradiction(profile: CampaignProfile) -> bool:
    return any(fact.status == FactStatus.CONTRADICTED for fact in profile.premiere_assertions)


def _contradicted_assertion_refs(profile: CampaignProfile) -> tuple[str, ...]:
    return _unique(
        ref
        for fact in profile.premiere_assertions
        if fact.status == FactStatus.CONTRADICTED
        for ref in fact.source_refs
    )


def _sort_screenings(screenings: Iterable[ScreeningSnapshot]) -> tuple[ScreeningSnapshot, ...]:
    maximum = datetime.max.replace(tzinfo=timezone.utc)
    return tuple(
        sorted(
            screenings,
            key=lambda item: (
                item.occurred_at or item.scheduled_at or maximum,
                item.screening_id,
                item.festival_id or "",
            ),
        )
    )


def _duplicate_contradictions(
    screenings: tuple[ScreeningSnapshot, ...],
) -> tuple[set[str], tuple[str, ...]]:
    by_id: dict[str, list[ScreeningSnapshot]] = {}
    for screening in screenings:
        by_id.setdefault(screening.screening_id, []).append(screening)
    contradictory: set[str] = set()
    refs: list[str] = []
    for screening_id, rows in by_id.items():
        signatures = {
            (
                row.state,
                row.access,
                _normalise(row.country),
                _normalise(row.region),
                row.scheduled_at,
                row.occurred_at,
            )
            for row in rows
        }
        if len(signatures) > 1:
            contradictory.add(screening_id)
            refs.extend(_screening_refs(rows))
    return contradictory, _unique(refs)


def _confirmed_public(screening: ScreeningSnapshot) -> bool:
    return bool(
        screening.state == ScreeningState.OCCURRED
        and screening.access == ScreeningAccess.PUBLIC
        and screening.source_refs
    )


def _unresolved_occurrence(screening: ScreeningSnapshot) -> bool:
    if screening.state != ScreeningState.OCCURRED:
        return False
    if screening.access == ScreeningAccess.UNKNOWN:
        return True
    return screening.access == ScreeningAccess.PUBLIC and not screening.source_refs


def _assertion_conflicts(
    screening: ScreeningSnapshot,
    assertions: tuple[tuple[datetime, tuple[str, ...]], ...],
) -> bool:
    if not _confirmed_public(screening):
        return False
    if screening.occurred_at is None:
        return False
    return any(screening.occurred_at <= observed_at for observed_at, _ in assertions)


def _assertion_refs(
    assertions: tuple[tuple[datetime, tuple[str, ...]], ...],
) -> tuple[str, ...]:
    return _unique(ref for _, refs in assertions for ref in refs)


def _non_consuming_reason(screenings: tuple[ScreeningSnapshot, ...]) -> str:
    if any(
        screening.state == ScreeningState.OCCURRED
        and screening.access == ScreeningAccess.PRIVATE
        and any("correct" in ref.lower() for ref in screening.source_refs)
        for screening in screenings
    ):
        return "corrected_access_private"
    if any(screening.state == ScreeningState.OCCURRED and screening.access == ScreeningAccess.PRIVATE for screening in screenings):
        return "private_screening"
    if any(screening.state == ScreeningState.SCHEDULED for screening in screenings):
        return "not_yet_occurred"
    if any(screening.state == ScreeningState.CANCELLED for screening in screenings):
        return "cancelled_screening"
    return "sourced_unscreened_assertion"


def _matches_scope(screening: ScreeningSnapshot, tracked: TrackedPremiereScope) -> bool | None:
    territory = _normalise(tracked.territory)
    country = _normalise(screening.country)
    region = _normalise(screening.region)
    if tracked.scope == PremiereScope.TERRITORIAL:
        if not country:
            return None
        return country == territory
    if region:
        return territory in region or region in territory
    if not country:
        return None
    continent = _COUNTRY_CONTINENTS.get(country)
    if continent is None:
        return None
    return continent == territory


class PremiereLedger:
    """Derive the current scoped ledger solely from film facts and screenings."""

    policy_version = PREMIERE_LEDGER_POLICY_VERSION

    def derive(
        self,
        profile: CampaignProfile,
        screenings: Iterable[ScreeningSnapshot],
        *,
        tracked_scopes: Iterable[TrackedPremiereScope] = (),
    ) -> PremiereLedgerSnapshot:
        ordered = _sort_screenings(screenings)
        tracked = tuple(
            sorted(
                set(tracked_scopes),
                key=lambda item: (item.scope.value, _normalise(item.territory)),
            )
        )
        assertions = _assertion_evidence(profile)
        domestic_only_assertions = _domestic_only_assertion_evidence(profile)
        assertion_refs = _assertion_refs(assertions)
        contradicted_assertion_refs = _contradicted_assertion_refs(profile)
        duplicate_ids, duplicate_refs = _duplicate_contradictions(ordered)
        public = tuple(
            row for row in ordered if _confirmed_public(row) and row.screening_id not in duplicate_ids
        )
        unresolved = tuple(
            row for row in ordered if _unresolved_occurrence(row) and row.screening_id not in duplicate_ids
        )

        scopes = [
            self._world_scope(
                profile,
                ordered,
                public,
                unresolved,
                assertions,
                assertion_refs,
                contradicted_assertion_refs,
                duplicate_refs,
            ),
            self._international_scope(
                profile,
                ordered,
                public,
                unresolved,
                assertions,
                domestic_only_assertions,
                assertion_refs,
                contradicted_assertion_refs,
                duplicate_refs,
            ),
        ]
        scopes.extend(
            self._tracked_scope(
                profile,
                ordered,
                public,
                unresolved,
                assertions,
                assertion_refs,
                contradicted_assertion_refs,
                duplicate_refs,
                item,
            )
            for item in tracked
        )
        input_payload = {
            "policy_version": self.policy_version,
            "film_country": profile.country,
            "premiere_assertions": profile.premiere_assertions,
            "screenings": ordered,
            "tracked_scopes": [
                {"scope": item.scope, "territory": item.territory} for item in tracked
            ],
        }
        input_hash = canonical_hash(input_payload)
        provisional = PremiereLedgerSnapshot(
            scopes=tuple(scopes),
            derivation_policy_version=self.policy_version,
            input_hash=input_hash,
            ledger_hash="0" * 64,
        )
        return provisional.model_copy(
            update={
                "ledger_hash": canonical_hash(
                    provisional, exclude_fields=frozenset({"ledger_hash"})
                )
            }
        )

    @staticmethod
    def tracked_scopes_from_snapshot(
        snapshot: PremiereLedgerSnapshot,
    ) -> tuple[TrackedPremiereScope, ...]:
        return tuple(
            TrackedPremiereScope(scope=item.scope, territory=item.territory)
            for item in snapshot.scopes
            if item.scope in {PremiereScope.CONTINENTAL, PremiereScope.TERRITORIAL}
            and item.territory
        )

    def _world_scope(
        self,
        profile: CampaignProfile,
        screenings: tuple[ScreeningSnapshot, ...],
        public: tuple[ScreeningSnapshot, ...],
        unresolved: tuple[ScreeningSnapshot, ...],
        assertions: tuple[tuple[datetime, tuple[str, ...]], ...],
        assertion_refs: tuple[str, ...],
        contradicted_assertion_refs: tuple[str, ...],
        duplicate_refs: tuple[str, ...],
    ) -> PremiereScopeState:
        if duplicate_refs or _has_assertion_contradiction(profile):
            return self._contradiction(
                PremiereScope.WORLD,
                None,
                duplicate_refs + assertion_refs + contradicted_assertion_refs,
            )
        conflicting = tuple(row for row in public if _assertion_conflicts(row, assertions))
        if conflicting:
            return self._contradiction(
                PremiereScope.WORLD,
                None,
                assertion_refs + _screening_refs(conflicting),
                "unscreened_assertion_conflicts_with_public_occurrence",
            )
        if public:
            first = public[0]
            return PremiereScopeState(
                scope=PremiereScope.WORLD,
                availability=PremiereAvailability.CONSUMED,
                reason_code="confirmed_occurred_public",
                evidence_refs=_screening_refs((first,)),
            )
        if unresolved:
            return PremiereScopeState(
                scope=PremiereScope.WORLD,
                availability=PremiereAvailability.UNKNOWN,
                reason_code="screening_access_verify",
                evidence_refs=_screening_refs(unresolved),
            )
        if assertions:
            return PremiereScopeState(
                scope=PremiereScope.WORLD,
                availability=PremiereAvailability.AVAILABLE,
                reason_code=_non_consuming_reason(screenings),
                evidence_refs=assertion_refs + _screening_refs(
                    row
                    for row in screenings
                    if row.state != ScreeningState.OCCURRED
                    or row.access == ScreeningAccess.PRIVATE
                ),
            )
        return PremiereScopeState(
            scope=PremiereScope.WORLD,
            availability=PremiereAvailability.UNKNOWN,
            reason_code="absence_is_not_availability",
        )

    def _international_scope(
        self,
        profile: CampaignProfile,
        screenings: tuple[ScreeningSnapshot, ...],
        public: tuple[ScreeningSnapshot, ...],
        unresolved: tuple[ScreeningSnapshot, ...],
        assertions: tuple[tuple[datetime, tuple[str, ...]], ...],
        domestic_only_assertions: tuple[tuple[datetime, tuple[str, ...]], ...],
        assertion_refs: tuple[str, ...],
        contradicted_assertion_refs: tuple[str, ...],
        duplicate_refs: tuple[str, ...],
    ) -> PremiereScopeState:
        if duplicate_refs or _has_assertion_contradiction(profile):
            return self._contradiction(
                PremiereScope.INTERNATIONAL,
                None,
                duplicate_refs + assertion_refs + contradicted_assertion_refs,
            )
        film_country = (
            _normalise(profile.country.value)
            if profile.country.status == FactStatus.CONFIRMED
            else ""
        )
        known_location = tuple(row for row in public if _normalise(row.country))
        unknown_location = tuple(row for row in public if not _normalise(row.country))
        marked_home_country = tuple(
            row
            for row in public
            if any("home-country" in ref.casefold() for ref in row.source_refs)
        )
        domestic_assertion_refs = _assertion_refs(domestic_only_assertions)
        if domestic_only_assertions:
            unmarked_public = tuple(row for row in public if row not in marked_home_country)
            if unmarked_public:
                return self._contradiction(
                    PremiereScope.INTERNATIONAL,
                    None,
                    domestic_assertion_refs + _screening_refs(unmarked_public),
                    "domestic_only_assertion_conflicts_with_public_occurrence",
                )
            return PremiereScopeState(
                scope=PremiereScope.INTERNATIONAL,
                availability=PremiereAvailability.AVAILABLE,
                reason_code="single_home_country_public_only",
                evidence_refs=domestic_assertion_refs + _screening_refs(public),
            )
        if public and not film_country:
            return PremiereScopeState(
                scope=PremiereScope.INTERNATIONAL,
                availability=PremiereAvailability.UNKNOWN,
                reason_code="film_country_unknown",
                evidence_refs=_screening_refs(public) + profile.country.source_refs,
            )
        foreign = tuple(row for row in known_location if _normalise(row.country) != film_country)
        conflicting = tuple(row for row in foreign if _assertion_conflicts(row, assertions))
        if conflicting:
            return self._contradiction(
                PremiereScope.INTERNATIONAL,
                None,
                assertion_refs + _screening_refs(conflicting),
                "unscreened_assertion_conflicts_with_foreign_public_occurrence",
            )
        if foreign:
            return PremiereScopeState(
                scope=PremiereScope.INTERNATIONAL,
                availability=PremiereAvailability.CONSUMED,
                reason_code="confirmed_foreign_public",
                evidence_refs=_screening_refs((foreign[0],)) + profile.country.source_refs,
            )
        if unknown_location:
            return PremiereScopeState(
                scope=PremiereScope.INTERNATIONAL,
                availability=PremiereAvailability.UNKNOWN,
                reason_code="screening_country_unknown",
                evidence_refs=_screening_refs(unknown_location) + profile.country.source_refs,
            )
        if unresolved:
            return PremiereScopeState(
                scope=PremiereScope.INTERNATIONAL,
                availability=PremiereAvailability.UNKNOWN,
                reason_code="screening_access_verify",
                evidence_refs=_screening_refs(unresolved),
            )
        if assertions:
            reason = "domestic_public_only" if public else _non_consuming_reason(screenings)
            return PremiereScopeState(
                scope=PremiereScope.INTERNATIONAL,
                availability=PremiereAvailability.AVAILABLE,
                reason_code=reason,
                evidence_refs=assertion_refs + _screening_refs(public),
            )
        return PremiereScopeState(
            scope=PremiereScope.INTERNATIONAL,
            availability=PremiereAvailability.UNKNOWN,
            reason_code="international_history_unsupported",
            evidence_refs=_screening_refs(public),
        )

    def _tracked_scope(
        self,
        profile: CampaignProfile,
        screenings: tuple[ScreeningSnapshot, ...],
        public: tuple[ScreeningSnapshot, ...],
        unresolved: tuple[ScreeningSnapshot, ...],
        assertions: tuple[tuple[datetime, tuple[str, ...]], ...],
        assertion_refs: tuple[str, ...],
        contradicted_assertion_refs: tuple[str, ...],
        duplicate_refs: tuple[str, ...],
        tracked: TrackedPremiereScope,
    ) -> PremiereScopeState:
        if duplicate_refs or _has_assertion_contradiction(profile):
            return self._contradiction(
                tracked.scope,
                tracked.territory,
                duplicate_refs + assertion_refs + contradicted_assertion_refs,
            )
        matched: list[ScreeningSnapshot] = []
        unknown_location: list[ScreeningSnapshot] = []
        for screening in public:
            match = _matches_scope(screening, tracked)
            if match is True:
                matched.append(screening)
            elif match is None:
                unknown_location.append(screening)
        conflicting = tuple(row for row in matched if _assertion_conflicts(row, assertions))
        if conflicting:
            return self._contradiction(
                tracked.scope,
                tracked.territory,
                assertion_refs + _screening_refs(conflicting),
            )
        if matched:
            return PremiereScopeState(
                scope=tracked.scope,
                territory=tracked.territory,
                availability=PremiereAvailability.CONSUMED,
                reason_code=(
                    "confirmed_continental_public"
                    if tracked.scope == PremiereScope.CONTINENTAL
                    else "confirmed_territorial_public"
                ),
                evidence_refs=_screening_refs((matched[0],)),
            )
        if unknown_location or unresolved:
            return PremiereScopeState(
                scope=tracked.scope,
                territory=tracked.territory,
                availability=PremiereAvailability.UNKNOWN,
                reason_code="territorial_scope_unresolved",
                evidence_refs=_screening_refs((*unknown_location, *unresolved)),
            )
        if assertions:
            return PremiereScopeState(
                scope=tracked.scope,
                territory=tracked.territory,
                availability=PremiereAvailability.AVAILABLE,
                reason_code="screening_outside_tracked_scope" if public else _non_consuming_reason(screenings),
                evidence_refs=assertion_refs + _screening_refs(public),
            )
        return PremiereScopeState(
            scope=tracked.scope,
            territory=tracked.territory,
            availability=PremiereAvailability.UNKNOWN,
            reason_code="territorial_history_unsupported",
            evidence_refs=_screening_refs(public),
        )

    @staticmethod
    def _contradiction(
        scope: PremiereScope,
        territory: str | None,
        refs: Iterable[str],
        reason: str = "contradictory_screening_evidence",
    ) -> PremiereScopeState:
        return PremiereScopeState(
            scope=scope,
            territory=territory,
            availability=PremiereAvailability.UNKNOWN,
            contradiction=True,
            reason_code=reason,
            evidence_refs=_unique(refs),
        )


__all__ = [
    "PREMIERE_LEDGER_POLICY_VERSION",
    "PremiereLedger",
    "TrackedPremiereScope",
]
