"""Capability-scoped routes mounted by the sole Vercel FastAPI entry point."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, TypeVar
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, Depends, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from app.campaign.auth import digest_capability, generate_capability
from app.campaign.contracts import parse_campaign_command
from app.campaign.models import (
    ApiError,
    BootstrapResponse,
    CampaignCreationRequest,
    ReplanRequest,
    SimulateRequest,
)
from app.campaign.repository import (
    CampaignNotFound,
    CampaignRepositoryError,
    IdempotencyConflict,
    SupabaseCampaignRepository,
    WorkspaceNotFound,
)
from app.campaign.scenarios import ScenarioError
from app.campaign.service import (
    CampaignService,
    CampaignServiceError,
    StrategyHistoryNotFound,
)
from app.campaign.state import CampaignStateError, InvalidTransition, VersionConflict


ROOT = Path(__file__).resolve().parent.parent
COOKIE_NAME = "distributor_workspace"
_CAPABILITY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
router = APIRouter()
T = TypeVar("T")


def _safe_exception_detail(exc: Exception) -> str:
    """Return bounded diagnostics without allowing configured secrets into logs."""

    detail = str(exc)
    for variable in (
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_ANON_KEY",
        "LLM_API_KEY",
        "PINECONE_API_KEY",
    ):
        secret = os.getenv(variable)
        if secret:
            detail = detail.replace(secret, "[redacted]")
    return detail.replace("\r", " ").replace("\n", " ")[:500]


class CampaignApiException(Exception):
    """A stable, deliberately non-sensitive workspace API error."""

    def __init__(self, status_code: int, error: ApiError) -> None:
        self.status_code = status_code
        self.error = error
        super().__init__(error.code)


def campaign_api_exception_response(exc: CampaignApiException) -> JSONResponse:
    return JSONResponse(
        exc.error.model_dump(mode="json"), status_code=exc.status_code
    )


def campaign_validation_error_response(exc: Exception) -> JSONResponse:
    details: list[str] = []
    errors = getattr(exc, "errors", lambda: ())()
    for item in errors[:12]:
        location = ".".join(
            str(part) for part in item.get("loc", ()) if part not in {"body"}
        )
        message = str(item.get("msg") or "invalid value")
        details.append(f"{location}: {message}" if location else message)
    error = ApiError(
        code="validation_error",
        message="The workspace request payload is invalid.",
        details=tuple(details),
    )
    return JSONResponse(error.model_dump(mode="json"), status_code=422)


_service_override: CampaignService | None = None
_origin_override: tuple[str, ...] | None = None


def configure_campaign_runtime_for_tests(
    service: CampaignService | None,
    *,
    allowed_origins: tuple[str, ...] | None = None,
) -> None:
    """Install or clear an isolated in-memory runtime for deterministic API tests."""

    global _service_override, _origin_override
    _service_override = service
    _origin_override = allowed_origins


@lru_cache(maxsize=1)
def _production_service() -> CampaignService:
    return CampaignService(SupabaseCampaignRepository.from_environment())


def _service() -> CampaignService:
    if _service_override is not None:
        return _service_override
    try:
        return _production_service()
    except Exception as exc:  # noqa: BLE001 - fail closed without leaking config
        raise CampaignApiException(
            503,
            ApiError(
                code="campaign_service_unavailable",
                message="Campaign Workspace persistence is not configured.",
            ),
        ) from exc


def _configured_origins() -> tuple[str, ...]:
    if _origin_override is not None:
        return _origin_override
    explicit = tuple(
        item.strip()
        for item in os.getenv("CAMPAIGN_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    )
    if explicit:
        origins = list(explicit)
    else:
        origins = ["http://localhost:8000", "http://127.0.0.1:8000"]
        for variable in ("VERCEL_PROJECT_PRODUCTION_URL", "VERCEL_URL"):
            host = (os.getenv(variable) or "").strip().strip("/")
            if host:
                origins.append(f"https://{host}")
    valid: list[str] = []
    for origin in dict.fromkeys(origins):
        parsed = urlsplit(origin)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and "*" not in origin
            and origin != "null"
        ):
            valid.append(origin)
    return tuple(valid)


def _mutation_guard(request: Request) -> None:
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise CampaignApiException(
            415,
            ApiError(
                code="json_required",
                message="Workspace mutations accept application/json only.",
            ),
        )
    origin = request.headers.get("origin")
    if not origin or origin == "null" or origin not in _configured_origins():
        raise CampaignApiException(
            403,
            ApiError(
                code="origin_forbidden",
                message="The request Origin is not allowed for workspace mutations.",
            ),
        )


def _workspace(request: Request, service: CampaignService = Depends(_service)) -> str:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw or not _CAPABILITY_PATTERN.fullmatch(raw):
        raise CampaignApiException(
            401,
            ApiError(
                code="workspace_capability_required",
                message="A valid private workspace capability is required.",
            ),
        )
    try:
        return service.repository.resolve_workspace(digest_capability(raw))
    except WorkspaceNotFound as exc:
        raise CampaignApiException(
            401,
            ApiError(
                code="workspace_capability_invalid",
                message="The private workspace capability is not recognized.",
            ),
        ) from exc


def _translate(exc: Exception) -> CampaignApiException:
    if isinstance(exc, VersionConflict):
        return CampaignApiException(
            409,
            ApiError(
                code="version_conflict",
                message="Campaign version is stale.",
                current_version=exc.current_version,
            ),
        )
    if isinstance(exc, IdempotencyConflict):
        return CampaignApiException(
            409,
            ApiError(
                code="idempotency_conflict",
                message="The idempotency key was already used for another command.",
            ),
        )
    if isinstance(exc, (CampaignNotFound, StrategyHistoryNotFound)):
        return CampaignApiException(
            404,
            ApiError(code="campaign_not_found", message="Campaign resource not found."),
        )
    if isinstance(exc, (InvalidTransition, ScenarioError, CampaignStateError)):
        return CampaignApiException(
            422,
            ApiError(code="invalid_transition", message=str(exc)),
        )
    if isinstance(exc, CampaignServiceError):
        return CampaignApiException(
            422,
            ApiError(code="campaign_operation_invalid", message=str(exc)),
        )
    if isinstance(exc, ValidationError):
        return CampaignApiException(
            422,
            ApiError(
                code="validation_error",
                message="The workspace request payload is invalid.",
                details=tuple(
                    f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                    for item in exc.errors()[:12]
                ),
            ),
        )
    if isinstance(exc, CampaignRepositoryError):
        return CampaignApiException(
            503,
            ApiError(
                code="campaign_persistence_unavailable",
                message="Campaign persistence is temporarily unavailable.",
            ),
        )
    return CampaignApiException(
        503,
        ApiError(
            code="campaign_operation_failed",
            message="The campaign operation could not be completed.",
        ),
    )


def _call(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except CampaignApiException:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize the public boundary
        print(  # noqa: T201 - Vercel captures stdout, not Python logger records
            "campaign_operation_failed "
            f"error_type={type(exc).__name__} "
            f"detail={_safe_exception_detail(exc)}",
            flush=True,
        )
        raise _translate(exc) from exc


def _parse_command(
    payload: Any, service: CampaignService, workspace_id: str, campaign_id: str
):
    aggregate = _call(
        lambda: service.repository.load_campaign(workspace_id, campaign_id)
    )
    known = {item.festival_id for item in aggregate.snapshot.opportunities}
    try:
        return parse_campaign_command(payload, known)
    except ValidationError as exc:
        raise CampaignApiException(
            422,
            ApiError(
                code="validation_error",
                message="The command payload is invalid.",
                details=tuple(
                    f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                    for item in exc.errors()[:12]
                ),
            ),
        ) from exc


@router.get("/campaign")
def campaign_page() -> FileResponse:
    return FileResponse(ROOT / "public" / "campaign.html", media_type="text/html")


@router.post("/api/workspace/bootstrap", dependencies=[Depends(_mutation_guard)])
def bootstrap(
    request: Request,
    response: Response,
    service: CampaignService = Depends(_service),
) -> BootstrapResponse:
    raw = request.cookies.get(COOKIE_NAME)
    if raw and _CAPABILITY_PATTERN.fullmatch(raw):
        try:
            workspace_id = service.repository.resolve_workspace(digest_capability(raw))
            return BootstrapResponse(workspace_id=workspace_id)
        except WorkspaceNotFound:
            pass
    capability = generate_capability()
    workspace_id = _call(lambda: service.create_workspace(capability.digest()))
    response.set_cookie(
        COOKIE_NAME,
        capability.reveal(),
        secure=True,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return BootstrapResponse(workspace_id=workspace_id)


@router.get("/api/workspace/campaigns")
def list_campaigns(
    service: CampaignService = Depends(_service),
    workspace_id: str = Depends(_workspace),
) -> dict[str, Any]:
    campaigns = _call(lambda: service.list_campaigns(workspace_id))
    return {"campaigns": campaigns}


@router.post("/api/workspace/campaigns", dependencies=[Depends(_mutation_guard)])
def create_campaign(
    payload: CampaignCreationRequest,
    service: CampaignService = Depends(_service),
    workspace_id: str = Depends(_workspace),
) -> dict[str, Any]:
    result = _call(lambda: service.create_campaign(workspace_id, payload))
    return {
        "campaign_id": result.aggregate.snapshot.campaign_id,
        "campaign_version": result.aggregate.snapshot.campaign_version,
        "strategy_status": result.status,
    }


@router.get("/api/workspace/campaigns/{campaign_id}")
def get_campaign(
    campaign_id: str,
    service: CampaignService = Depends(_service),
    workspace_id: str = Depends(_workspace),
) -> dict[str, Any]:
    return _call(lambda: service.get_campaign(workspace_id, campaign_id))


@router.post(
    "/api/workspace/campaigns/{campaign_id}/commands",
    dependencies=[Depends(_mutation_guard)],
)
def apply_command(
    campaign_id: str,
    payload: dict[str, Any] = Body(...),
    service: CampaignService = Depends(_service),
    workspace_id: str = Depends(_workspace),
) -> dict[str, Any]:
    command = _parse_command(payload, service, workspace_id, campaign_id)
    result = _call(lambda: service.apply_command(workspace_id, campaign_id, command))
    return {
        "campaign_id": campaign_id,
        "campaign_version": result.aggregate.snapshot.campaign_version,
        "strategy_status": result.status,
        "idempotent_replay": result.idempotent_replay,
        "cache_miss_reasons": result.cache_miss_reasons,
    }


@router.post(
    "/api/workspace/campaigns/{campaign_id}/replan",
    dependencies=[Depends(_mutation_guard)],
)
def replan(
    campaign_id: str,
    payload: ReplanRequest,
    service: CampaignService = Depends(_service),
    workspace_id: str = Depends(_workspace),
) -> dict[str, Any]:
    result = _call(
        lambda: service.replan(
            workspace_id, campaign_id, expected_version=payload.expected_version
        )
    )
    return {
        "campaign_id": campaign_id,
        "campaign_version": result.aggregate.snapshot.campaign_version,
        "strategy_status": result.status,
        "state_event_created": False,
        "cache_miss_reasons": result.cache_miss_reasons,
    }


@router.post(
    "/api/workspace/campaigns/{campaign_id}/simulate",
    dependencies=[Depends(_mutation_guard)],
)
def simulate(
    campaign_id: str,
    payload: dict[str, Any] = Body(...),
    service: CampaignService = Depends(_service),
    workspace_id: str = Depends(_workspace),
) -> dict[str, Any]:
    aggregate = _call(
        lambda: service.repository.load_campaign(workspace_id, campaign_id)
    )
    known = {item.festival_id for item in aggregate.snapshot.opportunities}
    try:
        request = SimulateRequest.model_validate(
            payload, context={"known_festival_ids": known}
        )
    except ValidationError as exc:
        raise _translate(exc) from exc
    result = _call(lambda: service.simulate(workspace_id, campaign_id, request))
    return {
        "base_campaign_version": result.base_snapshot.campaign_version,
        "hypothetical_snapshot": result.hypothetical_snapshot.model_dump(mode="json"),
        "hypothetical_plan": (
            result.hypothetical_plan.model_dump(mode="json")
            if result.hypothetical_plan
            else None
        ),
        "diff": result.diff.model_dump(mode="json") if result.diff else None,
        "reuse_manifest": (
            result.reuse_manifest.model_dump(mode="json")
            if result.reuse_manifest
            else None
        ),
        "simulated_events": tuple(
            item.model_dump(mode="json") for item in result.simulated_events
        ),
        "mutated_campaign": False,
        "requires_provider_refresh": result.requires_provider_refresh,
        "cache_miss_reasons": result.cache_miss_reasons,
    }


@router.get(
    "/api/workspace/campaigns/{campaign_id}/strategies/{strategy_no}"
)
def strategy_history(
    campaign_id: str,
    strategy_no: int,
    service: CampaignService = Depends(_service),
    workspace_id: str = Depends(_workspace),
) -> dict[str, Any]:
    if strategy_no < 1:
        raise CampaignApiException(
            422,
            ApiError(
                code="validation_error",
                message="strategy_no must be at least 1.",
            ),
        )
    return _call(
        lambda: service.strategy_history(workspace_id, campaign_id, strategy_no)
    )


__all__ = [
    "CampaignApiException",
    "COOKIE_NAME",
    "campaign_api_exception_response",
    "campaign_validation_error_response",
    "configure_campaign_runtime_for_tests",
    "router",
]
