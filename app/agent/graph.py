"""Plan-and-Execute orchestration: Planner -> Executor(+tools) -> Replanner."""

from __future__ import annotations

import time
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
    trace = Trace()
    llm = LLMClient(
        trace_callback=trace.add,
        deadline_monotonic=started + config.RUN_DEADLINE_SECONDS,
    )

    try:
        return _run(user_prompt, llm, trace, started)
    except AgentRunError:
        raise
    except Exception as exc:  # noqa: BLE001 - preserve the partial trace for the caller
        raise AgentRunError(f"{type(exc).__name__}: {exc}", trace.steps) from exc


def _run(
    user_prompt: str, llm: LLMClient, trace: Trace, started: float
) -> dict[str, Any]:

    plan = modules.planner(trace, user_prompt)
    task_modules = [task["module"] for task in plan["tasks"]]

    profile: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    memory: dict[str, Any] = {}
    scores: dict[str, dict[str, Any]] = {}
    risks: dict[str, dict[str, Any]] = {}
    roadmap: dict[str, Any] = {}
    ranked: list[dict[str, Any]] = []
    recommended_target: dict[str, Any] | None = None
    execution_log: list[dict[str, Any]] = []

    # The executor is visible before its children, while its response list is
    # filled in as each planned task completes.
    trace.add(
        "Executor",
        {"objective": plan.get("objective"), "tasks": plan["tasks"]},
        {"mode": "sequential_evidence_chain", "executed": execution_log},
    )

    for module_name in task_modules:
        if module_name == "FilmAnalyzer":
            profile = modules.film_analyzer(llm, trace, user_prompt)
            outcome = f"profile extracted ({profile.get('format')}, {len(profile.get('themes') or [])} themes)"
        elif module_name == "CompanyMemory":
            memory = modules.company_memory(trace)
            outcome = f"{len(memory.get('history', []))} history rows loaded before retrieval"
        elif module_name == "FestivalSearch":
            candidates = modules.festival_search(trace, profile, memory)
            outcome = f"{len(candidates)} deduplicated candidate festivals retrieved"
        elif module_name == "RiskChecker":
            risks = modules.risk_checker(trace, profile, candidates)
            outcome = f"{len(risks)} deterministic risk assessments"
        elif module_name == "MatchScorer":
            scores = modules.match_scorer(llm, trace, profile, candidates, memory)
            ranked = modules.assemble(candidates, scores, risks, memory, trace)
            recommended_target = modules.apply_premiere_strategy(profile, ranked)
            outcome = f"{len(scores)} creative-fit ratings combined with deterministic scores"
        elif module_name == "RoadmapBuilder":
            if not ranked:
                ranked = modules.assemble(candidates, scores, risks, memory, trace)
                recommended_target = modules.apply_premiere_strategy(profile, ranked)
            roadmap = modules.roadmap_builder(
                llm, trace, profile, ranked, memory, recommended_target
            )
            outcome = "roadmap drafted"
        else:
            continue
        execution_log.append({"module": module_name, "outcome": outcome})

    revisions = 0
    decision = modules.replanner(trace, ranked, roadmap, recommended_target)
    while (
        decision.get("decision") == "revise"
        and revisions < config.MAX_REPLAN_ROUNDS
        and candidates
        and time.monotonic() - started < config.REVISION_DEADLINE_SECONDS
    ):
        revisions += 1
        instructions = decision.get("revision_instructions") or decision.get("reason") or ""
        roadmap = modules.roadmap_builder(
            llm, trace, profile, ranked, memory, recommended_target, instructions
        )
        decision = modules.replanner(trace, ranked, roadmap, recommended_target)

    roadmap = modules.normalise_roadmap(roadmap, ranked, recommended_target, profile)
    if decision.get("decision") != "accept":
        decision = modules.replanner(trace, ranked, roadmap, recommended_target)

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
            "roadmap_validation": decision.get("defects"),
            "premiere_target": roadmap.get("premiere_target"),
            "elapsed_seconds": elapsed,
            "llm_usage": llm.usage,
            "execution_policy": {
                "normal_chat_calls": 3,
                "optional_match_scorer_repair_calls": 1,
                "optional_roadmap_revision_calls": 1,
                "deterministic_modules": ["Planner", "RiskChecker", "Replanner"],
            },
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

    premiere_target = roadmap.get("premiere_target")
    if premiere_target:
        lines.append("\n## Premiere Strategy")
        lines.append(
            f"- **Target:** {premiere_target.get('name') or premiere_target.get('id')} — "
            f"{premiere_target.get('reason')}"
        )

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
            deadline = record.get("deadline", {}) or {}
            if deadline.get("next_deadline"):
                label = f"deadline {deadline['next_deadline']}"
                if deadline.get("is_projection"):
                    label += " (projected)"
                details.append(label)
            elif record.get("deadline_month"):
                details.append(f"deadline month ~{record['deadline_month']} (verify)")
            constraint = record.get("premiere_constraint", {}) or {}
            if constraint.get("scope") and constraint.get("scope") != "none":
                premise = f"{constraint['scope']} premiere"
                if constraint.get("territory"):
                    premise += f" ({constraint['territory']})"
                if constraint.get("confidence") != "high":
                    premise += " (verify)"
                details.append(premise)
            if details:
                lines.append(f"*{' · '.join(details)}*")
            if entry.get("why"):
                lines.append(f"- **Why:** {entry['why']}")
            if entry.get("action"):
                lines.append(f"- **Action:** {entry['action']}")
            if record.get("risk_note"):
                label = (
                    "Eligibility assessment"
                    if record.get("eligibility_issue") or record.get("runtime_warning")
                    else f"Risk ({record.get('premiere_risk', 'none')})"
                )
                lines.append(
                    f"- **{label}:** {record['risk_note']}"
                )
            if record.get("eligibility_issue"):
                lines.append(
                    f"- **Eligibility:** ineligible ({record['eligibility_issue']}); verify the source rule before reconsidering."
                )
            sequence = record.get("premiere_sequence", {}) or {}
            if sequence.get("status") not in {None, "flexible", "not_applicable"}:
                lines.append(
                    f"- **Premiere sequence ({sequence.get('status')}):** "
                    f"{sequence.get('reason')}"
                )
            sources = record.get("retrieval_sources") or []
            facts_source = record.get("facts_source") or "bundled corpus"
            retrieval_backend = record.get("retrieval_backend") or "structured corpus"
            lines.append(
                f"- **Grounding:** retrieval={', '.join(sources) or 'structured corpus'}; "
                f"vector_backend={retrieval_backend}; facts={facts_source}; "
                "creative_identity=curated descriptive enrichment; "
                f"identity confidence={record.get('identity_confidence', 'low')}; "
                f"deadline confidence={deadline.get('confidence', 'low')}"
            )
            website = str(record.get("website") or "")
            if website.startswith(("https://", "http://")):
                lines.append(f"- **Source / submission page:** {website}")
            breakdown = record.get("breakdown") or {}
            if breakdown:
                parts = [
                    f"{scoring.DIMENSION_LABELS[dim]} {vals['rating']}/5"
                    for dim, vals in breakdown.items()
                ]
                lines.append(f"- **Score breakdown:** {', '.join(parts)}")
                lines.append(
                    f"- **Score calculation:** base {record.get('base_score', 0)}/100 "
                    f"minus premiere penalty {record.get('premiere_penalty', 0)} = "
                    f"{record.get('score', 0)}/100"
                )

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
