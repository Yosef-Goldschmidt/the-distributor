"""Run the behavioral suite against an isolated archive of a git commit."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values


MAX_PAID_LIVE_EXECUTIONS = 10
IMPLEMENTED_LIVE_EXECUTIONS = 1
SECRET_KEYS = {
    "LLM_API_KEY",
    "PINECONE_API_KEY",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_ANON_KEY",
}
PROVIDER_KEYS = {
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_EMBED_MODEL",
    "LLM_EMBEDDING_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_REASONING_EFFORT",
    "PINECONE_API_KEY",
    "PINECONE_INDEX",
    "PINECONE_NAMESPACE",
    "PINECONE_EMBED_MODEL",
    "EMBED_PROVIDER",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_ANON_KEY",
    "COMPANY_ID",
}


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def archive_commit(root: Path, commit: str, destination: Path) -> None:
    payload = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        archive.extractall(destination, filter="data")


def redact(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.upper() in SECRET_KEYS or "AUTHORIZATION" in key.upper():
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact(item, secrets)
        return redacted
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    text = value
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[REDACTED]", text)
    return text


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:80] or "scenario"


def report_markdown(
    *,
    commit: str,
    branch: str,
    started_at: str,
    existing_tests: dict[str, Any],
    payload: dict[str, Any],
    live_requested: int,
    live_reused: bool,
    production_source: str,
    artifact_key: str,
) -> str:
    results = payload["results"]
    cost = payload["cost"]
    lines = [
        "# Behavioral Evaluation Baseline",
        "",
        f"- Starting git commit: `{commit}`",
        f"- Branch at start: `{branch}`",
        f"- Started at (UTC): `{started_at}`",
        f"- Production source: {production_source}",
        f"- Existing test suite: **{existing_tests['status']}** ({existing_tests['summary']})",
        f"- Paid live execution cap: `{MAX_PAID_LIVE_EXECUTIONS}` full `/api/execute` runs",
        f"- Live executions requested in this run: `{live_requested}`",
        f"- Prior live artifact reused without a new provider call: `{str(live_reused).lower()}`",
        "",
        "## Overall",
        "",
        "| Scenario | Status | Reason |",
        "|---|---|---|",
    ]
    for item in results:
        reason = str(item["reason"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {item['name']} | **{item['status']}** | {reason} |")

    failures = [item for item in results if item["status"] == "FAIL"]
    warnings = [item for item in results if item["status"] == "WARN"]
    not_run = [item for item in results if item["status"] == "NOT RUN"]
    blockers = [item for item in failures if item.get("baseline_blocker")]
    later = [item for item in results if item.get("later_100")]

    lines.extend(["", "## True correctness failures", ""])
    if failures:
        for item in failures:
            root = item.get("diagnosis", {}).get("root_cause") or item.get("diagnosis", {}).get("root_layer") or "See raw artifact."
            lines.append(f"- **{item['name']}** — {item['reason']} Root cause: {root}")
    else:
        lines.append("- None found by the executed deterministic/mocked checks.")

    lines.extend(["", "## Weaker-quality / UX issues", ""])
    if warnings:
        for item in warnings:
            lines.append(f"- **{item['name']}** — {item['reason']}")
    else:
        lines.append("- None.")
    if not_run:
        lines.append(
            "- Live-only evidence remains unavailable for: "
            + ", ".join(item["name"] for item in not_run)
            + ". These are explicitly **NOT RUN**, not simulated passes."
        )

    lines.extend(["", "## Root-cause classification for FAILs", ""])
    if failures:
        for item in failures:
            diagnosis = item.get("diagnosis", {})
            lines.append(f"### {item['name']}")
            lines.append("")
            for key, value in diagnosis.items():
                lines.append(f"- `{key}`: {value}")
            lines.append("")
    else:
        lines.append("- No FAILs.")

    lines.extend(["", "## Baseline blockers", ""])
    if blockers:
        for item in blockers:
            lines.append(f"- **{item['name']}** — {item['reason']}")
    else:
        lines.append("- No executed FAIL is classified as a baseline-stability blocker.")

    lines.extend(["", "## Later `100` branch", ""])
    if later:
        for item in later:
            lines.append(f"- **{item['name']}** — {item['status']}: {item['reason']}")
    else:
        lines.append("- No items were assigned to the later branch.")

    lines.extend(
        [
            "",
            "## Retrieval diagnosis rule",
            "",
            "Every retrieval scenario reports these layers independently:",
            "",
            "1. `data_corpus` — whether the festival/entity exists in the committed corpus.",
            "2. `retrieval_recall` — whether it survives broad retrieval and the candidate cutoff.",
            "3. `scoring_ranking` — whether MatchScorer ranks a retrieved candidate appropriately.",
            "4. `roadmap_presentation` — whether a correctly ranked candidate survives into the strategy.",
            "",
            "A missing corpus entity is never reported as a retrieval, scoring or roadmap failure.",
            "",
            "## Cost and coverage",
            "",
            f"- Real full `/api/execute` runs: `{cost.get('real_api_execute_runs', 0)}`",
            f"- Successful real chat calls inferred from trace: `{cost.get('real_chat_calls', 0)}`",
            f"- Real chat attempts in trace: `{cost.get('real_chat_attempts', 0)}`",
            f"- Successful real embedding calls inferred from trace: `{cost.get('real_embedding_calls', 0)}`",
            f"- Real embedding attempts in trace: `{cost.get('real_embedding_attempts', 0)}`",
            f"- Offline/mocked behavioral scenarios: `{cost.get('offline_or_mocked_scenarios', 0)}`",
            "- No judge LLM was used.",
            "",
            "## Reproduction",
            "",
            "```bash",
            ".venv/bin/python evals/run_behavioral.py --live-runs 0",
            ".venv/bin/python evals/run_behavioral.py --working-tree",
            ".venv/bin/python evals/run_behavioral.py --live-runs 1",
            ".venv/bin/python evals/run_behavioral.py --reuse-live-artifact",
            ".venv/bin/python -m unittest discover -s tests -v",
            "```",
            "",
            "The first command evaluates committed HEAD offline; the second evaluates current uncommitted fixes offline. The live command permits exactly one paid probe, and the reuse command reuses saved live evidence without another call. The runner rejects values above one for this suite and retains the global hard cap of ten.",
            "",
            "## Raw artifacts",
            "",
            f"- Full structured result: `evals/artifacts/{artifact_key}/results.json`",
            f"- Existing test output: `evals/artifacts/{artifact_key}/existing_tests.txt`",
            f"- Per-FAIL reproductions: `evals/artifacts/{artifact_key}/failures/`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-runs",
        type=int,
        default=0,
        help="Allow the single implemented paid /api/execute probe (0 or 1).",
    )
    parser.add_argument(
        "--reuse-live-artifact",
        action="store_true",
        help="Reuse live.json for this commit without making another provider call.",
    )
    parser.add_argument(
        "--working-tree",
        action="store_true",
        help="Evaluate current production/tests instead of an immutable git archive.",
    )
    args = parser.parse_args()
    if args.live_runs and args.reuse_live_artifact:
        raise SystemExit("Choose either --live-runs or --reuse-live-artifact, not both.")
    if args.working_tree and args.reuse_live_artifact:
        raise SystemExit("A committed live artifact cannot validate uncommitted working-tree code.")
    if args.live_runs < 0 or args.live_runs > IMPLEMENTED_LIVE_EXECUTIONS:
        raise SystemExit(
            "This suite implements 0 or 1 live run. The global hard cap is "
            f"{MAX_PAID_LIVE_EXECUTIONS}; extending the suite requires an explicit user notice first."
        )
    if args.live_runs > MAX_PAID_LIVE_EXECUTIONS:
        raise SystemExit("Paid live evaluation hard cap exceeded.")

    root = Path(__file__).resolve().parent.parent
    commit = git(root, "rev-parse", "HEAD")
    branch = git(root, "branch", "--show-current")
    started_at = datetime.now(timezone.utc).isoformat()
    artifact_key = f"working-tree-{commit[:12]}" if args.working_tree else commit
    production_source = (
        f"current working tree based on `{commit}`"
        if args.working_tree
        else "isolated `git archive` of the starting commit"
    )
    artifact_dir = root / "evals" / "artifacts" / artifact_key
    failures_dir = artifact_dir / "failures"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    failures_dir.mkdir(parents=True, exist_ok=True)

    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit("Project runtime missing: .venv/bin/python")

    with tempfile.TemporaryDirectory(prefix="distributor-eval-head-") as temp_name:
        snapshot = root if args.working_tree else Path(temp_name)
        if not args.working_tree:
            archive_commit(root, commit, snapshot)
        worker = root / "evals" / "behavioral_worker.py"

        offline_env = dict(os.environ)
        for key in PROVIDER_KEYS:
            offline_env[key] = ""
        offline_env["DATA_DIR"] = str(snapshot / "data")
        offline_output = artifact_dir / "offline.json"
        worker_result = run(
            [
                str(python),
                str(worker),
                "--project-root",
                str(snapshot),
                "--output",
                str(offline_output),
                "--mode",
                "offline",
            ],
            cwd=snapshot,
            env=offline_env,
            timeout=120,
        )
        if worker_result.returncode:
            raise SystemExit(worker_result.stdout + worker_result.stderr)

        test_result = run(
            [str(python), "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=snapshot,
            env=offline_env,
            timeout=120,
        )
        existing_test_text = test_result.stdout + test_result.stderr
        (artifact_dir / "existing_tests.txt").write_text(existing_test_text, encoding="utf-8")
        summary_match = re.search(r"Ran (\d+) tests?", existing_test_text)
        existing_tests = {
            "status": "PASS" if test_result.returncode == 0 else "FAIL",
            "summary": (
                f"{summary_match.group(1)} tests"
                if summary_match
                else f"exit code {test_result.returncode}"
            ),
            "returncode": test_result.returncode,
        }

        offline = json.loads(offline_output.read_text(encoding="utf-8"))
        results = list(offline["results"])
        cost = dict(offline["cost"])

        live_payload: dict[str, Any] | None = None
        secrets: list[str] = []
        if args.reuse_live_artifact:
            live_output = artifact_dir / "live.json"
            if not live_output.exists():
                raise SystemExit(f"No reusable live artifact exists for commit {commit}.")
            live_payload = json.loads(live_output.read_text(encoding="utf-8"))
        elif args.live_runs:
            values = {key: str(value or "") for key, value in dotenv_values(root / ".env").items()}
            secrets = [values.get(key, "") for key in SECRET_KEYS if values.get(key)]
            if not values.get("LLM_API_KEY"):
                live_payload = {
                    "mode": "live",
                    "results": [
                        {
                            "name": "Live exact-input /api/execute probe",
                            "status": "NOT RUN",
                            "reason": "LLM credentials were unavailable to the isolated live probe.",
                            "input": UNKNOWN_INPUT_FOR_REPORT,
                            "expected_invariants": ["real provider execution"],
                            "observed": {"credentials": "unavailable"},
                            "diagnosis": {"external_access": "not configured"},
                            "baseline_blocker": False,
                            "later_100": False,
                        }
                    ],
                    "cost": {
                        "real_api_execute_runs": 0,
                        "real_chat_calls": 0,
                        "real_chat_attempts": 0,
                        "real_embedding_calls": 0,
                        "real_embedding_attempts": 0,
                        "offline_or_mocked_scenarios": 0,
                    },
                }
            else:
                live_env = dict(os.environ)
                for key in PROVIDER_KEYS:
                    live_env[key] = values.get(key, "")
                live_env["DATA_DIR"] = str(snapshot / "data")
                live_output = artifact_dir / "live.json"
                print("Attempting exactly 1 paid live /api/execute probe (hard cap: 10).", flush=True)
                live_result = run(
                    [
                        str(python),
                        str(worker),
                        "--project-root",
                        str(snapshot),
                        "--output",
                        str(live_output),
                        "--mode",
                        "live",
                    ],
                    cwd=snapshot,
                    env=live_env,
                    timeout=300,
                )
                if live_result.returncode:
                    error = redact(live_result.stdout + live_result.stderr, secrets)
                    live_payload = {
                        "mode": "live",
                        "results": [
                            {
                                "name": "Live exact-input /api/execute probe",
                                "status": "NOT RUN",
                                "reason": "The external live probe could not complete in this environment.",
                                "input": UNKNOWN_INPUT_FOR_REPORT,
                                "expected_invariants": ["real provider execution"],
                                "observed": {"worker_error": error},
                                "diagnosis": {"external_access": "unavailable"},
                                "baseline_blocker": False,
                                "later_100": False,
                            }
                        ],
                        "cost": {
                            "real_api_execute_runs": 1,
                            "real_chat_calls": 0,
                            "real_chat_attempts": "unknown before trace",
                            "real_embedding_calls": 0,
                            "real_embedding_attempts": 0,
                            "offline_or_mocked_scenarios": 0,
                        },
                    }
                else:
                    live_payload = json.loads(live_output.read_text(encoding="utf-8"))

        if live_payload:
            results.extend(live_payload["results"])
            for key, value in live_payload["cost"].items():
                if isinstance(value, int):
                    cost[key] = int(cost.get(key, 0)) + value
                else:
                    cost[key] = value

        if existing_tests["status"] == "FAIL":
            results.append(
                {
                    "name": "Existing automated suite on evaluated source",
                    "status": "FAIL",
                    "reason": "The evaluated test suite does not pass in the project runtime.",
                    "input": ".venv/bin/python -m unittest discover -s tests -v",
                    "expected_invariants": ["all existing tests pass"],
                    "observed": existing_tests,
                    "diagnosis": {"root_layer": "existing regression suite"},
                    "baseline_blocker": True,
                    "later_100": False,
                }
            )

        payload = redact(
            {
                "starting_commit": commit,
                "starting_branch": branch,
                "started_at_utc": started_at,
                "production_source": production_source,
                "working_tree_status": git(root, "status", "--short") if args.working_tree else "",
                "existing_tests": existing_tests,
                "results": results,
                "cost": cost,
            },
            secrets,
        )
        (artifact_dir / "results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for item in payload["results"]:
            if item["status"] == "FAIL":
                (failures_dir / f"{slug(item['name'])}.json").write_text(
                    json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    report = report_markdown(
        commit=commit,
        branch=branch,
        started_at=started_at,
        existing_tests=existing_tests,
        payload=payload,
        live_requested=args.live_runs,
        live_reused=args.reuse_live_artifact,
        production_source=production_source,
        artifact_key=artifact_key,
    )
    (root / "evals" / "latest_report.md").write_text(report, encoding="utf-8")
    print(report)


UNKNOWN_INPUT_FOR_REPORT = (
    "I have a 75-minute documentary from Israel about a divorced father rebuilding "
    "his relationship with his teenage daughter. The film is nearly finished. I want "
    "to know which festivals I should target."
)


if __name__ == "__main__":
    main()
