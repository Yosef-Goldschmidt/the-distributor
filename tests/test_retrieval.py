"""Hybrid retrieval tests: relevance, company memory, prestige and entity deduplication."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import modules  # noqa: E402
from app.stores import supabase_store  # noqa: E402


def festival(festival_id: str, name: str, tier: str, website: str) -> dict:
    return {
        "id": festival_id,
        "name": name,
        "country": "Testland",
        "tier": tier,
        "website": website,
        "accepts": ["feature_doc"],
        "focus": "Environmental documentary",
        "themes": ["environmental"],
    }


class HybridRetrievalTest(unittest.TestCase):
    def test_relevant_relationship_is_reserved_and_duplicate_entity_is_removed(self) -> None:
        records = {
            "semantic": festival("semantic", "Semantic Fit", "B", "https://semantic.test"),
            "duplicate-1": festival("duplicate-1", "Duplicate Festival", "B", "https://duplicate.test/"),
            "duplicate-2": festival("duplicate-2", "Duplicate Festival", "B", "https://duplicate.test/"),
            "prestige": festival("prestige", "Prestige Festival", "A", "https://prestige.test"),
            "relationship": festival("relationship", "Known Partner", "B", "https://partner.test"),
        }

        def get_festivals(ids):  # noqa: ANN001, ANN201
            return [records[festival_id] for festival_id in ids], "fixture"

        memory = {
            "company": {"name": "Test Distributor"},
            "history": [{"festival_id": "relationship", "screenings": 8, "years": [2025]}],
        }
        profile = {
            "format": "feature_doc",
            "search_query": "environmental documentary",
        }
        trace = modules.Trace()

        with patch("app.agent.modules.pinecone_store.search", return_value=(
            [("semantic", 0.95), ("duplicate-1", 0.85), ("duplicate-2", 0.84), ("prestige", 0.3)],
            "pinecone:fixture/vector", None,
        )), patch("app.agent.modules.corpus.lexical_search", return_value=[
            ("relationship", 0.7), ("semantic", 0.6), ("duplicate-1", 0.5),
            ("duplicate-2", 0.5), ("prestige", 0.2),
        ]), patch("app.agent.modules.supabase_store.get_festivals", side_effect=get_festivals), patch(
            "app.agent.modules.corpus.load_festivals", return_value=list(records.values())
        ), patch("app.agent.modules.config.CANDIDATE_POOL_SIZE", 4):
            selected = modules.festival_search(trace, profile, memory)

        selected_ids = {record["id"] for record in selected}
        self.assertIn("relationship", selected_ids)
        self.assertIn("prestige", selected_ids)
        self.assertEqual(len(selected), 4)
        self.assertEqual(
            len(selected_ids & {"duplicate-1", "duplicate-2"}), 1
        )
        relationship = next(record for record in selected if record["id"] == "relationship")
        self.assertIn("company_memory", relationship["retrieval_sources"])
        self.assertGreater(relationship["relationship_strength"], 0)

    def test_local_fallback_is_not_mislabeled_as_semantic(self) -> None:
        records = {
            "local": festival("local", "Local Match", "B", "https://local.test"),
        }
        trace = modules.Trace()
        with patch("app.agent.modules.pinecone_store.search", return_value=(
            [("local", 1.0)], "local_tfidf_fallback", "vector_retrieval_error:TimeoutError",
        )), patch("app.agent.modules.corpus.lexical_search", return_value=[("local", 1.0)]), patch(
            "app.agent.modules.supabase_store.get_festivals",
            return_value=([records["local"]], "local_seed:data/festivals.json (supabase_request_failed)"),
        ), patch("app.agent.modules.corpus.load_festivals", return_value=list(records.values())), patch(
            "app.agent.modules.config.CANDIDATE_POOL_SIZE", 1
        ):
            selected = modules.festival_search(
                trace, {"format": "feature_doc", "search_query": "environmental"},
                {"company": {}, "history": []},
            )

        self.assertEqual(selected[0]["retrieval_sources"], ["lexical"])
        self.assertEqual(selected[0]["retrieval_backend"], "local_tfidf_fallback")
        self.assertIn("supabase_request_failed", selected[0]["facts_source"])

    def test_partial_supabase_rows_are_filled_from_local_seed(self) -> None:
        remote = festival("remote", "Remote Festival", "A", "https://remote.test")
        local = festival("local", "Local Festival", "B", "https://local.test")
        client = MagicMock()
        client.table.return_value.select.return_value.in_.return_value.execute.return_value = (
            SimpleNamespace(data=[remote])
        )
        with patch("app.stores.supabase_store.config.supabase_enabled", return_value=True), patch(
            "app.stores.supabase_store._supabase", return_value=client
        ), patch(
            "app.stores.supabase_store.corpus.festivals_by_id",
            return_value={"remote": remote, "local": local},
        ):
            rows, source = supabase_store.get_festivals(["remote", "local"])

        self.assertEqual([row["id"] for row in rows], ["remote", "local"])
        self.assertIn("supabase:festivals+local_seed", source)


if __name__ == "__main__":
    unittest.main()
