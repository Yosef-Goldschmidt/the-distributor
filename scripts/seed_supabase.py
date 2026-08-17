"""Push the local seed data into Supabase.

Prerequisites: run scripts/schema.sql in the Supabase SQL editor, then set
SUPABASE_URL and SUPABASE_SERVICE_KEY (service role key — needed to write).

Usage:  python scripts/seed_supabase.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.stores import corpus  # noqa: E402

FESTIVAL_COLUMNS = [
    "id", "name", "city", "country", "region", "tier", "category", "accepts", "themes",
    "month", "festival_dates", "typical_deadline_month", "submission_open", "next_deadline",
    "final_deadline", "status", "premiere_requirement", "premiere_requirement_raw",
    "premiere_territory", "submission_fee", "waiver", "website", "company_previous_films",
    "strategic_value", "focus", "award_patterns", "notable_past_selections", "notes",
    "identity_confidence", "source",
]

COMPANY_COLUMNS = ["id", "name", "country", "profile", "circuit_summary", "films"]

HISTORY_COLUMNS = [
    "company_id", "festival_id", "festival_name", "screenings", "films", "years",
    "awards", "categories", "result", "note",
]


def chunked(items, size):
    for start in range(0, len(items), size):
        yield items[start : start + size]


def main() -> None:
    if not config.supabase_enabled():
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY first.")

    from supabase import create_client

    client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

    festivals = corpus.load_festivals()
    if not festivals:
        raise SystemExit("data/festivals.json is empty.")

    rows = [{column: festival.get(column) for column in FESTIVAL_COLUMNS} for festival in festivals]
    for batch in chunked(rows, 100):
        client.table("festivals").upsert(batch).execute()
    print(f"upserted {len(rows)} festivals")

    company_path = ROOT / "data" / "company.json"
    if not company_path.exists():
        raise SystemExit("data/company.json is missing — run scripts/make_company.py first.")
    seed = json.loads(company_path.read_text(encoding="utf-8"))

    company = {column: seed["company"].get(column) for column in COMPANY_COLUMNS}
    client.table("companies").upsert(company).execute()
    client.table("company_festival_history").delete().eq(
        "company_id", seed["company"]["id"]
    ).execute()
    history = [
        {column: row.get(column) for column in HISTORY_COLUMNS} for row in seed["history"]
    ]
    for batch in chunked(history, 100):
        client.table("company_festival_history").insert(batch).execute()
    print(f"upserted company {company['name']} with {len(history)} history rows")


if __name__ == "__main__":
    main()
