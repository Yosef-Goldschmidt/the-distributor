"""FastAPI application — The Distributor.

Exposes the four required course endpoints plus the no-auth GUI at "/".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app import config  # noqa: E402
from app.agent import graph, prompts, scoring  # noqa: E402
from api._campaign_routes import (  # noqa: E402
    CampaignApiException,
    campaign_api_exception_response,
    campaign_validation_error_response,
    router as campaign_router,
)
from app.stores import corpus, supabase_store  # noqa: E402

app = FastAPI(title="The Distributor", description="AI agent for film festival strategy")
app.include_router(campaign_router)


class ExecuteRequest(BaseModel):
    prompt: str = Field(default="")


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Keep malformed request bodies inside the course's exact error contract."""

    if request.url.path.startswith("/api/workspace/"):
        return campaign_validation_error_response(exc)

    return JSONResponse(
        {
            "status": "error",
            "error": "The request body must be JSON with a string 'prompt' field.",
            "response": None,
            "steps": [],
        }
    )


@app.exception_handler(CampaignApiException)
async def campaign_api_error(
    _request: Request, exc: CampaignApiException
) -> JSONResponse:
    return campaign_api_exception_response(exc)


def _load_json(name: str, default: Any) -> Any:
    path = config.DATA_DIR / name
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


# ------------------------------------------------------------------------ GUI
@app.get("/", response_class=HTMLResponse)
def gui() -> HTMLResponse:
    index = config.PUBLIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h1>The Distributor</h1><p>GUI asset missing.</p>", status_code=500)
    return HTMLResponse(index.read_text(encoding="utf-8"))


# ----------------------------------------------------------- /api/team_info
@app.get("/api/team_info")
def team_info() -> JSONResponse:
    return JSONResponse(_load_json("team_info.json", {}))


# ---------------------------------------------------------- /api/agent_info
@app.get("/api/agent_info")
def agent_info() -> JSONResponse:
    examples = _load_json("prompt_examples.json", [])
    module_details = {
        "Planner": {
            "type": "deterministic control",
            "role": "Declares the complete domain evidence chain; required tasks cannot be omitted.",
        },
        "Executor": {
            "type": "orchestrator",
            "role": "Runs the evidence chain in dependency order under a serverless time budget.",
        },
        "FilmAnalyzer": {
            "type": "llm",
            "role": "Extracts supported film facts, premiere history, unknowns and a retrieval query.",
        },
        "CompanyMemory": {
            "type": "retrieval tool",
            "role": "Loads full company history before candidate generation and computes relationship strength.",
        },
        "FestivalSearch": {
            "type": "hybrid retrieval tool",
            "role": "Traces the embedding request, then combines Pinecone semantics, lexical relevance, company memory, Supabase facts and entity deduplication with explicit fallbacks.",
        },
        "RiskChecker": {
            "type": "deterministic domain rules",
            "role": "Validates exact/projected deadlines, format eligibility and premiere scope with confidence labels.",
        },
        "MatchScorer": {
            "type": "llm + deterministic",
            "role": "Rates four creative dimensions; code validates the schema, optionally requests one targeted repair, and adds company/deadline evidence, guardrails, weights and penalties.",
        },
        "RoadmapBuilder": {
            "type": "llm",
            "role": "Selects which supplied evidence to foreground and which facts remain open; code owns narrative, actions and sequencing.",
        },
        "Replanner": {
            "type": "deterministic validator",
            "role": "Checks completeness, uniqueness, evidence references, buckets and premiere-target invariants; revises only the roadmap if needed.",
        },
    }
    module_order = ["Planner", "Executor", *prompts.TASK_CATALOG, "Replanner"]
    return JSONResponse(
        {
            "name": "The Distributor",
            "description": (
                "The Distributor is a plan-and-execute AI agent that builds festival "
                "submission strategies for independent film distribution companies. It "
                "reads a film's identity, retrieves festivals whose programming taste "
                "actually matches it, weighs the company's own history with each festival, "
                "and returns a ranked roadmap with match scores, reasoning and risks."
            ),
            "purpose": (
                "Replace days of manual festival research with a decision-ready submission "
                "strategy: what to submit first, what to delay, what to leverage through "
                "existing relationships, and what to avoid because of premiere or deadline risk."
            ),
            "architecture": {
                "pattern": "Plan-and-Execute with deterministic domain validation",
                "modules": [
                    {"module": module, **module_details[module]} for module in module_order
                ],
                "data_stores": {
                    "pinecone": "Festival identity embeddings for semantic retrieval; local TF-IDF is the offline fallback.",
                    "supabase": "Festival facts, full company memory and run logs; bundled JSON is the offline fallback.",
                },
                "normal_chat_calls": 3,
                "embedding_requests": "One when vector retrieval is configured; each attempt is traced and has a bounded fallback.",
                "revision_policy": "Only the malformed stage is retried: MatchScorer can receive one structural repair and RoadmapBuilder one invariant-guided rewrite; analysis and retrieval are never repeated.",
            },
            "scoring": {
                "method": "The LLM rates four creative dimensions with evidence. Company relationship and deadline urgency come from structured data; code applies confidence guardrails, weights, arithmetic and the premiere-risk penalty.",
                "weights": scoring.weights_documentation(),
                "premiere_penalty": scoring.PREMIERE_PENALTY,
                "buckets": list(graph.BUCKET_LABELS.values()),
            },
            "grounding": {
                "deadline_policy": "Recorded final dates take precedence; stale cycles are projected explicitly and marked with confidence.",
                "premiere_policy": "Territorial shorthand is not treated as a strict world-premiere rule; compatibility is evaluated across the whole sequence.",
                "trace_policy": "Every actual chat or embedding model attempt, including retries, errors and rejected-parameter fallbacks, appears in steps.",
            },
            "campaign_workspace": {
                "status": "implemented; runtime fails closed unless the additive schema and server-only service-role credential are configured",
                "architecture": "Capability-scoped aggregate, deterministic reducer and premiere ledger, directed compatibility graph, frozen PlanningInput, deterministic CampaignPlanner, immutable strategy versions/diffs, and cloned no-write scenarios.",
                "legacy_evidence_boundary": "Initial and A-class refreshes reuse FilmAnalyzer, CompanyMemory, FestivalSearch, RiskChecker and MatchScorer through LegacyEvidenceAdapter; CampaignPlanner never accepts legacy dictionaries.",
                "incremental_policy": "Valid B/C cached replans perform no chat or embedding calls and return an explicit ReuseManifest. Cache mismatches remain stale without silent provider fallback.",
                "security": "A 256-bit opaque Secure HttpOnly SameSite=Lax cookie is hashed with SHA-256 server-side; campaign IDs do not authorize access; JSON mutations require an exact configured Origin; persistence requires a server-only Supabase service-role credential.",
                "ui": "GET /campaign serves the compact, server-authoritative Campaign Workspace while GET / remains the public Quick Strategy experience.",
            },
            "prompt_template": {
                "template": (
                    "Title: {title}\n"
                    "Format: {feature fiction | feature documentary | short}\n"
                    "Country / Language: {country} / {language}\n"
                    "Runtime: {minutes} minutes\n"
                    "Director: {name, career stage, previous films and festival history}\n"
                    "Synopsis: {2-4 sentences}\n"
                    "Themes: {comma-separated themes}\n"
                    "Premiere status: {no premiere yet | already premiered at X}\n"
                    "Target audience: {who this film is for}\n"
                    "Goal: {e.g. build a festival strategy for the next 12 months}"
                ),
                "notes": [
                    "Premiere status is the single most valuable field — it drives risk and priority.",
                    "Director career stage unlocks first-feature and emerging-talent sections.",
                    "Missing film facts are preserved and surfaced in the roadmap's open questions.",
                ],
            },
            "prompt_examples": examples,
            "endpoints": {
                "GET /api/team_info": "Student details.",
                "GET /api/agent_info": "This document.",
                "GET /api/model_architecture": "Architecture diagram (PNG).",
                "POST /api/execute": "Run the agent. Always returns exactly status, error, response and steps.",
                "GET /campaign": "Campaign Workspace page (workspace bootstrap occurs only from the page's explicit JSON request).",
                "/api/workspace/*": "Capability-scoped campaign creation, commands, replanning, scenarios and immutable strategy history.",
            },
        }
    )


