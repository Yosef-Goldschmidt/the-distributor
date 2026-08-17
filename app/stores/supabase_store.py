"""Supabase access: structured festival facts and distribution-company memory.

Falls back to the bundled JSON seed files when Supabase is not configured, so
the agent runs identically in development and in production.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from app import config
from app.stores import corpus

_client: Any | None = None


def _supabase():
    global _client
    if _client is None:
        from supabase import create_client

        _client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)
    return _client


@lru_cache(maxsize=1)
def _local_company() -> dict[str, Any]:
    path = config.DATA_DIR / "company.json"
    if not path.exists():
        return {"company": {}, "history": []}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def get_festivals(festival_ids: list[str]) -> tuple[list[dict[str, Any]], str]:
    """Structured festival facts (deadlines, fees, premiere rules, categories)."""

    if config.supabase_enabled():
        try:
            response = (
                _supabase()
                .table("festivals")
                .select("*")
                .in_("id", festival_ids)
                .execute()
            )
            rows = {row["id"]: row for row in (response.data or [])}
            if rows:
                ordered = [rows[fid] for fid in festival_ids if fid in rows]
                if ordered:
                    return ordered, "supabase:festivals"
        except Exception:  # noqa: BLE001 - fall back to the bundled corpus
            pass

    by_id = corpus.festivals_by_id()
    ordered = [by_id[fid] for fid in festival_ids if fid in by_id]
    return ordered, "local_seed:data/festivals.json"


def get_company_memory(festival_ids: list[str]) -> tuple[dict[str, Any], str]:
    """The company profile plus its prior relationship with candidate festivals."""

    if config.supabase_enabled():
        try:
            client = _supabase()
            profile = (
                client.table("companies")
                .select("*")
                .eq("id", config.COMPANY_ID)
                .limit(1)
                .execute()
            )
            history = (
                client.table("company_festival_history")
                .select("*")
                .eq("company_id", config.COMPANY_ID)
                .in_("festival_id", festival_ids)
                .execute()
            )
            if profile.data:
                return (
                    {"company": profile.data[0], "history": history.data or []},
                    "supabase:companies+company_festival_history",
                )
        except Exception:  # noqa: BLE001 - fall back to the bundled seed
            pass

    seed = _local_company()
    history = [row for row in seed.get("history", []) if row.get("festival_id") in festival_ids]
    return {"company": seed.get("company", {}), "history": history}, "local_seed:data/company.json"


def log_run(record: dict[str, Any]) -> None:
    """Best-effort persistence of a run trace; never breaks the request."""

    if not config.supabase_enabled():
        return
    try:
        _supabase().table("agent_runs").insert(record).execute()
    except Exception:  # noqa: BLE001
        pass
