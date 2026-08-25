"""Offline end-to-end test of the agent pipeline with a stubbed LLM.

Verifies orchestration, retrieval fallback, deterministic scoring, bucketing,
markdown rendering and the /api/execute response contract — without spending a
cent of the LLMod.ai budget.

Usage:  python tests/test_pipeline.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import date
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
        "premiere_requirement_raw": "World - Israel", "submission_open": "2026-09-01",
        "final_deadline": "2027-01-15", "identity_confidence": "high",
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
        "premiere_requirement_raw": "World", "submission_open": "2026-04-01",
        "final_deadline": "2026-08-30", "identity_confidence": "high",
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
        "premiere_requirement_raw": "World - Spain", "submission_open": "2026-02-01",
        "final_deadline": "2026-07-15", "identity_confidence": "high",
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

from app.agent import graph, prompts, scoring  # noqa: E402
from app.llm import LLMClient  # noqa: E402

CALLS: list[str] = []


def fake_complete_json(self, system, user, **kwargs):  # noqa: ANN001
    payload = json.loads(user)

    def complete(module, result):  # noqa: ANN001
        CALLS.append(module)
        self.usage["calls"] += 1
        self.usage["attempts"] += 1
        if self.trace_callback:
            self.trace_callback(
                kwargs.get("module", module),
                {"system": system, "user": user, "provider": {"attempt": 1}},
                result,
            )
        return result

    if system.startswith("You are FilmAnalyzer"):
        return complete("FilmAnalyzer", {
            "title": "Salt and Ash",
            "logline": "Three women fight to stay on collapsing salt flats.",
            "format": "feature_doc", "genres": ["documentary"],
            "themes": ["environmental", "women_filmmakers", "displacement"],
            "country": "Israel", "language": "Hebrew", "runtime_minutes": 89,
            "director_profile": "Second feature.", "premiere_status": "world_premiere_available",
            "premiere_history": [],
            "target_audience": "Documentary and human-rights audiences",
            "festival_angles": ["environmental collapse"], "missing_info": [],
            "search_query": "Israeli environmental documentary about women resisting displacement",
        })

    if system.startswith("You are MatchScorer"):
        ratings = {
            "idfa": {"thematic_fit": 5, "genre_fit": 5, "lineup_similarity": 4,
                     "strategic_value": 5},
            "docaviv": {"thematic_fit": 5, "genre_fit": 5, "lineup_similarity": 5,
                        "strategic_value": 4},
            "sitges": {"thematic_fit": 0, "genre_fit": 0, "lineup_similarity": 0,
                       "strategic_value": 1},
        }
        return complete("MatchScorer", {
            "scores": [
                {
                    "id": candidate["id"],
                    "ratings": ratings.get(candidate["id"], {}),
                    "evidence": {
                        "thematic_fit": "Theme overlap in supplied data",
                        "genre_fit": "Accepted format and stated focus align",
                        "lineup_similarity": "Past selections support the comparison",
                        "strategic_value": "Tier and market role support the rating",
                    },
                    "headline": f"stub headline for {candidate['id']}",
                }
                for candidate in payload["candidates"]
            ]
        })

    if system.startswith("You are RoadmapBuilder"):
        buckets: dict[str, list] = {
            "submit_first": [], "prioritize_next": [], "leverage": [], "hold_avoid": [],
        }
        for festival in payload["festivals"]:
            buckets[festival["bucket"]].append(
                {
                    "id": festival["id"],
                    "evidence_dimensions": ["thematic_fit", "strategic_value"],
                }
            )
        return complete("RoadmapBuilder", {
            "headline": "Launch at IDFA, then land the domestic premiere.",
            "strategy_summary": "Stub summary.",
            "premiere_target": payload.get("recommended_premiere_target"),
            "buckets": buckets,
            "calendar": [{"month": "August", "action": "Submit to IDFA"}],
            "next_actions": ["Lock the world premiere plan"],
            "open_questions": ["Is the film finished by August?"],
        })

    raise AssertionError(f"unexpected system prompt: {system[:60]}")


def main() -> None:
    from app import config

    assert not config.supabase_enabled(), "test must not reach live Supabase"
    assert not config.pinecone_enabled(), "test must not reach live Pinecone"
    assert not config.llm_enabled(), "test must not reach the live LLM"

    LLMClient.complete_json = fake_complete_json  # type: ignore[method-assign]
    LLMClient.enabled = True  # type: ignore[assignment]

    result = graph.run(
        "Salt and Ash, an Israeli environmental documentary that has never been publicly screened."
    )

    modules_trace = [step["module"] for step in result["steps"]]
    assert modules_trace == [
        "Planner", "Executor", "FilmAnalyzer", "FilmAnalyzer", "CompanyMemory", "FestivalSearch",
        "RiskChecker", "MatchScorer", "MatchScorer", "RoadmapBuilder", "Replanner",
    ], modules_trace
    assert all({"module", "prompt", "response"} <= set(step) for step in result["steps"])
    modules_seen = set(modules_trace)

    ranked = {record["id"]: record for record in result["meta"]["ranked_festivals"]}

    idfa = ranked["idfa"]
    urgency = idfa["deadline"]["urgency"]
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
    assert docaviv["premiere_penalty"] == 0, docaviv["premiere_penalty"]
    assert docaviv["ratings"]["company_relationship"] == 2.5
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
    assert len(CALLS) == 3, f"expected 3 LLM calls, got {len(CALLS)}: {CALLS}"
    assert result["meta"]["premiere_target"]["id"] == "idfa"
    executor = next(step for step in result["steps"] if step["module"] == "Executor")
    scorer_outcome = next(
        row["outcome"]
        for row in executor["response"]["executed"]
        if row["module"] == "MatchScorer"
    )
    assert scorer_outcome.startswith("3 creative-fit ratings"), scorer_outcome

    bucket_ids = [
        entry["id"]
        for entries in result["meta"]["roadmap"]["buckets"].values()
        for entry in entries
    ]
    assert sorted(bucket_ids) == sorted(ranked), bucket_ids
    assert len(bucket_ids) == len(set(bucket_ids)), bucket_ids

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
    # The contract requires EXACTLY these four top-level fields.
    assert set(body) == {"status", "error", "response", "steps"}, set(body)
    assert body["status"] == "ok" and body["error"] is None and body["response"]
    assert isinstance(body["steps"], list) and body["steps"]

    # Extra request fields can never add top-level response fields.
    with_meta = client.post(
        "/api/execute", json={"prompt": "Salt and Ash documentary", "include_meta": True}
    ).json()
    assert set(with_meta) == {"status", "error", "response", "steps"}, set(with_meta)

    # A failure must still return the trace collected up to that point.
    def explode(self, system, user, **kwargs):  # noqa: ANN001
        if system.startswith("You are MatchScorer"):
            raise RuntimeError("simulated provider outage")
        return fake_complete_json(self, system, user, **kwargs)

    LLMClient.complete_json = explode  # type: ignore[method-assign]
    failed = client.post("/api/execute", json={"prompt": "Salt and Ash documentary"}).json()
    LLMClient.complete_json = fake_complete_json  # type: ignore[method-assign]
    assert set(failed) == {"status", "error", "response", "steps"}, set(failed)
    assert failed["status"] == "error" and failed["response"] is None
    assert "simulated provider outage" in failed["error"], failed["error"]
    assert failed["steps"], "a partial trace must survive a mid-run failure"
    assert [s["module"] for s in failed["steps"]][:2] == ["Planner", "Executor"]

    empty = client.post("/api/execute", json={"prompt": "  "}).json()
    assert empty["status"] == "error" and empty["response"] is None and empty["steps"] == []

    oversized = client.post("/api/execute", json={"prompt": "x" * 12001}).json()
    assert oversized["status"] == "error" and oversized["steps"] == []

    malformed = client.post(
        "/api/execute", content="{bad json", headers={"Content-Type": "application/json"}
    ).json()
    assert set(malformed) == {"status", "error", "response", "steps"}, malformed
    assert malformed["status"] == "error" and malformed["steps"] == []

    wrong_type = client.post("/api/execute", json={"prompt": {"title": "bad"}}).json()
    assert set(wrong_type) == {"status", "error", "response", "steps"}, wrong_type
    assert wrong_type["status"] == "error"

    team = client.get("/api/team_info").json()
    assert {"group_batch_order_number", "team_name", "students"} <= set(team)
    assert re.fullmatch(r"\d+_\d+", team["group_batch_order_number"])
    assert team["group_batch_order_number"] != "1_00"
    assert all(
        student.get("name")
        and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", student.get("email", ""))
        and "REPLACE_ME" not in student["email"]
        for student in team["students"]
    )

    info = client.get("/api/agent_info").json()
    assert {"description", "purpose", "prompt_template", "prompt_examples"} <= set(info)
    architecture_modules = info["architecture"]["modules"]
    canonical_modules = ["Planner", "Executor", *prompts.TASK_CATALOG, "Replanner"]
    assert [module["module"] for module in architecture_modules] == canonical_modules
    assert modules_seen == set(canonical_modules), set(canonical_modules) - modules_seen
    architecture_types = {module["module"]: module["type"] for module in architecture_modules}
    assert architecture_types["Planner"] == "deterministic control"
    assert architecture_types["RiskChecker"] == "deterministic domain rules"
    assert architecture_types["Replanner"] == "deterministic validator"
    assert info["architecture"]["normal_chat_calls"] == 3

    png = client.get("/api/model_architecture")
    assert png.status_code == 200, png.text[:200]
    assert png.headers["content-type"] == "image/png"
    assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    home = client.get("/")
    assert home.status_code == 200 and "Run Agent" in home.text

    print("api contract OK")


if __name__ == "__main__":
    main()
