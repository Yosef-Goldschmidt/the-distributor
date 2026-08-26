"""Campaign Workspace API, capability isolation, and product-boundary tests."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api._campaign_routes import COOKIE_NAME, configure_campaign_runtime_for_tests
from api.index import app
from app.campaign.adapter import AdaptedCampaignEvidence, LegacyEvidenceAdapter
from app.campaign.models import (
    CampaignProfile,
    CampaignSnapshot,
    FrozenCandidateEvidence,
    RetrievalInput,
)
from app.campaign.repository import InMemoryCampaignRepository
from app.campaign.service import CampaignService


ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "campaign"
MODELS = json.loads((FIXTURES / "boundary_models.json").read_text())
KNOWN_IDS = frozenset(
    json.loads((FIXTURES / "known_festival_ids.json").read_text())
)
AS_OF = date(2026, 8, 25)
NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
ORIGIN = "https://testserver"


def _adapted_evidence() -> AdaptedCampaignEvidence:
    snapshot = CampaignSnapshot.model_validate(
        MODELS["CampaignSnapshot"], context={"known_festival_ids": KNOWN_IDS}
    )
    return AdaptedCampaignEvidence(
        profile=CampaignProfile.model_validate(MODELS["CampaignProfile"]),
        retrieval_input=RetrievalInput.model_validate(MODELS["RetrievalInput"]),
        candidates=tuple(
            FrozenCandidateEvidence.model_validate(
                item.model_dump(mode="python"),
                context={"known_festival_ids": KNOWN_IDS},
            )
            for item in snapshot.candidates
        ),
        screenings=snapshot.screenings,
        company_memory_summary={
            "company_name": "Meridian Films",
            "festival_relationships": 2,
        },
        trace=({"module": "StaticEvidenceFixture", "provider_call": False},),
        chat_attempts=2,
        embedding_attempts=1,
    )


class _StaticPipeline:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, **_kwargs: Any) -> object:
        self.calls += 1
        return object()


class _StaticAdapter(LegacyEvidenceAdapter):
    def __init__(self, evidence: AdaptedCampaignEvidence) -> None:
        self.evidence = evidence

    def adapt(self, _bundle: object, **kwargs: Any) -> AdaptedCampaignEvidence:
        profile = kwargs.get("authoritative_profile") or self.evidence.profile
        if profile.profile_hash != self.evidence.profile.profile_hash:
            raise AssertionError("this deterministic API fixture does not perform A refresh")
        return self.evidence


@pytest.fixture
def campaign_runtime():
    repository = InMemoryCampaignRepository(clock=lambda: NOW)
    pipeline = _StaticPipeline()
    service = CampaignService(
        repository,
        pipeline=pipeline,
        adapter=_StaticAdapter(_adapted_evidence()),
        as_of_date=lambda: AS_OF,
        now=lambda: NOW,
    )
    configure_campaign_runtime_for_tests(
        service, allowed_origins=(ORIGIN,)
    )
    try:
        yield service, pipeline
    finally:
        configure_campaign_runtime_for_tests(None)


def _client() -> TestClient:
    return TestClient(app, base_url=ORIGIN)


def _bootstrap(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/workspace/bootstrap", json={}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _create(client: TestClient, title: str = "Borrowed Ground") -> str:
    response = client.post(
        "/api/workspace/campaigns",
        json={"free_text": f"{title} is a feature documentary with no prior screening."},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["campaign_version"] == 0
    assert body["strategy_status"] == "ready"
    return body["campaign_id"]


def _command(
    client: TestClient,
    campaign_id: str,
    command_type: str,
    festival_id: str,
    *,
    version: int,
    key: str,
):
    decision = command_type in {
        "mark_submitted",
        "record_rejection",
        "record_invitation",
    }
    payload: dict[str, Any] = {"festival_id": festival_id}
    if decision:
        payload["source_refs"] = [f"human:{key}"]
    if command_type == "record_invitation":
        payload["offer_ref"] = f"offer:{key}"
    return client.post(
        f"/api/workspace/campaigns/{campaign_id}/commands",
        json={
            "type": command_type,
            "payload": payload,
            "expected_version": version,
            "idempotency_key": key,
            "actor": {"kind": "human", "actor_ref": "human:api-test"},
            "invalidation_class": "C",
        },
        headers={"Origin": ORIGIN},
    )


def test_bootstrap_cookie_is_opaque_secure_and_never_returned(
    campaign_runtime,
) -> None:
    service, _pipeline = campaign_runtime
    client = _client()
    body = _bootstrap(client)
    raw = client.cookies.get(COOKIE_NAME)
    assert raw and len(raw) == 43
    assert body == {
        "workspace_id": body["workspace_id"],
        "capability_in_body": False,
    }
    assert raw not in json.dumps(body)
    set_cookie = client.post(
        "/api/workspace/bootstrap", json={}, headers={"Origin": ORIGIN}
    ).headers.get("set-cookie", "")
    # Existing valid capabilities are resolved without unnecessarily rotating them.
    assert set_cookie == ""
    assert service.repository.resolve_workspace(
        __import__("hashlib").sha256(raw.encode("ascii")).hexdigest()
    ) == body["workspace_id"]

    fresh = _client()
    response = fresh.post(
        "/api/workspace/bootstrap", json={}, headers={"Origin": ORIGIN}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_two_capabilities_cannot_read_or_mutate_each_others_campaigns(
    campaign_runtime,
) -> None:
    _service, pipeline = campaign_runtime
    first, second = _client(), _client()
    one, two = _bootstrap(first), _bootstrap(second)
    assert one["workspace_id"] != two["workspace_id"]
    assert first.cookies.get(COOKIE_NAME) != second.cookies.get(COOKIE_NAME)
    first_campaign = _create(first, "First Film")
    second_campaign = _create(second, "Second Film")
    assert pipeline.calls == 2

    assert first.get(f"/api/workspace/campaigns/{first_campaign}").status_code == 200
    assert second.get(f"/api/workspace/campaigns/{second_campaign}").status_code == 200
    denied_read = second.get(f"/api/workspace/campaigns/{first_campaign}")
    assert denied_read.status_code == 404
    assert denied_read.json()["code"] == "campaign_not_found"
    denied_write = _command(
        second,
        first_campaign,
        "mark_submitted",
        "hot-docs",
        version=0,
        key="cross-workspace-command-0001",
    )
    assert denied_write.status_code == 404
    aggregate = first.get(f"/api/workspace/campaigns/{first_campaign}").json()
    assert aggregate["snapshot"]["campaign_version"] == 0
    assert aggregate["events"] == []


@pytest.mark.parametrize(
    ("headers", "expected_status", "expected_code"),
    [
        ({"Content-Type": "application/json"}, 403, "origin_forbidden"),
        (
            {"Origin": "null", "Content-Type": "application/json"},
            403,
            "origin_forbidden",
        ),
        (
            {
                "Origin": "https://evil.example",
                "Content-Type": "application/json",
            },
            403,
            "origin_forbidden",
        ),
        (
            {"Origin": ORIGIN, "Content-Type": "text/plain"},
            415,
            "json_required",
        ),
    ],
)
def test_mutations_require_json_and_an_exact_allowed_origin(
    campaign_runtime, headers, expected_status, expected_code
) -> None:
    client = _client()
    response = client.post(
        "/api/workspace/bootstrap", content="{}", headers=headers
    )
    assert response.status_code == expected_status
    assert response.json() == {
        "code": expected_code,
        "message": response.json()["message"],
        "current_version": None,
        "details": [],
    }
    assert client.cookies.get(COOKIE_NAME) is None


def test_rejection_replans_with_diff_reuse_zero_providers_and_idempotency(
    campaign_runtime,
) -> None:
    _service, pipeline = campaign_runtime
    client = _client()
    _bootstrap(client)
    campaign_id = _create(client)
    assert pipeline.calls == 1
    submitted = _command(
        client,
        campaign_id,
        "mark_submitted",
        "hot-docs",
        version=0,
        key="api-submit-hot-docs-0001",
    )
    assert submitted.status_code == 200, submitted.text
    rejected = _command(
        client,
        campaign_id,
        "record_rejection",
        "hot-docs",
        version=1,
        key="api-reject-hot-docs-0001",
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["campaign_version"] == 2
    assert pipeline.calls == 1

    aggregate = client.get(f"/api/workspace/campaigns/{campaign_id}").json()
    assert aggregate["latest_diff"]["primary_before"] == "hot-docs"
    assert aggregate["latest_diff"]["primary_after"] != "hot-docs"
    reuse = aggregate["latest_diff"]["reuse_summary"]
    assert reuse["invalidation_class"] == "C"
    assert reuse["chat_attempts"] == 0
    assert reuse["embedding_attempts"] == 0
    assert aggregate["view"]["reuse"]["no_new_llm"] is True
    assert aggregate["view"]["reuse"]["evidence_reused"] is True

    replay = _command(
        client,
        campaign_id,
        "record_rejection",
        "hot-docs",
        version=1,
        key="api-reject-hot-docs-0001",
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["campaign_version"] == 2
    assert pipeline.calls == 1
    conflict = _command(
        client,
        campaign_id,
        "record_rejection",
        "idfa",
        version=2,
        key="api-reject-hot-docs-0001",
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"


def test_scenario_returns_diff_and_reuse_without_writing(
    campaign_runtime,
) -> None:
    _service, pipeline = campaign_runtime
    client = _client()
    _bootstrap(client)
    campaign_id = _create(client)
    before = client.get(f"/api/workspace/campaigns/{campaign_id}").json()
    response = client.post(
        f"/api/workspace/campaigns/{campaign_id}/simulate",
        json={
            "commands": [
                {
                    "type": "mark_submitted",
                    "payload": {
                        "festival_id": "hot-docs",
                        "source_refs": ["human:hypothetical-submission"],
                    },
                    "expected_version": 0,
                    "idempotency_key": "scenario-submission-0001",
                    "actor": {"kind": "human", "actor_ref": "human:api-test"},
                    "invalidation_class": "C",
                },
                {
                    "type": "record_rejection",
                    "payload": {
                        "festival_id": "hot-docs",
                        "source_refs": ["human:hypothetical-email"],
                    },
                    "expected_version": 1,
                    "idempotency_key": "scenario-rejection-0001",
                    "actor": {"kind": "human", "actor_ref": "human:api-test"},
                    "invalidation_class": "C",
                }
            ]
        },
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mutated_campaign"] is False
    assert body["requires_provider_refresh"] is False
    assert body["diff"] is not None
    assert body["reuse_manifest"]["chat_attempts"] == 0
    assert body["reuse_manifest"]["embedding_attempts"] == 0
    after = client.get(f"/api/workspace/campaigns/{campaign_id}").json()
    assert after["snapshot"] == before["snapshot"]
    assert after["events"] == before["events"] == []
    assert pipeline.calls == 1


def test_version_validation_history_and_course_contracts_remain_exact(
    campaign_runtime,
) -> None:
    client = _client()
    _bootstrap(client)
    campaign_id = _create(client)
    stale = _command(
        client,
        campaign_id,
        "mark_submitted",
        "hot-docs",
        version=9,
        key="api-stale-command-0001",
    )
    assert stale.status_code == 409
    assert stale.json() == {
        "code": "version_conflict",
        "message": "Campaign version is stale.",
        "current_version": 0,
        "details": [],
    }
    history = client.get(
        f"/api/workspace/campaigns/{campaign_id}/strategies/1"
    )
    assert history.status_code == 200
    assert history.json()["campaign_id"] == campaign_id
    assert history.json()["usage"] == {
        "chat_attempts": 2,
        "embedding_attempts": 1,
    }

    for path in ("/api/team_info", "/api/agent_info"):
        assert client.get(path).status_code == 200
    architecture = client.get("/api/model_architecture")
    assert architecture.status_code == 200
    assert architecture.headers["content-type"].startswith("image/png")
    for payload in ({}, {"prompt": ""}, {"prompt": 7}):
        response = client.post("/api/execute", json=payload)
        assert set(response.json()) == {"status", "error", "response", "steps"}


def test_single_router_and_vercel_function_contract(campaign_runtime) -> None:
    vercel = json.loads((ROOT / "vercel.json").read_text())
    assert list(vercel["functions"]) == ["api/index.py"]
    public_python_entries = sorted(
        path.name
        for path in (ROOT / "api").glob("*.py")
        if not path.name.startswith("_")
    )
    assert public_python_entries == ["index.py"]
    assert vercel["rewrites"] == [
        {"source": "/(.*)", "destination": "/api/index"}
    ]
    paths = {getattr(route, "path", None) for route in app.routes}
    # FastAPI 0.116+ stores included routers lazily; the OpenAPI schema is the
    # authoritative flattened route set in both representations.
    paths |= set(app.openapi()["paths"])
    assert "/api/execute" in paths
    assert "/api/workspace/bootstrap" in paths
    assert "/api/workspace/campaigns/{campaign_id}/simulate" in paths


def test_campaign_page_is_compact_server_authoritative_and_quick_root_survives(
    campaign_runtime,
) -> None:
    client = _client()
    campaign_page = client.get("/campaign")
    assert campaign_page.status_code == 200
    html = campaign_page.text
    for required in (
        "Primary launch",
        "Up to two alternatives",
        "Premiere-option preservation",
        "Verification gates",
        "Highest-priority clarification",
        "No new LLM or embeddings",
        "Latest strategy change",
        "What-if",
        "Public screening",
        "Strict preservation",
    ):
        assert required in html
    assert "innerHTML" not in html
    assert "localStorage" not in html
    assert "compatibility_edges" not in html
    assert "premiere_ledger" not in html
    assert "supabase" not in html.lower()
    assert "pinecone" not in html.lower()

    root = client.get("/")
    assert root.status_code == 200
    assert 'id="prompt"' in root.text
    assert 'id="run"' in root.text
    assert "Run Agent" in root.text
    assert 'href="/campaign"' in root.text
