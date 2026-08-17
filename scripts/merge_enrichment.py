"""Merge the curated enrichment parts into data/festivals.json.

Only descriptive fields are merged (focus, award_patterns, notes, themes,
notable_past_selections). Every hard fact from the distributor's workbook —
tier, deadlines, premiere requirements, fees, website — is left untouched.

Usage:  python scripts/merge_enrichment.py
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FESTIVALS = ROOT / "data" / "festivals.json"
PARTS_DIR = ROOT / "data" / "enrichment"

MAX_THEMES = 10


def main() -> None:
    festivals = json.loads(FESTIVALS.read_text(encoding="utf-8"))
    by_id = {festival["id"]: festival for festival in festivals}

    parts = sorted(PARTS_DIR.glob("part_*.json"))
    if not parts:
        raise SystemExit(f"no enrichment parts found in {PARTS_DIR}")

    merged = 0
    unknown: list[str] = []
    confidence: collections.Counter = collections.Counter()

    for path in parts:
        for entry in json.loads(path.read_text(encoding="utf-8")):
            festival = by_id.get(entry.get("id"))
            if festival is None:
                unknown.append(entry.get("id", "?"))
                continue

            festival["focus"] = entry.get("focus") or festival.get("focus")
            festival["award_patterns"] = entry.get("award_patterns") or festival.get("award_patterns")
            festival["notes"] = entry.get("notes") or festival.get("notes")

            selections = [title for title in (entry.get("notable_past_selections") or []) if title]
            if selections:
                festival["notable_past_selections"] = selections[:5]

            themes = list(festival.get("themes") or [])
            for theme in entry.get("themes") or []:
                if theme and theme not in themes:
                    themes.append(theme)
            festival["themes"] = themes[:MAX_THEMES]

            level = (entry.get("confidence") or "unknown").lower()
            festival["identity_confidence"] = level
            festival["identity_source"] = "curated enrichment (descriptive text only)"
            confidence[level] += 1
            merged += 1

    missing = [festival["id"] for festival in festivals if not festival.get("focus")]

    FESTIVALS.write_text(
        json.dumps(festivals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"merged {merged}/{len(festivals)} festivals from {len(parts)} part files")
    print(f"confidence: {dict(confidence)}")
    if unknown:
        print(f"WARNING: {len(unknown)} enrichment ids not found in the corpus: {unknown[:10]}")
    if missing:
        print(f"WARNING: {len(missing)} festivals still have no focus text: {missing[:10]}")


if __name__ == "__main__":
    main()
