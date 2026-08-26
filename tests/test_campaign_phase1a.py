"""Focused deterministic tests for Campaign Workspace Phase 1A."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.campaign.auth import (
    OpaqueCapability,
    capability_digests_equal,
    digest_capability,
    generate_capability,
)
from app.campaign.contracts import (
    campaign_snapshot_hash,
    parse_campaign_command,
)
from app.campaign.models import (
    CampaignLifecycle,
    CampaignPlan,
    CampaignSnapshot,
    Fact,
    FactStatus,
    OpportunityPolicyState,
    PremiereAvailability,
    PremiereScope,
    ScreeningAccess,
    ScreeningSnapshot,
    ScreeningState,
    VerificationItem,
)
from app.campaign.premiere import PremiereLedger, TrackedPremiereScope
from app.campaign.repository import (
    CampaignNotFound,
    IdempotencyConflict,
    InMemoryCampaignRepository,
    StrategyActivationConflict,
    StrategyAttempt,
    SupabaseCampaignRepository,
)
from app.campaign.state import InvalidTransition, VersionConflict


ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "campaign"
MIGRATION = ROOT / "scripts" / "migrations" / "20260825_campaign_workspace_phase_1a.sql"
KNOWN_IDS = frozenset(json.loads((FIXTURES / "known_festival_ids.json").read_text()))
BOUNDARY_MODELS = json.loads((FIXTURES / "boundary_models.json").read_text())
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc)


def _snapshot() -> CampaignSnapshot:
    return CampaignSnapshot.model_validate(
        BOUNDARY_MODELS["CampaignSnapshot"],
        context={"known_festival_ids": KNOWN_IDS},
    )


def _rehash(snapshot: CampaignSnapshot, **updates: Any) -> CampaignSnapshot:
    provisional = snapshot.model_copy(update={**updates, "aggregate_hash": "0" * 64})
    return provisional.model_copy(
        update={"aggregate_hash": campaign_snapshot_hash(provisional)}
    )


def _command(
    command_type: str,
    payload: dict[str, Any],
    *,
    version: int = 3,
    key: str | None = None,
    invalidation: str | None = None,
):
    if invalidation is None:
        invalidation = (
            "A"
            if command_type == "update_profile_fact"
            else "B"
            if command_type in {"confirm_screening", "verify_opportunity_fact", "correct_record"}
            else "C"
        )
    return parse_campaign_command(
        {
            "type": command_type,
            "payload": payload,
            "expected_version": version,
            "idempotency_key": key or f"phase1a-{command_type}-0001",
            "actor": {"kind": "human", "actor_ref": "human:phase1a-test"},
            "invalidation_class": invalidation,
        },
        KNOWN_IDS,
    )


def _repository(
    *, before_commit=None, snapshot: CampaignSnapshot | None = None
) -> InMemoryCampaignRepository:
    repo = InMemoryCampaignRepository(clock=lambda: NOW, before_commit=before_commit)
    repo.register_workspace("workspace-golden", "a" * 64)
    repo.register_workspace("workspace-other", "b" * 64)
    repo.save_campaign("workspace-golden", snapshot or _snapshot())
    return repo


def _scope(ledger, scope: PremiereScope, territory: str | None = None):
    return next(
        item
        for item in ledger.scopes
        if item.scope == scope and item.territory == territory
    )


def _screening(
    screening_id: str,
    *,
    state: ScreeningState = ScreeningState.OCCURRED,
    access: ScreeningAccess = ScreeningAccess.PUBLIC,
    country: str | None = "Canada",
    region: str | None = None,
    occurred_at: datetime | None = LATER,
    source_refs: tuple[str, ...] = ("evidence:screening",),
    festival_id: str | None = "hot-docs",
) -> ScreeningSnapshot:
    return ScreeningSnapshot(
        screening_id=screening_id,
        festival_id=festival_id,
        state=state,
        access=access,
        country=country,
        region=region,
        scheduled_at=LATER if state == ScreeningState.SCHEDULED else None,
        occurred_at=occurred_at if state == ScreeningState.OCCURRED else None,
        source_refs=source_refs,
    )


def _profile_without_assertion():
    return _snapshot().profile.model_copy(update={"premiere_assertions": ()})


def test_additive_migration_defines_eight_tables_initial_creation_and_atomic_state_rpcs() -> None:
    sql = MIGRATION.read_text()
    tables = re.findall(r"^create table if not exists (\w+)", sql, flags=re.MULTILINE)
    assert tables == [
        "workspaces",
        "film_projects",
        "campaigns",
        "campaign_constraints",
        "campaign_events",
        "campaign_opportunities",
        "screenings",
        "strategy_versions",
    ]
    assert re.search(r"\bdrop\b", sql, flags=re.IGNORECASE) is None
    assert "create or replace function apply_campaign_command" in sql
    assert "create or replace function activate_campaign_strategy" in sql
    assert "create or replace function create_campaign_from_snapshot" in sql
    assert "invalid_initial_campaign_snapshot" in sql
    assert "create or replace function campaign_canonical_json" in sql
    assert "create or replace function campaign_utc_text" in sql
    assert sql.count("campaign_canonical_json(") >= 4
    assert "jsonb_set(v_snapshot, '{aggregate_hash}'" in sql
    command_rpc = sql.index("create or replace function apply_campaign_command")
    activation_rpc = sql.index("create or replace function activate_campaign_strategy")
    assert command_rpc < activation_rpc
    assert sql.index("insert into campaign_events", command_rpc) < sql.index(
        "if v_type = 'update_profile_fact'", command_rpc
    )
    for guarantee in (
        "for update",
        "Workspace scope check",
        "Expected-version check",
        "Idempotency check",
        "rederive_campaign_premiere_ledger",
        "version = version + 1",
        "strategy_stale = true",
        "get_campaign_aggregate",
    ):
        assert guarantee in sql
    assert "campaign_events_append_only" in sql
    assert "strategy_versions_immutable" in sql
    assert sql.count("enable row level security") == 8


def test_capability_generation_digest_comparison_and_repr_redaction() -> None:
    entropy = bytes(range(32))
    with patch("app.campaign.contracts.secrets.token_bytes", return_value=entropy) as rng:
        capability = generate_capability()
    rng.assert_called_once_with(32)
    raw = capability.reveal()
    assert isinstance(capability, OpaqueCapability)
    assert len(raw) == 43
    assert set(raw) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    expected = hashlib.sha256(raw.encode("ascii")).hexdigest()
    assert capability.digest() == digest_capability(capability) == expected
    assert capability_digests_equal(expected, expected)
    assert not capability_digests_equal(expected, "0" * 64)
    assert capability == OpaqueCapability(raw)
    assert raw not in repr(capability)
    assert raw not in str(capability)
    assert "redacted" in repr(capability).lower()


def test_empty_history_is_unknown_but_sourced_unscreened_is_available() -> None:
    engine = PremiereLedger()
    empty = engine.derive(_profile_without_assertion(), ())
    asserted = engine.derive(_snapshot().profile, ())
    assert _scope(empty, PremiereScope.WORLD).availability == PremiereAvailability.UNKNOWN
    assert _scope(empty, PremiereScope.WORLD).reason_code == "absence_is_not_availability"
    assert _scope(asserted, PremiereScope.WORLD).availability == PremiereAvailability.AVAILABLE
    assert _scope(asserted, PremiereScope.WORLD).evidence_refs == ("fixture:source",)
    assert engine.derive(_snapshot().profile, ()) == asserted


@pytest.mark.parametrize(
    ("state", "access", "expected_reason"),
    [
        (ScreeningState.SCHEDULED, ScreeningAccess.PUBLIC, "not_yet_occurred"),
        (ScreeningState.CANCELLED, ScreeningAccess.PUBLIC, "cancelled_screening"),
        (ScreeningState.OCCURRED, ScreeningAccess.PRIVATE, "private_screening"),
    ],
)
def test_scheduled_cancelled_and_private_screenings_do_not_consume(
    state: ScreeningState, access: ScreeningAccess, expected_reason: str
) -> None:
    screening = _screening("screening-1", state=state, access=access)
    ledger = PremiereLedger().derive(_snapshot().profile, (screening,))
    world = _scope(ledger, PremiereScope.WORLD)
    assert world.availability == PremiereAvailability.AVAILABLE
    assert world.reason_code == expected_reason


def test_public_screening_consumes_world_and_only_supported_international_scope() -> None:
    engine = PremiereLedger()
    foreign = engine.derive(
        _profile_without_assertion(),
        (_screening("foreign", country="Canada", source_refs=("proof:foreign",)),),
    )
    assert _scope(foreign, PremiereScope.WORLD).availability == PremiereAvailability.CONSUMED
    assert _scope(foreign, PremiereScope.WORLD).evidence_refs == ("proof:foreign",)
    assert (
        _scope(foreign, PremiereScope.INTERNATIONAL).availability
        == PremiereAvailability.CONSUMED
    )

    domestic = engine.derive(
        _snapshot().profile,
        (_screening("domestic", country="Israel", source_refs=("proof:domestic",)),),
    )
    assert _scope(domestic, PremiereScope.WORLD).availability == PremiereAvailability.CONSUMED
    international = _scope(domestic, PremiereScope.INTERNATIONAL)
    assert international.availability == PremiereAvailability.AVAILABLE
    assert international.reason_code == "domestic_public_only"


def test_unknown_access_film_country_and_screening_country_remain_unknown() -> None:
    engine = PremiereLedger()
    unknown_access = engine.derive(
        _snapshot().profile,
        (
            _screening(
                "unknown-access",
                access=ScreeningAccess.UNKNOWN,
                source_refs=("proof:ambiguous-access",),
            ),
        ),
    )
    assert _scope(unknown_access, PremiereScope.WORLD).availability == PremiereAvailability.UNKNOWN

    unknown_screening_country = engine.derive(
        _profile_without_assertion(), (_screening("unknown-country", country=None),)
    )
    assert (
        _scope(unknown_screening_country, PremiereScope.WORLD).availability
        == PremiereAvailability.CONSUMED
    )
    assert (
        _scope(unknown_screening_country, PremiereScope.INTERNATIONAL).availability
        == PremiereAvailability.UNKNOWN
    )

    unknown_country_fact = Fact[str](
        status=FactStatus.UNKNOWN,
        source_refs=(),
        observed_at=NOW,
    )
    unknown_film_country = _profile_without_assertion().model_copy(
        update={"country": unknown_country_fact}
    )
    ledger = engine.derive(unknown_film_country, (_screening("known-location"),))
    assert _scope(ledger, PremiereScope.INTERNATIONAL).availability == PremiereAvailability.UNKNOWN


def test_territorial_and_continental_consumption_is_directional_and_conservative() -> None:
    engine = PremiereLedger()
    ledger = engine.derive(
        _snapshot().profile,
        (
            _screening(
                "spain-public",
                country="Spain",
                region="Southern Europe",
                source_refs=("proof:spain",),
            ),
        ),
        tracked_scopes=(
            TrackedPremiereScope(PremiereScope.TERRITORIAL, "Spain"),
            TrackedPremiereScope(PremiereScope.TERRITORIAL, "Canada"),
            TrackedPremiereScope(PremiereScope.CONTINENTAL, "Europe"),
            TrackedPremiereScope(PremiereScope.CONTINENTAL, "North America"),
        ),
    )
    assert (
        _scope(ledger, PremiereScope.TERRITORIAL, "Spain").availability
        == PremiereAvailability.CONSUMED
    )
    assert (
        _scope(ledger, PremiereScope.CONTINENTAL, "Europe").availability
        == PremiereAvailability.CONSUMED
    )
    assert (
        _scope(ledger, PremiereScope.TERRITORIAL, "Canada").availability
        == PremiereAvailability.AVAILABLE
    )
    assert (
        _scope(ledger, PremiereScope.CONTINENTAL, "North America").availability
        == PremiereAvailability.AVAILABLE
    )

    unsupported = engine.derive(
        _profile_without_assertion(),
        (_screening("unsupported", country="Morocco", region=None),),
        tracked_scopes=(
            TrackedPremiereScope(PremiereScope.CONTINENTAL, "Africa"),
        ),
    )
    assert (
        _scope(unsupported, PremiereScope.CONTINENTAL, "Africa").availability
        == PremiereAvailability.UNKNOWN
    )


def test_multiple_screenings_same_festival_are_distinct_and_earliest_public_is_evidence() -> None:
    screenings = (
        _screening(
            "private-show",
            access=ScreeningAccess.PRIVATE,
            source_refs=("proof:private",),
        ),
        _screening(
            "public-late",
            occurred_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
            source_refs=("proof:late",),
        ),
        _screening(
            "public-first",
            occurred_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            source_refs=("proof:first",),
        ),
    )
    ledger = PremiereLedger().derive(_profile_without_assertion(), screenings)
    world = _scope(ledger, PremiereScope.WORLD)
    assert world.availability == PremiereAvailability.CONSUMED
    assert world.evidence_refs == ("proof:first",)


def test_conflicting_current_screening_evidence_is_explicit_contradiction() -> None:
    public = _screening("same-record", source_refs=("proof:public",))
    private = _screening(
        "same-record",
        access=ScreeningAccess.PRIVATE,
        source_refs=("proof:private",),
    )
    ledger = PremiereLedger().derive(_snapshot().profile, (public, private))
    world = _scope(ledger, PremiereScope.WORLD)
    assert world.availability == PremiereAvailability.UNKNOWN
    assert world.contradiction
    assert set(world.evidence_refs) == {"proof:public", "proof:private", "fixture:source"}


def test_contradicted_unscreened_assertion_retains_both_evidence_refs() -> None:
    contradicted = Fact[str](
        status=FactStatus.CONTRADICTED,
        source_refs=("source:claims-unscreened", "source:claims-screened"),
        observed_at=NOW,
    )
    profile = _profile_without_assertion().model_copy(
        update={"premiere_assertions": (contradicted,)}
    )
    world = _scope(PremiereLedger().derive(profile, ()), PremiereScope.WORLD)
    assert world.availability == PremiereAvailability.UNKNOWN
    assert world.contradiction
    assert world.evidence_refs == (
        "source:claims-unscreened",
        "source:claims-screened",
    )


def test_company_memory_cannot_enter_or_change_this_films_ledger() -> None:
    signature = inspect.signature(PremiereLedger.derive)
    assert "company_memory" not in signature.parameters
    baseline = PremiereLedger().derive(_profile_without_assertion(), ())
    unrelated_company_memory = {
        "film": "Another Film",
        "festival": "hot-docs",
        "screened": True,
    }
    assert unrelated_company_memory["screened"] is True
    assert PremiereLedger().derive(_profile_without_assertion(), ()) == baseline


def test_repository_save_load_and_workspace_isolation() -> None:
    repo = _repository()
    loaded = repo.load_campaign("workspace-golden", "campaign-golden")
    assert loaded.snapshot == _snapshot()
    assert loaded.events == ()
    assert loaded.strategy_versions == ()
    assert repo.resolve_workspace("a" * 64) == "workspace-golden"
    with pytest.raises(CampaignNotFound):
        repo.load_campaign("workspace-other", "campaign-golden")


def test_idempotency_returns_one_event_and_version_and_rejects_key_reuse() -> None:
    repo = _repository()
    command = _command(
        "lock_opportunity", {"festival_id": "hot-docs"}, key="idempotency-lock-0001"
    )
    first = repo.apply_command("workspace-golden", "campaign-golden", command)
    replay = repo.apply_command("workspace-golden", "campaign-golden", command)
    assert first.aggregate.snapshot.campaign_version == 4
    assert replay.aggregate.snapshot == first.aggregate.snapshot
    assert replay.idempotent_replay
    assert len(repo.load_campaign("workspace-golden", "campaign-golden").events) == 1

    conflicting = _command(
        "lock_opportunity",
        {"festival_id": "idfa"},
        key="idempotency-lock-0001",
    )
    with pytest.raises(IdempotencyConflict):
        repo.apply_command("workspace-golden", "campaign-golden", conflicting)


def test_expected_version_and_concurrent_stale_writer_create_no_partial_state() -> None:
    repo = _repository()
    stale = _command(
        "lock_opportunity",
        {"festival_id": "hot-docs"},
        version=2,
        key="stale-lock-0001",
    )
    with pytest.raises(VersionConflict):
        repo.apply_command("workspace-golden", "campaign-golden", stale)
    assert repo.load_campaign("workspace-golden", "campaign-golden").events == ()

    commands = (
        _command(
            "lock_opportunity",
            {"festival_id": "hot-docs"},
            key="race-lock-0001",
        ),
        _command(
            "exclude_opportunity",
            {"festival_id": "idfa"},
            key="race-exclude-0001",
        ),
    )

    def apply(command):
        try:
            return repo.apply_command("workspace-golden", "campaign-golden", command)
        except VersionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, commands))
    assert sum(not isinstance(item, VersionConflict) for item in results) == 1
    aggregate = repo.load_campaign("workspace-golden", "campaign-golden")
    assert aggregate.snapshot.campaign_version == 4
    assert len(aggregate.events) == 1


def test_transaction_failure_rolls_back_event_and_projection_together() -> None:
    def fail_before_commit(_reduction):
        raise RuntimeError("simulated transaction failure")

    repo = _repository(before_commit=fail_before_commit)
    before = repo.load_campaign("workspace-golden", "campaign-golden")
    with pytest.raises(RuntimeError, match="simulated transaction failure"):
        repo.apply_command(
            "workspace-golden",
            "campaign-golden",
            _command("lock_opportunity", {"festival_id": "hot-docs"}),
        )
    after = repo.load_campaign("workspace-golden", "campaign-golden")
    assert after == before


def test_append_only_event_ordering_and_hash_chain() -> None:
    repo = _repository()
    commands = (
        _command(
            "lock_opportunity",
            {"festival_id": "hot-docs"},
            version=3,
            key="ordered-lock-0001",
        ),
        _command(
            "unlock_opportunity",
            {"festival_id": "hot-docs"},
            version=4,
            key="ordered-unlock-0001",
        ),
        _command(
            "exclude_opportunity",
            {"festival_id": "hot-docs"},
            version=5,
            key="ordered-exclude-0001",
        ),
    )
    for command in commands:
        repo.apply_command("workspace-golden", "campaign-golden", command)
    events = repo.load_campaign("workspace-golden", "campaign-golden").events
    assert [event.sequence_no for event in events] == [1, 2, 3]
    assert [event.event_id for event in events] == [
        "event:campaign-golden:000001",
        "event:campaign-golden:000002",
        "event:campaign-golden:000003",
    ]
    assert events[0].after_aggregate_hash == events[1].before_aggregate_hash
    assert events[1].after_aggregate_hash == events[2].before_aggregate_hash


def test_locks_require_explicit_human_unlock_for_opportunities_and_constraints() -> None:
    repo = _repository()
    locked = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command("lock_opportunity", {"festival_id": "hot-docs"}),
    )
    assert (
        locked.aggregate.snapshot.opportunities[0].policy_state
        == OpportunityPolicyState.LOCKED
    )
    with pytest.raises(InvalidTransition):
        repo.apply_command(
            "workspace-golden",
            "campaign-golden",
            _command(
                "exclude_opportunity",
                {"festival_id": "hot-docs"},
                version=4,
                key="locked-exclude-0001",
            ),
        )
    assert len(repo.load_campaign("workspace-golden", "campaign-golden").events) == 1
    repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "unlock_opportunity",
            {"festival_id": "hot-docs"},
            version=4,
            key="explicit-unlock-0001",
        ),
    )

    fresh = _repository()
    constraint = {
        "constraint_id": "preserve-world",
        "constraint_type": "preserve_world_premiere",
        "strength": "hard",
        "value": True,
        "locked": True,
        "active": True,
        "candidate_expanding": False,
        "source_ref": "human:policy",
    }
    fresh.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command("set_constraint", {"constraint": constraint}),
    )
    with pytest.raises(InvalidTransition):
        fresh.apply_command(
            "workspace-golden",
            "campaign-golden",
            _command(
                "remove_constraint",
                {"constraint_id": "preserve-world", "explicit_unlock": False},
                version=4,
                key="remove-locked-0001",
            ),
        )
    removed = fresh.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "remove_constraint",
            {"constraint_id": "preserve-world", "explicit_unlock": True},
            version=4,
            key="remove-unlocked-0001",
        ),
    )
    assert not removed.aggregate.snapshot.constraints[0].active


def test_rejection_invitation_acceptance_and_scheduling_never_consume_premiere() -> None:
    repo = _repository()
    initial_ledger = repo.load_campaign(
        "workspace-golden", "campaign-golden"
    ).snapshot.premiere_ledger
    rejected = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command("record_rejection", {"festival_id": "hot-docs", "source_refs": ["email:reject"]}),
    )
    assert rejected.aggregate.snapshot.premiere_ledger == initial_ledger

    invite_repo = _repository()
    invited = invite_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "record_invitation",
            {
                "festival_id": "idfa",
                "source_refs": ["email:invite"],
                "offer_ref": "offer:idfa",
            },
        ),
    )
    accepted = invite_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "accept_offer",
            {"festival_id": "idfa", "source_refs": ["human:accept"]},
            version=4,
            key="accept-idfa-0001",
        ),
    )
    assert invited.aggregate.snapshot.premiere_ledger == initial_ledger
    assert accepted.aggregate.snapshot.premiere_ledger == initial_ledger
    assert accepted.aggregate.snapshot.screenings == ()

    scheduled = invite_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "schedule_screening",
            {
                "screening_id": "idfa-show-1",
                "festival_id": "idfa",
                "country": "Netherlands",
                "scheduled_at": "2026-09-01T19:00:00Z",
                "access": "public",
                "source_refs": ["calendar:idfa"],
            },
            version=5,
            key="schedule-idfa-0001",
        ),
    )
    assert scheduled.aggregate.snapshot.premiere_ledger == initial_ledger

    source_required = _repository()
    with pytest.raises(InvalidTransition, match="source reference"):
        source_required.apply_command(
            "workspace-golden",
            "campaign-golden",
            _command(
                "record_invitation",
                {
                    "festival_id": "idfa",
                    "source_refs": [],
                    "offer_ref": "offer:idfa",
                },
                key="invitation-without-source-0001",
            ),
        )
    assert source_required.load_campaign(
        "workspace-golden", "campaign-golden"
    ).events == ()


def test_public_screening_correction_rederives_ledger_and_retains_history() -> None:
    repo = _repository()
    scheduled = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "schedule_screening",
            {
                "screening_id": "hot-docs-show-1",
                "festival_id": "hot-docs",
                "country": "Canada",
                "scheduled_at": "2026-09-01T19:00:00Z",
                "access": "unknown",
                "source_refs": ["calendar:hot-docs"],
            },
            key="correction-schedule-0001",
        ),
    )
    assert scheduled.aggregate.snapshot.premiere_ledger == _snapshot().premiere_ledger
    confirmed = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "confirm_screening",
            {
                "screening_id": "hot-docs-show-1",
                "occurred_at": "2026-09-01T19:00:00Z",
                "access": "public",
                "country": "Canada",
                "source_refs": ["proof:public"],
            },
            version=4,
            key="correction-confirm-0001",
        ),
    )
    assert (
        _scope(confirmed.aggregate.snapshot.premiere_ledger, PremiereScope.WORLD).availability
        == PremiereAvailability.CONSUMED
    )
    corrected = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "correct_record",
            {
                "prior_ref": confirmed.event.event_id,
                "corrected_domain": "domain_evidence",
                "replacement": {
                    "value": "private",
                    "status": "confirmed",
                    "source_refs": ["proof:access-correction"],
                    "observed_at": "2026-09-02T12:00:00Z",
                },
            },
            version=5,
            key="correction-private-0001",
        ),
    )
    assert corrected.aggregate.snapshot.screenings[0].access == ScreeningAccess.PRIVATE
    world = _scope(corrected.aggregate.snapshot.premiere_ledger, PremiereScope.WORLD)
    assert world.availability == PremiereAvailability.AVAILABLE
    assert world.reason_code == "corrected_access_private"
    assert [event.type.value for event in corrected.aggregate.events] == [
        "screening_scheduled",
        "screening_confirmed",
        "record_corrected",
    ]
    assert corrected.aggregate.events[1] == confirmed.event


def test_profile_correction_replaces_current_fact_without_erasing_prior_event() -> None:
    repo = _repository()
    updated = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "update_profile_fact",
            {
                "fact_key": "premiere_assertion",
                "fact": {
                    "value": "World premiere rights remain available",
                    "status": "asserted",
                    "source_refs": ["human:first-assertion"],
                    "observed_at": "2026-08-25T12:00:00Z",
                },
            },
            key="profile-assertion-0001",
        ),
    )
    corrected = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "correct_record",
            {
                "prior_ref": updated.event.event_id,
                "corrected_domain": "identity",
                "replacement": {
                    "value": "No prior public screening as of today",
                    "status": "confirmed",
                    "source_refs": ["human:corrected-assertion"],
                    "observed_at": "2026-08-25T13:00:00Z",
                },
            },
            version=4,
            key="profile-assertion-correction-0001",
            invalidation="A",
        ),
    )
    assertions = corrected.aggregate.snapshot.profile.premiere_assertions
    assert len(assertions) == 2
    assert assertions[-1].value == "No prior public screening as of today"
    assert [event.type.value for event in corrected.aggregate.events] == [
        "profile_fact_updated",
        "record_corrected",
    ]


def test_identity_update_does_not_implicitly_activate_a_draft_campaign() -> None:
    draft = _rehash(_snapshot(), lifecycle=CampaignLifecycle.DRAFT)
    repo = _repository(snapshot=draft)
    result = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "update_profile_fact",
            {
                "fact_key": "title",
                "fact": {
                    "value": "Salt Road — Working Title",
                    "status": "asserted",
                    "source_refs": ["human:title"],
                    "observed_at": "2026-08-25T12:00:00Z",
                },
            },
            key="draft-title-update-0001",
        ),
    )
    assert result.aggregate.snapshot.lifecycle == CampaignLifecycle.DRAFT


def test_remaining_phase_zero_command_matrix_updates_only_its_projection() -> None:
    # Profile update.
    updated = _repository().apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "update_profile_fact",
            {
                "fact_key": "synopsis",
                "fact": {
                    "value": "A corrected synopsis.",
                    "status": "asserted",
                    "source_refs": ["human:profile"],
                    "observed_at": "2026-08-25T12:00:00Z",
                },
            },
        ),
    )
    assert updated.aggregate.snapshot.profile.synopsis.value == "A corrected synopsis."

    # Include/exclude, submit, withdraw, and direct imported rejection.
    policy_repo = _repository()
    excluded = policy_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command("exclude_opportunity", {"festival_id": "idfa"}),
    )
    assert excluded.aggregate.snapshot.opportunities[1].policy_state.value == "excluded"
    policy_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "include_opportunity",
            {"festival_id": "idfa"},
            version=4,
            key="matrix-include-0001",
        ),
    )
    policy_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "mark_submitted",
            {"festival_id": "idfa", "source_refs": ["human:submit"]},
            version=5,
            key="matrix-submit-0001",
        ),
    )
    withdrawn = policy_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "withdraw",
            {"festival_id": "idfa", "source_refs": ["human:withdraw"]},
            version=6,
            key="matrix-withdraw-0001",
        ),
    )
    assert withdrawn.aggregate.snapshot.opportunities[1].submission_state.value == "withdrawn"

    imported = _repository().apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "record_rejection",
            {"festival_id": "idfa", "source_refs": ["import:external-decision"]},
            key="matrix-import-reject-0001",
        ),
    )
    assert imported.aggregate.snapshot.opportunities[1].submission_state.value == "rejected"

    # Decline a pending offer.
    decline_repo = _repository()
    decline_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "record_invitation",
            {
                "festival_id": "idfa",
                "source_refs": ["email:invite"],
                "offer_ref": "offer:idfa",
            },
            key="matrix-invite-0001",
        ),
    )
    declined = decline_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "decline_offer",
            {"festival_id": "idfa", "source_refs": ["human:decline"]},
            version=4,
            key="matrix-decline-0001",
        ),
    )
    assert declined.aggregate.snapshot.opportunities[1].offer_state.value == "declined"

    # Cancel a scheduled occurrence.
    cancel_repo = _repository()
    cancel_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "schedule_screening",
            {
                "screening_id": "cancel-me",
                "festival_id": "idfa",
                "scheduled_at": "2026-09-01T19:00:00Z",
                "access": "unknown",
                "source_refs": [],
            },
            key="matrix-schedule-0001",
        ),
    )
    cancelled = cancel_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "cancel_screening",
            {"screening_id": "cancel-me", "correction": False},
            version=4,
            key="matrix-cancel-0001",
        ),
    )
    assert cancelled.aggregate.snapshot.screenings[0].state == ScreeningState.CANCELLED

    # Verify a frozen item and close explicitly.
    base = _snapshot()
    opportunity = base.opportunities[1].model_copy(
        update={
            "verification_items": (
                VerificationItem(
                    item_id="verify-deadline",
                    fact_key="deadline",
                    status=FactStatus.UNKNOWN,
                    blocking=True,
                ),
            )
        }
    )
    verify_snapshot = _rehash(
        base,
        opportunities=(base.opportunities[0], opportunity),
    )
    verify_repo = _repository(snapshot=verify_snapshot)
    verified = verify_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "verify_opportunity_fact",
            {
                "festival_id": "idfa",
                "verification_item_id": "verify-deadline",
                "result": {
                    "value": "2026-10-01",
                    "status": "confirmed",
                    "source_refs": ["official:deadline"],
                    "observed_at": "2026-08-25T12:00:00Z",
                },
            },
        ),
    )
    assert not verified.aggregate.snapshot.opportunities[1].verification_items[0].blocking
    closed = verify_repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "close_campaign",
            {"reason_ref": "human:close"},
            version=4,
            key="matrix-close-0001",
        ),
    )
    assert closed.aggregate.snapshot.lifecycle.value == "closed"


def test_strategy_activation_compare_and_set_preserves_prior_active_on_failure_or_race() -> None:
    repo = _repository()
    changed = repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command("lock_opportunity", {"festival_id": "hot-docs"}),
    )
    failed = StrategyAttempt(
        strategy_id="strategy-failed-4",
        based_on_campaign_version=4,
        outcome="failed",
        input_snapshot_json=changed.aggregate.snapshot.model_dump(mode="json"),
        input_hash="c" * 64,
        error_json={"code": "planner_failed"},
    )
    after_failure = repo.record_strategy_attempt(
        "workspace-golden", "campaign-golden", failed
    )
    assert after_failure.snapshot.active_strategy_ref == "strategy-3"
    assert after_failure.strategy_stale

    plan = CampaignPlan.model_validate(
        BOUNDARY_MODELS["CampaignPlan"], context={"known_festival_ids": KNOWN_IDS}
    )
    ready = StrategyAttempt(
        strategy_id="strategy-ready-4",
        based_on_campaign_version=4,
        outcome="ready",
        input_snapshot_json=changed.aggregate.snapshot.model_dump(mode="json"),
        input_hash="d" * 64,
        plan=plan,
    )
    activated = repo.record_strategy_attempt(
        "workspace-golden", "campaign-golden", ready
    )
    assert activated.snapshot.active_strategy_ref == "strategy-ready-4"
    assert not activated.strategy_stale

    repo.apply_command(
        "workspace-golden",
        "campaign-golden",
        _command(
            "unlock_opportunity",
            {"festival_id": "hot-docs"},
            version=4,
            key="cas-race-command-0001",
        ),
    )
    stale_ready = StrategyAttempt(
        strategy_id="strategy-stale-4",
        based_on_campaign_version=4,
        outcome="ready",
        input_snapshot_json=changed.aggregate.snapshot.model_dump(mode="json"),
        input_hash="e" * 64,
        plan=plan,
    )
    with pytest.raises(StrategyActivationConflict):
        repo.record_strategy_attempt(
            "workspace-golden", "campaign-golden", stale_ready
        )
    after_race = repo.load_campaign("workspace-golden", "campaign-golden")
    assert after_race.snapshot.active_strategy_ref == "strategy-ready-4"
    assert after_race.strategy_stale
    assert len(after_race.strategy_versions) == 2


class _RpcCall:
    def __init__(self, response_data: Any) -> None:
        self.response_data = response_data

    def execute(self):
        return type("Response", (), {"data": self.response_data})()


class _FakeSupabaseClient:
    def __init__(self, response_data: Any) -> None:
        self.response_data = response_data
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def rpc(self, name: str, params: dict[str, Any]):
        self.calls.append((name, params))
        return _RpcCall(self.response_data)


class _FailingRpcCall:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def execute(self):
        raise RuntimeError(self.payload)


class _FailingSupabaseClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def rpc(self, _name: str, _params: dict[str, Any]):
        return _FailingRpcCall(self.payload)


def test_supabase_strategy_payload_omits_absent_optional_json_fields() -> None:
    attempt = StrategyAttempt(
        strategy_id="strategy-failed-json-null",
        based_on_campaign_version=3,
        outcome="failed",
        input_snapshot_json={"campaign_id": "campaign-golden"},
        input_hash="f" * 64,
        error_json={"code": "planner_failed"},
    )

    payload = SupabaseCampaignRepository._attempt_payload(attempt)

    assert "plan_json" not in payload
    assert "diff_json" not in payload
    assert payload["error_json"] == {"code": "planner_failed"}


def test_supabase_repository_command_uses_one_rpc_not_application_transactions() -> None:
    local = _repository()
    command = _command("lock_opportunity", {"festival_id": "hot-docs"})
    applied = local.apply_command("workspace-golden", "campaign-golden", command)
    response = {
        "aggregate": {
            "snapshot": applied.aggregate.snapshot.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in applied.aggregate.events],
            "strategy_versions": [],
            "strategy_stale": True,
        },
        "event": applied.event.model_dump(mode="json"),
        "idempotent_replay": False,
    }
    client = _FakeSupabaseClient(response)
    repository = SupabaseCampaignRepository(client)
    result = repository.apply_command("workspace-golden", "campaign-golden", command)
    assert result.aggregate.snapshot == applied.aggregate.snapshot
    assert [name for name, _ in client.calls] == ["apply_campaign_command"]
    assert client.calls[0][1]["p_expected_version"] == 3
    assert client.calls[0][1]["p_workspace_id"] == "workspace-golden"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"message": "campaign_not_found"}, CampaignNotFound),
        ({"message": "idempotency_conflict"}, IdempotencyConflict),
        (
            {"message": "invalid_rejection_transition"},
            InvalidTransition,
        ),
    ],
)
def test_supabase_repository_normalizes_expected_rpc_errors(
    payload: dict[str, Any], expected: type[Exception]
) -> None:
    repository = SupabaseCampaignRepository(_FailingSupabaseClient(payload))
    with pytest.raises(expected):
        repository._rpc("apply_campaign_command", {})


def test_supabase_repository_preserves_current_version_from_rpc_conflict() -> None:
    repository = SupabaseCampaignRepository(
        _FailingSupabaseClient(
            {"message": "version_conflict", "details": "7"}
        )
    )
    with pytest.raises(VersionConflict) as exc_info:
        repository._rpc(
            "apply_campaign_command", {"p_expected_version": 6}
        )
    assert exc_info.value.expected_version == 6
    assert exc_info.value.current_version == 7


def test_supabase_repository_fails_closed_without_service_role_key(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    with pytest.raises(Exception, match="SERVICE_ROLE_KEY"):
        SupabaseCampaignRepository.from_environment()
