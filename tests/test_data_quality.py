"""Corpus-wide invariants for data quality and deterministic interpretation."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import domain, modules  # noqa: E402


class CorpusQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.festivals = json.loads(
            (ROOT / "data" / "festivals.json").read_text(encoding="utf-8")
        )
        cls.company = json.loads(
            (ROOT / "data" / "company.json").read_text(encoding="utf-8")
        )

    def test_corpus_has_unique_ids_and_required_retrieval_fields(self) -> None:
        self.assertGreaterEqual(len(self.festivals), 300)
        ids = [festival.get("id") for festival in self.festivals]
        self.assertEqual(len(ids), len(set(ids)))
        for festival in self.festivals:
            for field in ("id", "name", "tier", "accepts", "focus"):
                self.assertIsNotNone(festival.get(field), f"{festival.get('id')}: {field}")

    def test_all_structured_dates_are_iso_dates(self) -> None:
        for festival in self.festivals:
            for field in ("submission_open", "next_deadline", "final_deadline"):
                value = festival.get(field)
                if value:
                    date.fromisoformat(value)

    def test_exact_final_dates_always_override_typical_month(self) -> None:
        reference_date = date(2026, 8, 23)
        for festival in self.festivals:
            if festival.get("final_deadline"):
                assessment = domain.assess_deadline(festival, reference_date)
                self.assertNotEqual(assessment["basis"], "typical_month_only")
                projected_open = assessment.get("next_submission_open")
                projected_final = assessment.get("next_deadline")
                if assessment["is_projection"] and projected_open and projected_final:
                    self.assertLess(
                        projected_open, projected_final, festival.get("id")
                    )

    def test_premiere_shorthand_is_not_overstated(self) -> None:
        for festival in self.festivals:
            raw = festival.get("premiere_requirement_raw") or ""
            constraint = domain.premiere_constraint(festival)
            if raw.lower().startswith("world -"):
                self.assertNotEqual(constraint["scope"], "world", festival.get("id"))
            if raw.lower() in {"world", "world premiere"}:
                self.assertEqual(constraint["scope"], "world", festival.get("id"))

    def test_company_history_references_known_festivals(self) -> None:
        festival_ids = {festival["id"] for festival in self.festivals}
        missing = [
            row.get("festival_id")
            for row in self.company.get("history", [])
            if row.get("festival_id") not in festival_ids
        ]
        self.assertEqual(missing, [])

    def test_known_duplicate_entity_has_one_runtime_identity(self) -> None:
        ale_kino = [
            festival for festival in self.festivals
            if festival.get("name") == "International Young Audience Film Festival Ale Kino!"
        ]
        self.assertEqual(len(ale_kino), 2)
        self.assertEqual(
            len({modules._entity_key(festival) for festival in ale_kino}), 1
        )


if __name__ == "__main__":
    unittest.main()
