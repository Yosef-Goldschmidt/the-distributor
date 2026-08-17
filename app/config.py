"""Runtime configuration, loaded from environment variables.

Every external dependency is optional: when a credential is missing the agent
falls back to a local implementation so the project always runs.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR") or ROOT_DIR / "data")
PUBLIC_DIR = ROOT_DIR / "public"
ASSETS_DIR = ROOT_DIR / "assets"

load_dotenv(ROOT_DIR / ".env")


def _clean(value: str | None) -> str:
    return (value or "").strip()


# ---------------------------------------------------------------- LLM provider
# LLMod.ai exposes an OpenAI-compatible API. Confirm the exact base URL and
# model id in the LLMod.ai dashboard and set them here.
LLM_BASE_URL = _clean(os.getenv("LLM_BASE_URL")) or "https://api.llmod.ai/v1"
LLM_API_KEY = _clean(os.getenv("LLM_API_KEY"))
LLM_MODEL = _clean(os.getenv("LLM_MODEL")) or "gpt-4o-mini"
LLM_EMBED_MODEL = _clean(os.getenv("LLM_EMBED_MODEL")) or _clean(os.getenv("LLM_EMBEDDING_MODEL"))
LLM_TIMEOUT_SECONDS = float(_clean(os.getenv("LLM_TIMEOUT_SECONDS")) or 90)
# Left unset by default: reasoning models such as gpt-5-mini only accept temperature=1.
_temperature = _clean(os.getenv("LLM_TEMPERATURE"))
LLM_TEMPERATURE = float(_temperature) if _temperature else None
LLM_MAX_OUTPUT_TOKENS = int(_clean(os.getenv("LLM_MAX_OUTPUT_TOKENS")) or 2000)
# Hard ceiling for the automatic retry when reasoning tokens exhaust the budget.
LLM_MAX_OUTPUT_TOKENS_CEILING = int(_clean(os.getenv("LLM_MAX_OUTPUT_TOKENS_CEILING")) or 16000)
# "minimal"/"low"/"medium"/"high" on reasoning models; empty to omit the parameter.
LLM_REASONING_EFFORT = _clean(os.getenv("LLM_REASONING_EFFORT")) or "low"

# ------------------------------------------------------------------- Pinecone
PINECONE_API_KEY = _clean(os.getenv("PINECONE_API_KEY"))
PINECONE_INDEX = _clean(os.getenv("PINECONE_INDEX")) or "the-distributor"
PINECONE_NAMESPACE = _clean(os.getenv("PINECONE_NAMESPACE")) or "festivals"
# "llm" uses the LLMod.ai embedding model; "pinecone" uses Pinecone hosted inference.
EMBED_PROVIDER = (_clean(os.getenv("EMBED_PROVIDER")) or "llm").lower()
PINECONE_EMBED_MODEL = _clean(os.getenv("PINECONE_EMBED_MODEL")) or "multilingual-e5-large"

# ------------------------------------------------------------------- Supabase
SUPABASE_URL = _clean(os.getenv("SUPABASE_URL"))
SUPABASE_KEY = _clean(os.getenv("SUPABASE_SERVICE_KEY")) or _clean(os.getenv("SUPABASE_ANON_KEY"))
COMPANY_ID = _clean(os.getenv("COMPANY_ID")) or "meridian-films"

# ----------------------------------------------------------------- Agent knobs
CANDIDATE_POOL_SIZE = int(_clean(os.getenv("CANDIDATE_POOL_SIZE")) or 16)
MAX_REPLAN_ROUNDS = int(_clean(os.getenv("MAX_REPLAN_ROUNDS")) or 1)
# A revision is skipped past this point so a run stays inside the 300s
# serverless limit even in the worst case.
REVISION_DEADLINE_SECONDS = float(_clean(os.getenv("REVISION_DEADLINE_SECONDS")) or 150)


def llm_enabled() -> bool:
    return bool(LLM_API_KEY)


def pinecone_enabled() -> bool:
    return bool(PINECONE_API_KEY)


def embeddings_enabled() -> bool:
    if EMBED_PROVIDER == "pinecone":
        return bool(PINECONE_API_KEY)
    return bool(LLM_API_KEY and LLM_EMBED_MODEL)


def supabase_enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)
