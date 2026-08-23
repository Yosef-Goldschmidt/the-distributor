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
        from supabase.lib.client_options import SyncClientOptions

        _client = create_client(
            config.SUPABASE_URL,
            config.SUPABASE_KEY,
            options=SyncClientOptions(
                postgrest_client_timeout=config.SUPABASE_TIMEOUT_SECONDS
            ),
        )
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

    local = corpus.festivals_by_id()
    fallback_reason = "supabase_not_configured"
    if config.supabase_enabled():
        try:
            response = (
                _supabase()
                .table("festivals")
                .select("*")
                .in_("id", festival_ids)
                .execute()
            )
            rows = {row["id"]: row for row in (response.data or []) if row.get("id")}
            if rows:
                missing = [fid for fid in festival_ids if fid not in rows and fid in local]
                ordered = [rows.get(fid) or local.get(fid) for fid in festival_ids]
                ordered = [row for row in ordered if row]
                if ordered:
                    source = "supabase:festivals"
                    if missing:
                        source += f"+local_seed:data/festivals.json ({len(missing)} missing rows)"
                    return ordered, source
            fallback_reason = "supabase_returned_no_rows"
        except Exception:  # noqa: BLE001 - fall back to the bundled corpus
            fallback_reason = "supabase_request_failed"

    ordered = [local[fid] for fid in festival_ids if fid in local]
    return ordered, f"local_seed:data/festivals.json ({fallback_reason})"


def get_company_memory(festival_ids: list[str] | None = None) -> tuple[dict[str, Any], str]:
    """Company profile plus either all history or selected festival relationships."""

    fallback_reason = "supabase_not_configured"
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
            history_query = (
                client.table("company_festival_history")
                .select("*")
                .eq("company_id", config.COMPANY_ID)
            )
            if festival_ids is not None:
                if not festival_ids:
                    history_rows = []
                else:
                    history_rows = history_query.in_("festival_id", festival_ids).execute().data or []
            else:
                history_rows = history_query.execute().data or []
            if profile.data:
                return (
                    {"company": profile.data[0], "history": history_rows},
                    "supabase:companies+company_festival_history",
                )
            fallback_reason = "supabase_company_not_found"
        except Exception:  # noqa: BLE001 - fall back to the bundled seed
            fallback_reason = "supabase_request_failed"

    seed = _local_company()
    history = seed.get("history", [])
    if festival_ids is not None:
        wanted = set(festival_ids)
        history = [row for row in history if row.get("festival_id") in wanted]
    return (
        {"company": seed.get("company", {}), "history": history},
        f"local_seed:data/company.json ({fallback_reason})",
    )


def log_run(record: dict[str, Any]) -> None:
    """Best-effort persistence of a run trace; never breaks the request."""

    if not config.supabase_enabled():
        return
    try:
        _supabase().table("agent_runs").insert(record).execute()
    except Exception:  # noqa: BLE001
        pass
