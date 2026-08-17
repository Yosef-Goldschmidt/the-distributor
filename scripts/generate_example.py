"""Run the agent on sample films and store the results as /api/agent_info examples.

Requires live LLMod.ai credentials. Costs a few cents per example.

Usage:  python scripts/generate_example.py [--examples 2]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.agent import graph  # noqa: E402

PROMPTS = [
    """Title: Salt and Ash
Format: feature documentary
Country / Language: Israel / Hebrew, Arabic
Runtime: 89 minutes
Director: Noa Ben-Ari, second feature; her debut short played Clermont-Ferrand
Synopsis: In a shrinking Dead Sea village, three women who have worked the salt flats for thirty years fight a government plan to relocate them, while the ground literally collapses beneath their homes.
Themes: environmental collapse, women's labour, displacement, community resistance
Premiere status: no premiere yet
Target audience: documentary and human-rights festival audiences, environmental programmers
Goal: build a festival strategy for the next 12 months""",
    """Title: The Night Shift
Format: feature fiction
Country / Language: Georgia / Georgian, Russian
Runtime: 104 minutes
Director: Levan Kiknadze, debut feature; graduated from the Tbilisi film school
Synopsis: A young hospital porter working nights in Tbilisi starts trading favours for medicine on the black market, until a patient's death forces him to choose between his brother and the truth.
Themes: first feature, social realism, post-Soviet economy, moral compromise, brotherhood
Premiere status: no premiere yet
Target audience: European arthouse audiences, first-feature and emerging-director sections
Goal: identify realistic A-list targets and a fallback plan if we miss the spring deadlines""",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", type=int, default=2, choices=[1, 2])
    args = parser.parse_args()

    if not config.llm_enabled():
        raise SystemExit("Set LLM_API_KEY (and LLM_BASE_URL / LLM_MODEL) first.")

    examples = []
    for prompt in PROMPTS[: args.examples]:
        print(f"running: {prompt.splitlines()[0]}")
        result = graph.run(prompt)
        usage = result["meta"]["llm_usage"]
        print(f"  {usage['calls']} LLM calls, {len(result['steps'])} steps")
        examples.append(
            {
                "prompt": prompt,
                "full_response": result["response"],
                "steps": result["steps"],
            }
        )

    out = ROOT / "data" / "prompt_examples.json"
    out.write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
