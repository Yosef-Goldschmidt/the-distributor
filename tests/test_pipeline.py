"""Offline end-to-end test of the agent pipeline with a stubbed LLM.

Verifies orchestration, retrieval fallback, deterministic scoring, bucketing,
markdown rendering and the /api/execute response contract — without spending a
cent of the LLMod.ai budget.

Usage:  python tests/test_pipeline.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE_DIR = Path(tempfile.mkdtemp(prefix="distributor-test-"))
os.environ["DATA_DIR"] = str(FIXTURE_DIR)

# Keep the test hermetic: no live Supabase, Pinecone or LLM, even when .env is
# populated. These are set before app.config is imported, and python-dotenv does
# not override existing environment variables.
os.environ["SUPABASE_URL"] = ""
os.environ["SUPABASE_ANON_KEY"] = ""
os.environ["SUPABASE_SERVICE_KEY"] = ""
os.environ["PINECONE_API_KEY"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_EMBED_MODEL"] = ""
os.environ["LLM_EMBEDDING_MODEL"] = ""

FESTIVALS = [
    {
        "id": "docaviv", "name": "Docaviv", "country": "Israel", "city": "Tel Aviv",
        "region": "Middle East", "tier": "B", "month": "May", "typical_deadline_month": "January",
        "accepts": ["feature_doc", "short_doc"], "premiere_requirement": "national",
        "competitive": True, "submission_fee_usd_range": "0-60",
        "focus": "Israel's leading documentary festival, strong on social and environmental subjects.",
        "themes": ["documentary", "israeli", "social_realism", "environmental"],
        "notable_past_selections": ["Mission: Hebron (2019)", "H2: The Occupation Lab (2022)"],
        "award_patterns": "Rewards intimate character-driven local documentary.",
        "strategic_value": "Essential domestic launch for Israeli documentary.",
        "notes": "Israeli premiere expected.", "website": "https://docaviv.co.il",
    },
    {
        "id": "idfa", "name": "IDFA", "country": "Netherlands", "city": "Amsterdam",
        "region": "Western Europe", "tier": "A", "month": "November",
        "typical_deadline_month": "August", "accepts": ["feature_doc", "short_doc"],
        "premiere_requirement": "world", "competitive": True, "submission_fee_usd_range": "40-100",
        "focus": "The world's largest documentary festival; formally ambitious, politically engaged work.",
        "themes": ["documentary", "human_rights", "political", "environmental", "auteur"],
        "notable_past_selections": ["Children of the Mist (2021)", "The Etilaat Roz (2022)"],
        "award_patterns": "Favours authored documentary with a strong point of view.",
        "strategic_value": "Career-defining documentary launch with a major industry market.",
        "notes": "World premiere required for the main competition.",
        "website": "https://www.idfa.nl",
    },
    {
        "id": "sitges", "name": "Sitges Film Festival", "country": "Spain", "city": "Sitges",
        "region": "Western Europe", "tier": "B", "month": "October",
        "typical_deadline_month": "July", "accepts": ["feature_fiction", "short_fiction"],
        "premiere_requirement": "regional", "competitive": True,
        "submission_fee_usd_range": "30-80",
        "focus": "The leading fantastic and genre film festival in Europe.",
        "themes": ["genre", "horror", "sci_fi", "thriller"],
        "notable_past_selections": ["Hereditary (2018)", "Talk to Me (2023)"],
        "award_patterns": "Rewards inventive genre filmmaking.",
        "strategic_value": "Key market for genre sales.",
        "notes": "Not a documentary venue.", "website": "https://sitgesfilmfestival.com",
    },
]

COMPANY = {
    "company": {
        "id": "meridian-films",
        "name": "Meridian Films",
        "profile": "Tel Aviv documentary specialist.",
    },
    "history": [
        {
            "company_id": "meridian-films", "festival_id": "docaviv", "festival_name": "Docaviv",
            "film_title": "The Salt Road", "year": 2021, "result": "awarded",
            "note": "Won the Israeli competition; direct programmer relationship.",
        }
    ],
}

(FIXTURE_DIR / "festivals.json").write_text(json.dumps(FESTIVALS), encoding="utf-8")
(FIXTURE_DIR / "company.json").write_text(json.dumps(COMPANY), encoding="utf-8")
for name in ("team_info.json", "prompt_examples.json"):
    source = ROOT / "data" / name
    if source.exists():
        (FIXTURE_DIR / name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

from app.agent import graph, scoring  # noqa: E402
from app.llm import LLMClient  # noqa: E402

CALLS: list[str] = []


def fake_complete_json(self, system, user, **kwargs):  # noqa: ANN001
    self.usage["calls"] += 1
    payload = json.loads(user)

    if system.startswith("You are the Planner"):
        CALLS.append("Planner")
        return {
            "objective": "Build a festival strategy",
            "tasks": [
                {"module": module, "goal": "…"}
                for module in [
                    "FilmAnalyzer", "FestivalSearch", "CompanyMemory",
                    "MatchScorer", "RiskChecker", "RoadmapBuilder",
                ]
            ],
            "assumptions": [],
        }

    if system.startswith("You are FilmAnalyzer"):
        CALLS.append("FilmAnalyzer")
        return {
            "title": "Salt and Ash",
            "logline": "Three women fight to stay on collapsing salt flats.",
            "format": "feature_doc", "genres": ["documentary"],
            "themes": ["environmental", "women_filmmakers", "displacement"],
            "country": "Israel", "language": "Hebrew", "runtime_minutes": 89,
            "director_profile": "Second feature.", "premiere_status": "world_premiere_available",
            "target_audience": "Documentary and human-rights audiences",
            "festival_angles": ["environmental collapse"], "missing_info": [],
            "search_query": "Israeli environmental documentary about women resisting displacement",
        }

    if system.startswith("You are MatchScorer"):
        CALLS.append("MatchScorer")
        ratings = {
            "idfa": {"thematic_fit": 5, "genre_fit": 5, "lineup_similarity": 4,
                     "company_relationship": 0, "strategic_value": 5},
            "docaviv": {"thematic_fit": 5, "genre_fit": 5, "lineup_similarity": 5,
                        "company_relationship": 5, "strategic_value": 4},
            "sitges": {"thematic_fit": 0, "genre_fit": 0, "lineup_similarity": 0,
                       "company_relationship": 0, "strategic_value": 1},
        }
        return {
            "scores": [
                {
                    "id": candidate["id"],
                    "ratings": ratings.get(candidate["id"], {}),
                    "evidence": {"thematic_fit": "stub"},
                    "headline": f"stub headline for {candidate['id']}",
                }
                for candidate in payload["candidates"]
            ]
        }

    if system.startswith("You are RiskChecker"):
        CALLS.append("RiskChecker")
        risk = {
            "idfa": ("none", "open", True, True, "World premiere is available."),
            "docaviv": ("medium", "closing_soon", True, False, "Israeli premiere may conflict with IDFA timing."),
            "sitges": ("none", "open", False, False, "Does not accept documentary."),
        }
        return {
            "risks": [
                {
                    "id": candidate["id"],
                    "premiere_risk": risk[candidate["id"]][0],
                    "deadline_status": risk[candidate["id"]][1],
                    "eligible": risk[candidate["id"]][2],
                    "premiere_opportunity": risk[candidate["id"]][3],
                    "risk_note": risk[candidate["id"]][4],
                }
                for candidate in payload["candidates"]
            ]
        }

    if system.startswith("You are RoadmapBuilder"):
        CALLS.append("RoadmapBuilder")
        buckets: dict[str, list] = {
            "submit_first": [], "prioritize_next": [], "leverage": [], "hold_avoid": [],
        }
        for festival in payload["festivals"]:
            buckets[festival["bucket"]].append(
                {"id": festival["id"], "why": "stub why", "action": "stub action"}
            )
        return {
            "headline": "Launch at IDFA, then land the domestic premiere.",
            "strategy_summary": "Stub summary.", "buckets": buckets,
            "calendar": [{"month": "August", "action": "Submit to IDFA"}],
            "next_actions": ["Lock the world premiere plan"],
            "open_questions": ["Is the film finished by August?"],
        }

    if system.startswith("You are the Replanner"):
        CALLS.append("Replanner")
        return {"decision": "complete", "reason": "Roadmap is usable.", "revision_instructions": None}

    raise AssertionError(f"unexpected system prompt: {system[:60]}")


def main() -> None:
    from app import config

    assert not config.supabase_enabled(), "test must not reach live Supabase"
    assert not config.pinecone_enabled(), "test must not reach live Pinecone"
    assert not config.llm_enabled(), "test must not reach the live LLM"

    LLMClient.complete_json = fake_complete_json  # type: ignore[method-assign]
    LLMClient.enabled = True  # type: ignore[assignment]

    result = graph.run("Salt and Ash, an Israeli environmental documentary.")

    modules_trace = [step["module"] for step in result["steps"]]
    # MatchScorer and RiskChecker run concurrently, so their order is not fixed.
    assert modules_trace[:3] == ["Planner", "FilmAnalyzer", "FestivalSearch"], modules_trace
    assert modules_trace[3] == "CompanyMemory", modules_trace
    assert set(modules_trace[4:6]) == {"MatchScorer", "RiskChecker"}, modules_trace
    assert modules_trace[6:] == ["MatchScorer", "RoadmapBuilder", "Executor", "Replanner"], modules_trace
    assert all({"module", "prompt", "response"} <= set(step) for step in result["steps"])
    modules_seen = set(modules_trace)

    ranked = {record["id"]: record for record in result["meta"]["ranked_festivals"]}

    today = datetime.now(timezone.utc).date()
    idfa = ranked["idfa"]
    urgency, _ = scoring.deadline_urgency("August", today)
    expected_idfa = round(
        (5 / 5) * scoring.WEIGHTS["thematic_fit"]
        + (5 / 5) * scoring.WEIGHTS["genre_fit"]
        + (4 / 5) * scoring.WEIGHTS["lineup_similarity"]
        + 0
        + (5 / 5) * scoring.WEIGHTS["strategic_value"]
        + (urgency / 5) * scoring.WEIGHTS["deadline_urgency"]
    )
    assert idfa["score"] == expected_idfa, f"IDFA score {idfa['score']} != {expected_idfa}"
    assert idfa["bucket"] == "submit_first", idfa["bucket"]
    assert idfa["ratings"]["deadline_urgency"] == urgency, idfa["ratings"]
    assert idfa["evidence"].get("deadline_urgency"), "urgency evidence missing"

    docaviv = ranked["docaviv"]
    assert docaviv["premiere_penalty"] == 7, docaviv["premiere_penalty"]
    assert docaviv["bucket"] in {"submit_first", "leverage"}, docaviv["bucket"]

    sitges = ranked["sitges"]
    assert sitges["bucket"] == "hold_avoid", sitges["bucket"]

    # A world-premiere festival is an opportunity when the film still has its premiere.
    premiere_case = {
        "score": 85, "tier": "A", "deadline_status": "closing_soon", "eligible": True,
        "premiere_risk": "high", "premiere_opportunity": True,
        "ratings": {"company_relationship": 0},
    }
    assert scoring.assign_bucket(premiere_case) == "submit_first"
    assert scoring.assign_bucket({**premiere_case, "premiere_opportunity": False}) == "hold_avoid"
    assert scoring.assign_bucket({**premiere_case, "eligible": False}) == "hold_avoid"

    # Deadline urgency must be calendar-derived, never taken from the model.
    august_urgency, _ = scoring.deadline_urgency("August", date(2026, 8, 17))
    assert august_urgency == 5.0
    assert scoring.deadline_urgency("September", date(2026, 8, 17))[0] == 5.0
    assert scoring.deadline_urgency("December", date(2026, 8, 17))[0] == 3.0
    assert scoring.deadline_urgency(None, date(2026, 8, 17))[0] == 2.0
    assert "deadline_urgency" not in scoring.LLM_DIMENSIONS

    assert "# Festival Strategy — Salt and Ash" in result["response"]
    assert "Submit First" in result["response"] and "Hold / Avoid" in result["response"]
    assert "IDFA" in result["response"]
    assert result["meta"]["revision_rounds"] == 0
    assert CALLS.count("MatchScorer") == 1 and CALLS.count("RoadmapBuilder") == 1
    assert len(CALLS) == 6, f"expected 6 LLM calls, got {len(CALLS)}: {CALLS}"

    print("pipeline OK")
    print(f"  trace modules : {' -> '.join(modules_trace)}")
    print(f"  llm calls     : {len(CALLS)}")
    print(
        "  scores        : "
        + ", ".join(f"{r['name']}={r['score']}" for r in result["meta"]["ranked_festivals"])
    )

    # --- API contract ---
    from fastapi.testclient import TestClient

    from api.index import app

    client = TestClient(app)

    body = client.post("/api/execute", json={"prompt": "Salt and Ash documentary"}).json()
    assert {"status", "error", "response", "steps"} <= set(body)
    assert body["status"] == "ok" and body["error"] is None and body["response"]

    empty = client.post("/api/execute", json={"prompt": "  "}).json()
    assert empty["status"] == "error" and empty["response"] is None and empty["steps"] == []

    team = client.get("/api/team_info").json()
    assert {"group_batch_order_number", "team_name", "students"} <= set(team)

    info = client.get("/api/agent_info").json()
    assert {"description", "purpose", "prompt_template", "prompt_examples"} <= set(info)
    diagram_modules = {module["module"] for module in info["architecture"]["modules"]}
    assert modules_seen <= diagram_modules, modules_seen - diagram_modules

    png = client.get("/api/model_architecture")
    assert png.status_code == 200, png.text[:200]
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    home = client.get("/")
    assert home.status_code == 200 and "Run Agent" in home.text

    print("api contract OK")


if __name__ == "__main__":
    main()
