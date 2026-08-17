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

from fastapi import FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from app import config  # noqa: E402
from app.agent import graph, prompts, scoring  # noqa: E402
from app.stores import corpus, supabase_store  # noqa: E402

app = FastAPI(title="The Distributor", description="AI agent for film festival strategy")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExecuteRequest(BaseModel):
    prompt: str = Field(default="")


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
                "pattern": "Plan-and-Execute (Planner -> Executor + tools -> Replanner)",
                "modules": [
                    {"module": "Planner", "type": "llm", "role": "Turns the request into an ordered task plan."},
                    {"module": "Executor", "type": "orchestrator", "role": "Runs planned tasks through the tool modules."},
                    {"module": "FilmAnalyzer", "type": "llm", "role": "Extracts the festival-relevant film profile."},
                    {"module": "FestivalSearch", "type": "tool", "role": "Pinecone semantic retrieval over the festival corpus + Supabase facts."},
                    {"module": "CompanyMemory", "type": "tool", "role": "Supabase lookup of the company's prior festival history."},
                    {"module": "MatchScorer", "type": "llm + deterministic", "role": "Rates six dimensions 0-5; code applies the weights."},
                    {"module": "RiskChecker", "type": "llm", "role": "Premiere, eligibility and deadline risk per festival."},
                    {"module": "RoadmapBuilder", "type": "llm", "role": "Writes the bucketed strategic roadmap."},
                    {"module": "Replanner", "type": "llm", "role": "Accepts the strategy or triggers one revision round."},
                ],
                "data_stores": {
                    "pinecone": "Festival identity embeddings for semantic matching.",
                    "supabase": "Festival facts, distribution-company memory, run logs.",
                },
            },
            "scoring": {
                "method": "The LLM rates each dimension 0-5 with evidence; the weighted 0-100 total is computed in code, then a premiere-risk penalty is subtracted.",
                "weights": scoring.weights_documentation(),
                "premiere_penalty": scoring.PREMIERE_PENALTY,
                "buckets": list(graph.BUCKET_LABELS.values()),
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
                    "Anything omitted is returned in the roadmap's open questions.",
                ],
            },
            "prompt_examples": examples,
            "endpoints": {
                "GET /api/team_info": "Student details.",
                "GET /api/agent_info": "This document.",
                "GET /api/model_architecture": "Architecture diagram (PNG).",
                "POST /api/execute": "Run the agent; returns response + full step trace.",
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

    try:
        result = graph.run(prompt)
    except Exception as exc:  # noqa: BLE001 - the contract requires a readable error
        return JSONResponse(
            {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "response": None,
                "steps": getattr(exc, "steps", []),
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
            "meta": result["meta"],
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
