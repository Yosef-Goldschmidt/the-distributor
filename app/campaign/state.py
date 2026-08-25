"""Pure deterministic campaign state transitions for Phase 1A."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from app.campaign.contracts import (
    COMMAND_EVENT_TYPES,
    campaign_profile_hash,
    campaign_snapshot_hash,
)
from app.campaign.models import (
    CampaignCommand,
    CampaignCommandType,
    CampaignConstraint,
    CampaignEvent,
    CampaignLifecycle,
    CampaignOpportunity,
    CampaignProfile,
    CampaignReadiness,
    CampaignSnapshot,
    Fact,
    FactStatus,
    OfferState,
    OpportunityPolicyState,
    PremiereAvailability,
    ProfileFactKey,
    ScreeningAccess,
    ScreeningSnapshot,
    ScreeningState,
    SubmissionState,
    VerificationItem,
)
from app.campaign.premiere import PremiereLedger


class CampaignStateError(ValueError):
    """Base error for rejected pure state transitions."""


class VersionConflict(CampaignStateError):
    def __init__(self, expected_version: int, current_version: int) -> None:
        super().__init__(
            f"campaign version conflict: expected {expected_version}, current {current_version}"
        )
        self.expected_version = expected_version
        self.current_version = current_version


class InvalidTransition(CampaignStateError):
    """The command is validly typed but invalid for the current projection."""


@dataclass(frozen=True, slots=True)
class StateReduction:
    snapshot: CampaignSnapshot
    event: CampaignEvent
    ledger_rederived: bool


def _profile_with_hash(profile: CampaignProfile) -> CampaignProfile:
    return profile.model_copy(update={"profile_hash": campaign_profile_hash(profile)})


def _replace_profile_fact(
    profile: CampaignProfile,
    fact_key: ProfileFactKey,
    fact: Fact[object],
) -> CampaignProfile:
    payload = profile.model_dump(mode="python")
    direct_fields = {
        ProfileFactKey.TITLE: "title",
        ProfileFactKey.SYNOPSIS: "synopsis",
        ProfileFactKey.FORMAT: "format",
        ProfileFactKey.COUNTRY: "country",
        ProfileFactKey.THEMES: "themes",
        ProfileFactKey.RUNTIME_MINUTES: "runtime_minutes",
    }
    if fact_key in direct_fields:
        payload[direct_fields[fact_key]] = fact.model_dump(mode="python")
    elif fact_key == ProfileFactKey.PREMIERE_ASSERTION:
        payload["premiere_assertions"] = [
            *payload["premiere_assertions"],
            fact.model_dump(mode="python"),
        ]
    elif fact_key == ProfileFactKey.TARGET_REGION:
        if not isinstance(fact.value, str):
            raise InvalidTransition("target_region updates require a known string value")
        payload["target_regions"] = sorted(
            {*payload["target_regions"], fact.value}, key=str.casefold
        )
    else:  # pragma: no cover - the frozen enum makes this defensive only
        raise InvalidTransition(f"unsupported profile fact: {fact_key}")
    payload["profile_hash"] = "0" * 64
    return _profile_with_hash(CampaignProfile.model_validate(payload))


def _replace_item(items: tuple[object, ...], index: int, value: object) -> tuple[object, ...]:
    mutable = list(items)
    mutable[index] = value
    return tuple(mutable)


class CampaignStateReducer:
    """Apply one frozen command without side effects or partial mutation."""

    def __init__(self, ledger: PremiereLedger | None = None) -> None:
        self._ledger = ledger or PremiereLedger()

    def reduce(
        self,
        snapshot: CampaignSnapshot,
        command: CampaignCommand,
        *,
        event_id: str,
        sequence_no: int,
        occurred_at: datetime,
        prior_event: CampaignEvent | None = None,
    ) -> StateReduction:
        if campaign_snapshot_hash(snapshot) != snapshot.aggregate_hash:
            raise CampaignStateError("campaign snapshot aggregate_hash is invalid")
        if command.expected_version != snapshot.campaign_version:
            raise VersionConflict(command.expected_version, snapshot.campaign_version)
        if snapshot.lifecycle == CampaignLifecycle.CLOSED:
            raise InvalidTransition("closed campaigns reject further commands")
        if sequence_no < 1:
            raise CampaignStateError("event sequence numbers start at one")

        profile = snapshot.profile
        constraints = snapshot.constraints
        opportunities = snapshot.opportunities
        screenings = snapshot.screenings
        lifecycle = snapshot.lifecycle
        ledger_rederived = False

        command_type = command.type
        payload = command.payload

        if command_type == CampaignCommandType.UPDATE_PROFILE_FACT:
            profile = _replace_profile_fact(profile, payload.fact_key, payload.fact)
            ledger_rederived = True
        elif command_type == CampaignCommandType.SET_CONSTRAINT:
            constraints = self._set_constraint(constraints, payload.constraint)
        elif command_type == CampaignCommandType.REMOVE_CONSTRAINT:
            constraints = self._remove_constraint(
                constraints, payload.constraint_id, payload.explicit_unlock
            )
        elif command_type in {
            CampaignCommandType.LOCK_OPPORTUNITY,
            CampaignCommandType.UNLOCK_OPPORTUNITY,
            CampaignCommandType.EXCLUDE_OPPORTUNITY,
            CampaignCommandType.INCLUDE_OPPORTUNITY,
        }:
            opportunities = self._change_policy(opportunities, payload.festival_id, command_type)
        elif command_type == CampaignCommandType.MARK_SUBMITTED:
            opportunities = self._mark_submitted(opportunities, payload.festival_id)
        elif command_type == CampaignCommandType.RECORD_REJECTION:
            opportunities = self._record_rejection(
                opportunities, payload.festival_id, payload.source_refs
            )
        elif command_type == CampaignCommandType.RECORD_INVITATION:
            opportunities = self._record_invitation(
                opportunities, payload.festival_id, payload.source_refs
            )
        elif command_type == CampaignCommandType.ACCEPT_OFFER:
            opportunities = self._decide_offer(
                opportunities, payload.festival_id, OfferState.ACCEPTED
            )
        elif command_type == CampaignCommandType.DECLINE_OFFER:
            opportunities = self._decide_offer(
                opportunities, payload.festival_id, OfferState.DECLINED
            )
        elif command_type == CampaignCommandType.WITHDRAW:
            opportunities = self._withdraw(
                opportunities, screenings, payload.festival_id
            )
        elif command_type == CampaignCommandType.SCHEDULE_SCREENING:
            screenings = self._schedule_screening(screenings, opportunities, payload)
        elif command_type == CampaignCommandType.CONFIRM_SCREENING:
            screenings = self._confirm_screening(screenings, payload)
            ledger_rederived = True
        elif command_type == CampaignCommandType.CANCEL_SCREENING:
            screenings, cancelled_occurred = self._cancel_screening(screenings, payload)
            ledger_rederived = cancelled_occurred
        elif command_type == CampaignCommandType.VERIFY_OPPORTUNITY_FACT:
            opportunities = self._verify_opportunity_fact(
                opportunities,
                payload.festival_id,
                payload.verification_item_id,
                payload.result,
            )
        elif command_type == CampaignCommandType.CORRECT_RECORD:
            if prior_event is None or prior_event.event_id != payload.prior_ref:
                raise InvalidTransition("corrections require the referenced prior event")
            profile, constraints, opportunities, screenings, correction_affects_ledger = (
                self._correct_record(
                    profile,
                    constraints,
                    opportunities,
                    screenings,
                    prior_event,
                    payload.replacement,
                    event_id,
                )
            )
            ledger_rederived = correction_affects_ledger or (
                payload.corrected_domain.value in {"identity", "domain_evidence"}
            )
        elif command_type == CampaignCommandType.CLOSE_CAMPAIGN:
            lifecycle = CampaignLifecycle.CLOSED
        else:  # pragma: no cover - exhaustive over the frozen discriminator
            raise InvalidTransition(f"unsupported command: {command_type}")

        premiere_ledger = snapshot.premiere_ledger
        if ledger_rederived:
            premiere_ledger = self._ledger.derive(
                profile,
                screenings,
                tracked_scopes=self._ledger.tracked_scopes_from_snapshot(
                    snapshot.premiere_ledger
                ),
            )
            if lifecycle != CampaignLifecycle.CLOSED:
                world = next(
                    (
                        item
                        for item in premiere_ledger.scopes
                        if item.scope.value == "world" and item.territory is None
                    ),
                    None,
                )
                if world and world.availability == PremiereAvailability.CONSUMED:
                    lifecycle = CampaignLifecycle.POST_PREMIERE
                elif snapshot.lifecycle == CampaignLifecycle.POST_PREMIERE:
                    lifecycle = CampaignLifecycle.ACTIVE

        provisional = snapshot.model_copy(
            update={
                "campaign_version": snapshot.campaign_version + 1,
                "lifecycle": lifecycle,
                "readiness": CampaignReadiness.STALE,
                "profile": profile,
                "premiere_ledger": premiere_ledger,
                "screenings": screenings,
                "constraints": constraints,
                "opportunities": opportunities,
                "aggregate_hash": "0" * 64,
            }
        )
        after = provisional.model_copy(
            update={"aggregate_hash": campaign_snapshot_hash(provisional)}
        )
        event = CampaignEvent(
            event_id=event_id,
            campaign_id=snapshot.campaign_id,
            sequence_no=sequence_no,
            type=COMMAND_EVENT_TYPES[command.type],
            command=command,
            before_aggregate_hash=snapshot.aggregate_hash,
            after_aggregate_hash=after.aggregate_hash,
            occurred_at=occurred_at,
        )
        return StateReduction(
            snapshot=after,
            event=event,
            ledger_rederived=ledger_rederived,
        )

    @staticmethod
    def _opportunity(
        opportunities: tuple[CampaignOpportunity, ...], festival_id: str
    ) -> tuple[int, CampaignOpportunity]:
        for index, opportunity in enumerate(opportunities):
            if opportunity.festival_id == festival_id:
                return index, opportunity
        raise InvalidTransition(f"unknown campaign opportunity: {festival_id}")

    @staticmethod
    def _set_constraint(
        constraints: tuple[CampaignConstraint, ...], constraint: CampaignConstraint
    ) -> tuple[CampaignConstraint, ...]:
        if not constraint.active:
            raise InvalidTransition("set_constraint cannot silently deactivate a constraint")
        for index, current in enumerate(constraints):
            if current.constraint_id != constraint.constraint_id:
                continue
            if current.locked and current != constraint:
                raise InvalidTransition(
                    "locked constraints require explicit removal/unlock before replacement"
                )
            if current == constraint:
                raise InvalidTransition("constraint is already set to that value")
            return _replace_item(constraints, index, constraint)  # type: ignore[return-value]
        return (*constraints, constraint)

    @staticmethod
    def _remove_constraint(
        constraints: tuple[CampaignConstraint, ...],
        constraint_id: str,
        explicit_unlock: bool,
    ) -> tuple[CampaignConstraint, ...]:
        for index, current in enumerate(constraints):
            if current.constraint_id != constraint_id or not current.active:
                continue
            if current.locked and not explicit_unlock:
                raise InvalidTransition("locked constraints require explicit_unlock")
            replacement = current.model_copy(update={"active": False, "locked": False})
            return _replace_item(constraints, index, replacement)  # type: ignore[return-value]
        raise InvalidTransition(f"active constraint not found: {constraint_id}")

    def _change_policy(
        self,
        opportunities: tuple[CampaignOpportunity, ...],
        festival_id: str,
        command_type: CampaignCommandType,
    ) -> tuple[CampaignOpportunity, ...]:
        index, current = self._opportunity(opportunities, festival_id)
        expected_and_target = {
            CampaignCommandType.LOCK_OPPORTUNITY: (
                OpportunityPolicyState.NORMAL,
                OpportunityPolicyState.LOCKED,
            ),
            CampaignCommandType.UNLOCK_OPPORTUNITY: (
                OpportunityPolicyState.LOCKED,
                OpportunityPolicyState.NORMAL,
            ),
            CampaignCommandType.EXCLUDE_OPPORTUNITY: (
                OpportunityPolicyState.NORMAL,
                OpportunityPolicyState.EXCLUDED,
            ),
            CampaignCommandType.INCLUDE_OPPORTUNITY: (
                OpportunityPolicyState.EXCLUDED,
                OpportunityPolicyState.NORMAL,
            ),
        }
        expected, target = expected_and_target[command_type]
        if current.policy_state != expected:
            raise InvalidTransition(
                f"{command_type.value} requires policy state {expected.value}"
            )
        return _replace_item(
            opportunities, index, current.model_copy(update={"policy_state": target})
        )  # type: ignore[return-value]

    def _mark_submitted(
        self, opportunities: tuple[CampaignOpportunity, ...], festival_id: str
    ) -> tuple[CampaignOpportunity, ...]:
        index, current = self._opportunity(opportunities, festival_id)
        if current.policy_state == OpportunityPolicyState.EXCLUDED:
            raise InvalidTransition("excluded opportunities cannot be submitted")
        if current.submission_state != SubmissionState.NOT_SUBMITTED:
            raise InvalidTransition("mark_submitted requires not_submitted")
        return _replace_item(
            opportunities,
            index,
            current.model_copy(update={"submission_state": SubmissionState.SUBMITTED}),
        )  # type: ignore[return-value]

    def _record_rejection(
        self,
        opportunities: tuple[CampaignOpportunity, ...],
        festival_id: str,
        source_refs: Iterable[str],
    ) -> tuple[CampaignOpportunity, ...]:
        index, current = self._opportunity(opportunities, festival_id)
        imported = any(ref.lower().startswith("import:") for ref in source_refs)
        if current.submission_state != SubmissionState.SUBMITTED and not imported:
            raise InvalidTransition(
                "record_rejection requires submitted state or an import: source"
            )
        return _replace_item(
            opportunities,
            index,
            current.model_copy(
                update={
                    "submission_state": SubmissionState.REJECTED,
                    "offer_state": OfferState.NONE,
                }
            ),
        )  # type: ignore[return-value]

    def _record_invitation(
        self,
        opportunities: tuple[CampaignOpportunity, ...],
        festival_id: str,
        source_refs: Iterable[str],
    ) -> tuple[CampaignOpportunity, ...]:
        index, current = self._opportunity(opportunities, festival_id)
        if not tuple(source_refs):
            raise InvalidTransition("direct invitations require a source reference")
        if current.policy_state == OpportunityPolicyState.EXCLUDED:
            raise InvalidTransition("excluded opportunities cannot receive invitations")
        if current.submission_state not in {
            SubmissionState.NOT_SUBMITTED,
            SubmissionState.SUBMITTED,
        }:
            raise InvalidTransition("invitation requires an active nonterminal opportunity")
        return _replace_item(
            opportunities,
            index,
            current.model_copy(
                update={
                    "submission_state": SubmissionState.INVITED,
                    "offer_state": OfferState.PENDING,
                }
            ),
        )  # type: ignore[return-value]

    def _decide_offer(
        self,
        opportunities: tuple[CampaignOpportunity, ...],
        festival_id: str,
        target: OfferState,
    ) -> tuple[CampaignOpportunity, ...]:
        index, current = self._opportunity(opportunities, festival_id)
        if (
            current.submission_state != SubmissionState.INVITED
            or current.offer_state != OfferState.PENDING
        ):
            raise InvalidTransition("offer decisions require a pending invitation")
        return _replace_item(
            opportunities, index, current.model_copy(update={"offer_state": target})
        )  # type: ignore[return-value]

    def _withdraw(
        self,
        opportunities: tuple[CampaignOpportunity, ...],
        screenings: tuple[ScreeningSnapshot, ...],
        festival_id: str,
    ) -> tuple[CampaignOpportunity, ...]:
        index, current = self._opportunity(opportunities, festival_id)
        allowed = current.submission_state in {
            SubmissionState.SUBMITTED,
            SubmissionState.INVITED,
        } or current.offer_state == OfferState.ACCEPTED
        if not allowed:
            raise InvalidTransition("withdraw requires submitted, invited, or accepted state")
        if any(
            screening.festival_id == festival_id
            and screening.state == ScreeningState.OCCURRED
            for screening in screenings
        ):
            raise InvalidTransition("an opportunity cannot be withdrawn after screening")
        return _replace_item(
            opportunities,
            index,
            current.model_copy(update={"submission_state": SubmissionState.WITHDRAWN}),
        )  # type: ignore[return-value]

    def _schedule_screening(
        self,
        screenings: tuple[ScreeningSnapshot, ...],
        opportunities: tuple[CampaignOpportunity, ...],
        payload: object,
    ) -> tuple[ScreeningSnapshot, ...]:
        if any(item.screening_id == payload.screening_id for item in screenings):
            raise InvalidTransition(f"screening already exists: {payload.screening_id}")
        if payload.festival_id is not None:
            self._opportunity(opportunities, payload.festival_id)
        screening = ScreeningSnapshot(
            screening_id=payload.screening_id,
            festival_id=payload.festival_id,
            state=ScreeningState.SCHEDULED,
            access=payload.access,
            country=payload.country,
            region=payload.region,
            scheduled_at=payload.scheduled_at,
            source_refs=payload.source_refs,
        )
        return (*screenings, screening)

    @staticmethod
    def _screening(
        screenings: tuple[ScreeningSnapshot, ...], screening_id: str
    ) -> tuple[int, ScreeningSnapshot]:
        for index, screening in enumerate(screenings):
            if screening.screening_id == screening_id:
                return index, screening
        raise InvalidTransition(f"unknown screening: {screening_id}")

    def _confirm_screening(
        self, screenings: tuple[ScreeningSnapshot, ...], payload: object
    ) -> tuple[ScreeningSnapshot, ...]:
        index, current = self._screening(screenings, payload.screening_id)
        if current.state != ScreeningState.SCHEDULED:
            raise InvalidTransition("confirm_screening requires a scheduled screening")
        confirmed = current.model_copy(
            update={
                "state": ScreeningState.OCCURRED,
                "access": payload.access,
                "country": payload.country if payload.country is not None else current.country,
                "region": payload.region if payload.region is not None else current.region,
                "occurred_at": payload.occurred_at,
                "source_refs": payload.source_refs,
            }
        )
        return _replace_item(screenings, index, confirmed)  # type: ignore[return-value]

    def _cancel_screening(
        self, screenings: tuple[ScreeningSnapshot, ...], payload: object
    ) -> tuple[tuple[ScreeningSnapshot, ...], bool]:
        index, current = self._screening(screenings, payload.screening_id)
        if current.state == ScreeningState.CANCELLED:
            raise InvalidTransition("screening is already cancelled")
        if current.state == ScreeningState.OCCURRED and not payload.correction:
            raise InvalidTransition("occurred screenings require correction=true to cancel")
        replacement = current.model_copy(update={"state": ScreeningState.CANCELLED})
        return (
            _replace_item(screenings, index, replacement),  # type: ignore[arg-type]
            current.state == ScreeningState.OCCURRED,
        )

    def _verify_opportunity_fact(
        self,
        opportunities: tuple[CampaignOpportunity, ...],
        festival_id: str,
        verification_item_id: str,
        result: Fact[object],
    ) -> tuple[CampaignOpportunity, ...]:
        index, current = self._opportunity(opportunities, festival_id)
        items = list(current.verification_items)
        for item_index, item in enumerate(items):
            if item.item_id != verification_item_id:
                continue
            items[item_index] = VerificationItem(
                item_id=item.item_id,
                fact_key=item.fact_key,
                status=result.status,
                blocking=result.status in {
                    FactStatus.UNKNOWN,
                    FactStatus.CONTRADICTED,
                },
                source_refs=result.source_refs,
            )
            return _replace_item(
                opportunities,
                index,
                current.model_copy(update={"verification_items": tuple(items)}),
            )  # type: ignore[return-value]
        raise InvalidTransition(f"unknown verification item: {verification_item_id}")

    def _correct_record(
        self,
        profile: CampaignProfile,
        constraints: tuple[CampaignConstraint, ...],
        opportunities: tuple[CampaignOpportunity, ...],
        screenings: tuple[ScreeningSnapshot, ...],
        prior_event: CampaignEvent,
        replacement: Fact[object],
        correction_event_id: str,
    ) -> tuple[
        CampaignProfile,
        tuple[CampaignConstraint, ...],
        tuple[CampaignOpportunity, ...],
        tuple[ScreeningSnapshot, ...],
        bool,
    ]:
        prior_type = prior_event.command.type
        prior_payload = prior_event.command.payload
        if prior_type == CampaignCommandType.UPDATE_PROFILE_FACT:
            profile = self._correct_profile_fact(
                profile,
                prior_payload.fact_key,
                prior_payload.fact,
                replacement,
            )
            return profile, constraints, opportunities, screenings, True
        if prior_type in {
            CampaignCommandType.SCHEDULE_SCREENING,
            CampaignCommandType.CONFIRM_SCREENING,
        }:
            index, current = self._screening(screenings, prior_payload.screening_id)
            if replacement.status in {FactStatus.UNKNOWN, FactStatus.CONTRADICTED}:
                access = ScreeningAccess.UNKNOWN
            else:
                try:
                    access = ScreeningAccess(str(replacement.value))
                except ValueError as exc:
                    raise InvalidTransition(
                        "screening corrections currently require public/private/unknown access"
                    ) from exc
            corrected = current.model_copy(
                update={
                    "access": access,
                    "source_refs": tuple(
                        dict.fromkeys(
                            (*replacement.source_refs, f"correction:{correction_event_id}")
                        )
                    ),
                }
            )
            screenings = _replace_item(screenings, index, corrected)  # type: ignore[assignment]
            return profile, constraints, opportunities, screenings, True
        if prior_type == CampaignCommandType.VERIFY_OPPORTUNITY_FACT:
            opportunities = self._verify_opportunity_fact(
                opportunities,
                prior_payload.festival_id,
                prior_payload.verification_item_id,
                replacement,
            )
            return profile, constraints, opportunities, screenings, False
        if prior_type == CampaignCommandType.SET_CONSTRAINT:
            constraint_id = prior_payload.constraint.constraint_id
            for index, current in enumerate(constraints):
                if current.constraint_id == constraint_id:
                    if current.locked:
                        raise InvalidTransition(
                            "locked constraint corrections require explicit unlock/removal"
                        )
                    corrected_payload = current.model_dump(mode="python")
                    corrected_payload["value"] = replacement.value
                    corrected = CampaignConstraint.model_validate(corrected_payload)
                    constraints = _replace_item(constraints, index, corrected)  # type: ignore[assignment]
                    return profile, constraints, opportunities, screenings, False
            raise InvalidTransition(f"constraint not found for correction: {constraint_id}")
        if hasattr(prior_payload, "festival_id"):
            index, current = self._opportunity(
                opportunities, prior_payload.festival_id
            )
            if replacement.value is None:
                raise InvalidTransition("operational corrections require a concrete state")
            value = str(replacement.value)
            update: dict[str, object]
            try:
                update = {"policy_state": OpportunityPolicyState(value)}
            except ValueError:
                try:
                    update = {"submission_state": SubmissionState(value)}
                except ValueError:
                    try:
                        update = {"offer_state": OfferState(value)}
                    except ValueError as exc:
                        raise InvalidTransition(
                            "unsupported corrected opportunity state"
                        ) from exc
            opportunities = _replace_item(
                opportunities, index, current.model_copy(update=update)
            )  # type: ignore[assignment]
            return profile, constraints, opportunities, screenings, False
        raise InvalidTransition(
            f"correction target is not supported for {prior_type.value}"
        )

    @staticmethod
    def _correct_profile_fact(
        profile: CampaignProfile,
        fact_key: ProfileFactKey,
        prior_fact: Fact[object],
        replacement: Fact[object],
    ) -> CampaignProfile:
        if fact_key not in {
            ProfileFactKey.PREMIERE_ASSERTION,
            ProfileFactKey.TARGET_REGION,
        }:
            return _replace_profile_fact(profile, fact_key, replacement)

        payload = profile.model_dump(mode="python")
        if fact_key == ProfileFactKey.PREMIERE_ASSERTION:
            prior_payload = prior_fact.model_dump(mode="python")
            assertions = list(payload["premiere_assertions"])
            for index in range(len(assertions) - 1, -1, -1):
                if assertions[index] == prior_payload:
                    assertions[index] = replacement.model_dump(mode="python")
                    payload["premiere_assertions"] = assertions
                    break
            else:
                raise InvalidTransition("corrected premiere assertion is no longer current")
        else:
            if not isinstance(prior_fact.value, str) or not isinstance(
                replacement.value, str
            ):
                raise InvalidTransition("target_region corrections require string values")
            regions = list(payload["target_regions"])
            try:
                index = regions.index(prior_fact.value)
            except ValueError as exc:
                raise InvalidTransition(
                    "corrected target region is no longer current"
                ) from exc
            regions[index] = replacement.value
            payload["target_regions"] = sorted(set(regions), key=str.casefold)
        payload["profile_hash"] = "0" * 64
        return _profile_with_hash(CampaignProfile.model_validate(payload))


__all__ = [
    "CampaignStateError",
    "CampaignStateReducer",
    "InvalidTransition",
    "StateReduction",
    "VersionConflict",
]
