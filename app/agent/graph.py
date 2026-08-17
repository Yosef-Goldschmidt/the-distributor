"""Plan-and-Execute orchestration: Planner -> Executor(+tools) -> Replanner."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app import config
from app.agent import modules, scoring
from app.agent.modules import Trace
from app.llm import LLMClient
from app.stores import corpus

BUCKET_LABELS = {
    "submit_first": "Submit First",
    "prioritize_next": "Prioritize Next",
    "leverage": "Leverage",
    "hold_avoid": "Hold / Avoid",
}
BUCKET_ORDER = ["submit_first", "prioritize_next", "leverage", "hold_avoid"]


class AgentRunError(RuntimeError):
    """A run that failed part-way, carrying the trace collected so far."""

    def __init__(self, message: str, steps: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.steps = steps


def run(user_prompt: str) -> dict[str, Any]:
    """Execute one full strategy run and return response, steps and metadata."""

    if not corpus.load_festivals():
        raise RuntimeError(
            "The festival corpus (data/festivals.json) is empty — seed it before running."
        )

    started = time.monotonic()
    llm = LLMClient()
    trace = Trace()

    try:
        return _run(user_prompt, llm, trace, started)
    except AgentRunError:
        raise
    except Exception as exc:  # noqa: BLE001 - preserve the partial trace for the caller
        raise AgentRunError(f"{type(exc).__name__}: {exc}", trace.steps) from exc


def _run(
    user_prompt: str, llm: LLMClient, trace: Trace, started: float
) -> dict[str, Any]:

    plan = modules.planner(llm, trace, user_prompt)
    task_modules = [task["module"] for task in plan["tasks"]]

    profile: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    memory: dict[str, Any] = {}
    scores: dict[str, dict[str, Any]] = {}
    risks: dict[str, dict[str, Any]] = {}
    roadmap: dict[str, Any] = {}
    ranked: list[dict[str, Any]] = []
    execution_log: list[dict[str, Any]] = []

    for module_name in task_modules:
        if module_name == "FilmAnalyzer":
            profile = modules.film_analyzer(llm, trace, user_prompt)
            outcome = f"profile extracted ({profile.get('format')}, {len(profile.get('themes') or [])} themes)"
        elif module_name == "FestivalSearch":
            candidates = modules.festival_search(trace, profile)
            outcome = f"{len(candidates)} candidate festivals retrieved"
        elif module_name == "CompanyMemory":
            memory = modules.company_memory(trace, candidates)
            outcome = f"{len(memory.get('history', []))} prior relationships matched"
        elif module_name == "MatchScorer":
            # MatchScorer and RiskChecker are independent, so they run concurrently
            # to stay comfortably inside the 300s serverless limit.
            with ThreadPoolExecutor(max_workers=2) as pool:
                score_future = pool.submit(
                    modules.match_scorer, llm, trace, profile, candidates, memory
                )
                risk_future = pool.submit(
                    modules.risk_checker, llm, trace, profile, candidates
                )
                scores = score_future.result()
                risks = risk_future.result()
            outcome = f"{len(scores)} festivals rated, {len(risks)} risk assessments"
        elif module_name == "RiskChecker":
            if not risks:
                risks = modules.risk_checker(llm, trace, profile, candidates)
            ranked = modules.assemble(candidates, scores, risks, trace)
            outcome = f"{len(risks)} risk assessments, scores computed"
        elif module_name == "RoadmapBuilder":
            if not ranked:
                ranked = modules.assemble(candidates, scores, risks, trace)
            roadmap = modules.roadmap_builder(llm, trace, profile, ranked, memory)
            outcome = "roadmap drafted"
        else:
            continue
        execution_log.append({"module": module_name, "outcome": outcome})

    trace.add(
        "Executor",
        {"objective": plan.get("objective"), "tasks": plan["tasks"]},
        {"executed": execution_log},
    )

    revisions = 0
    decision = modules.replanner(llm, trace, plan.get("objective", user_prompt), ranked, roadmap)
    while (
        decision.get("decision") == "revise"
        and revisions < config.MAX_REPLAN_ROUNDS
        and candidates
        and time.monotonic() - started < config.REVISION_DEADLINE_SECONDS
    ):
        revisions += 1
        instructions = decision.get("revision_instructions") or decision.get("reason") or ""
        scores = modules.match_scorer(llm, trace, profile, candidates, memory, instructions)
        ranked = modules.assemble(candidates, scores, risks, trace)
        roadmap = modules.roadmap_builder(llm, trace, profile, ranked, memory, instructions)
        decision = modules.replanner(
            llm, trace, plan.get("objective", user_prompt), ranked, roadmap
        )

    by_id = {record["id"]: record for record in ranked}
    response = render_markdown(profile, roadmap, ranked, by_id)
    elapsed = round(time.monotonic() - started, 1)

    return {
        "response": response,
        "steps": trace.steps,
        "meta": {
            "film_title": profile.get("title"),
            "candidates_considered": len(candidates),
            "revision_rounds": revisions,
            "replanner_decision": decision.get("decision"),
            "premiere_target": roadmap.get("premiere_target"),
            "elapsed_seconds": elapsed,
            "llm_usage": llm.usage,
            "scoring_weights": scoring.weights_documentation(),
            "roadmap": {
                "headline": roadmap.get("headline"),
                "buckets": roadmap.get("buckets", {}),
                "calendar": roadmap.get("calendar", []),
                "next_actions": roadmap.get("next_actions", []),
                "open_questions": roadmap.get("open_questions", []),
            },
            "ranked_festivals": ranked,
        },
    }


def render_markdown(
    profile: dict[str, Any],
    roadmap: dict[str, Any],
    ranked: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
) -> str:
    title = profile.get("title") or "Untitled film"
    lines: list[str] = [f"# Festival Strategy — {title}"]

    if roadmap.get("headline"):
        lines.append(f"**{roadmap['headline']}**")
    if roadmap.get("strategy_summary"):
        lines.append(roadmap["strategy_summary"])

    buckets = roadmap.get("buckets", {}) or {}
    for bucket in BUCKET_ORDER:
        entries = buckets.get(bucket) or []
        if not entries:
            continue
        lines.append(f"\n## {BUCKET_LABELS[bucket]}")
        for entry in entries:
            record = by_id.get(entry.get("id"), {})
            name = record.get("name") or entry.get("id")
            score = record.get("score")
            header = f"### {name}" + (f" — {score}/100" if score is not None else "")
            lines.append(header)
            details = []
            if record.get("country"):
                details.append(str(record["country"]))
            if record.get("deadline_month"):
                details.append(f"deadline ~{record['deadline_month']}")
            if record.get("premiere_requirement"):
                details.append(f"{record['premiere_requirement']} premiere")
            if details:
                lines.append(f"*{' · '.join(details)}*")
            if entry.get("why"):
                lines.append(f"- **Why:** {entry['why']}")
            if entry.get("action"):
                lines.append(f"- **Action:** {entry['action']}")
            if record.get("risk_note"):
                lines.append(
                    f"- **Risk ({record.get('premiere_risk', 'none')}):** {record['risk_note']}"
                )
            breakdown = record.get("breakdown") or {}
            if breakdown:
                parts = [
                    f"{scoring.DIMENSION_LABELS[dim]} {vals['rating']}/5"
                    for dim, vals in breakdown.items()
                ]
                lines.append(f"- **Score breakdown:** {', '.join(parts)}")

    calendar = roadmap.get("calendar") or []
    if calendar:
        lines.append("\n## Submission Calendar")
        lines.extend(f"- **{item.get('month')}** — {item.get('action')}" for item in calendar)

    next_actions = roadmap.get("next_actions") or []
    if next_actions:
        lines.append("\n## Next Actions")
        lines.extend(f"- {action}" for action in next_actions)

    open_questions = roadmap.get("open_questions") or []
    if open_questions:
        lines.append("\n## Open Questions")
        lines.extend(f"- {question}" for question in open_questions)

    if not buckets and ranked:
        lines.append("\n## Ranked Festivals")
        lines.extend(f"- {r['name']} — {r['score']}/100 ({r['bucket']})" for r in ranked)

    return "\n\n".join(lines).strip()
