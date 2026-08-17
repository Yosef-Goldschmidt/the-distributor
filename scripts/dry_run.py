"""Dry-run the retrieval path without calling the LLM.

Shows which festivals a film profile pulls back and how large the MatchScorer
prompt would be — useful for tuning CANDIDATE_POOL_SIZE against the budget.

Usage:  python scripts/dry_run.py "environmental documentary about women in Israel"
        python scripts/dry_run.py --format feature_doc "..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.agent import modules  # noqa: E402
from app.stores import corpus, supabase_store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--format", dest="film_format", default="feature_doc")
    parser.add_argument("--pool", type=int, default=config.CANDIDATE_POOL_SIZE)
    args = parser.parse_args()

    config.CANDIDATE_POOL_SIZE = args.pool

    trace = modules.Trace()
    profile = {
        "title": "Dry run",
        "logline": args.query,
        "format": args.film_format,
        "themes": [],
        "search_query": args.query,
    }

    candidates = modules.festival_search(trace, profile)
    memory = modules.company_memory(trace, candidates)
    history = {row["festival_id"]: row for row in memory.get("history", [])}

    print(f"corpus: {len(corpus.load_festivals())} festivals\n")
    print(f"{'#':>2}  {'score':>6}  {'tier':<3} {'name':<48} {'country':<16} history")
    for index, candidate in enumerate(candidates, 1):
        record = history.get(candidate["id"])
        note = f"{record['screenings']} screening(s)" if record else "-"
        print(
            f"{index:>2}  {candidate['retrieval_score']:>6.3f}  {candidate.get('tier',''):<3} "
            f"{(candidate.get('name') or '')[:48]:<48} {(candidate.get('country') or '')[:16]:<16} {note}"
        )

    payload = json.dumps(
        [
            {
                **corpus.compact_for_prompt(candidate),
                "focus": modules._truncate(candidate.get("focus"), 240),
                "notes": modules._truncate(candidate.get("notes"), 160),
            }
            for candidate in candidates
        ],
        ensure_ascii=False,
    )
    print(f"\nMatchScorer candidate payload: {len(payload)} chars (~{len(payload)//4} tokens)")

    festivals, source = supabase_store.get_festivals([c["id"] for c in candidates])
    print(f"facts source: {source} ({len(festivals)} rows)")


if __name__ == "__main__":
    main()