# ------------------------------------------------- /api/model_architecture
@app.get("/api/model_architecture")
def model_architecture():
    diagram = config.ASSETS_DIR / "architecture.png"
    if not diagram.exists():
        return JSONResponse(
            {"error": "architecture.png is missing. Run: python scripts/make_architecture.py"},
            status_code=500,
        )
    return FileResponse(diagram, media_type="image/png", filename="architecture.png")


# ------------------------------------------------------------- /api/execute
@app.post("/api/execute")
def execute(request: ExecuteRequest) -> JSONResponse:
    """Main entry point.

    The response always carries exactly the four contract fields — status,
    error, response and steps.
    """

    prompt = (request.prompt or "").strip()
    if not prompt:
        return JSONResponse(
            {
                "status": "error",
                "error": "The 'prompt' field is required and must describe the film.",
                "response": None,
                "steps": [],
            }
        )
    if len(prompt) > config.MAX_PROMPT_CHARS:
        return JSONResponse(
            {
                "status": "error",
                "error": f"The 'prompt' field must be at most {config.MAX_PROMPT_CHARS} characters.",
                "response": None,
                "steps": [],
            }
        )

    try:
        result = graph.run(prompt)
    except graph.AgentRunError as exc:
        # Return whatever the agent managed to trace before failing.
        return JSONResponse(
            {"status": "error", "error": str(exc), "response": None, "steps": exc.steps}
        )
    except Exception as exc:  # noqa: BLE001 - the contract requires a readable error
        return JSONResponse(
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "response": None,
                "steps": [],
            }
        )

    supabase_store.log_run(
        {
            "prompt": prompt,
            "film_title": result["meta"].get("film_title"),
            "step_count": len(result["steps"]),
        }
    )

    return JSONResponse(
        {
            "status": "ok",
            "error": None,
            "response": result["response"],
            "steps": result["steps"],
        }
    )


# -------------------------------------------------------------- diagnostics
@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "festivals_loaded": len(corpus.load_festivals()),
            "llm_configured": config.llm_enabled(),
            "llm_model": config.LLM_MODEL if config.llm_enabled() else None,
            "pinecone_configured": config.pinecone_enabled(),
            "supabase_configured": config.supabase_enabled(),
            "task_catalog": prompts.TASK_CATALOG,
        }
    )
