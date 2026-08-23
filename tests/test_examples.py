"""Submission examples must be real outputs of the current architecture."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import prompts, scoring  # noqa: E402


class SubmissionExamplesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.examples = json.loads(
            (ROOT / "data" / "prompt_examples.json").read_text(encoding="utf-8")
        )

    def test_two_complete_examples_are_current_and_domain_consistent(self) -> None:
        self.assertGreaterEqual(len(self.examples), 2)
        canonical = {"Planner", "Executor", *prompts.TASK_CATALOG, "Replanner"}
        llm_modules = {"FilmAnalyzer", "MatchScorer", "RoadmapBuilder"}
        label_to_dimension = {
            label: dimension for dimension, label in scoring.DIMENSION_LABELS.items()
        }

        for example in self.examples:
            self.assertTrue(example.get("prompt"))
            response = example.get("full_response") or ""
            steps = example.get("steps") or []
            modules = [step.get("module") for step in steps]

            self.assertEqual(set(modules), canonical)
            self.assertEqual(modules[:2], ["Planner", "Executor"])
            self.assertLess(modules.index("CompanyMemory"), modules.index("FestivalSearch"))
            self.assertLess(modules.index("RiskChecker"), modules.index("MatchScorer"))
            self.assertEqual(modules[-1], "Replanner")
            self.assertTrue(all(set(step) == {"module", "prompt", "response"} for step in steps))

            provider_modules = {
                step["module"]
                for step in steps
                if isinstance(step.get("prompt"), dict)
                and step["prompt"].get("provider", {}).get("kind") == "chat"
            }
            self.assertEqual(provider_modules, llm_modules)
            chat_steps = [
                step for step in steps
                if isinstance(step.get("prompt"), dict)
                and step["prompt"].get("provider", {}).get("kind") == "chat"
            ]
            chat_counts = {
                module: sum(step["module"] == module for step in chat_steps)
                for module in llm_modules
            }
            self.assertEqual(chat_counts["FilmAnalyzer"], 1)
            self.assertEqual(chat_counts["MatchScorer"], 1)
            self.assertIn(chat_counts["RoadmapBuilder"], {1, 2})
            self.assertLessEqual(len(chat_steps), 4)
            embedding_steps = [
                step for step in steps
                if isinstance(step.get("prompt"), dict)
                and step["prompt"].get("provider", {}).get("kind") == "embedding"
            ]
            self.assertEqual(len(embedding_steps), 1)
            self.assertEqual(embedding_steps[0]["module"], "FestivalSearch")

            llm_score_step = next(
                step for step in steps
                if step["module"] == "MatchScorer" and step["prompt"].get("provider")
            )
            for row in llm_score_step["response"]["scores"]:
                self.assertEqual(set(row["ratings"]), set(scoring.LLM_DIMENSIONS))
                self.assertEqual(set(row["evidence"]), set(scoring.LLM_DIMENSIONS))

            deterministic_score = next(
                step for step in steps
                if step["module"] == "MatchScorer"
                and step["prompt"].get("operation") == "deterministic_weighted_score"
            )
            validation = deterministic_score["response"]["llm_output_validation"]
            self.assertEqual(
                validation,
                {
                    "missing_ids": [], "unknown_ids": [], "duplicate_ids": [],
                    "invalid_rows": 0, "invalid_score_rows": [],
                },
            )

            search_step = next(
                step for step in steps
                if step["module"] == "FestivalSearch"
                and isinstance(step.get("response"), dict)
                and isinstance(step["response"].get("festivals"), list)
            )
            retrieved = search_step["response"]["festivals"]
            self.assertEqual(len(retrieved), len({row["id"] for row in retrieved}))
            self.assertEqual(len(retrieved), len({row["name"] for row in retrieved}))

            roadmap_step = next(
                step for step in steps
                if step["module"] == "RoadmapBuilder" and step["prompt"].get("provider")
            )
            roadmap_payload = json.loads(roadmap_step["prompt"]["user"])
            festivals = {row["name"]: row for row in roadmap_payload["festivals"]}
            self.assertEqual(set(festivals), {row["name"] for row in retrieved})

            replanner = next(step for step in reversed(steps) if step["module"] == "Replanner")
            self.assertEqual(replanner["response"]["decision"], "accept")
            self.assertTrue(
                all(not value for value in replanner["response"]["defects"].values())
            )

            self.assertIn("## Premiere Strategy", response)
            self.assertIn("**Grounding:**", response)
            self.assertIn("**Source / submission page:**", response)
            self.assertIn("**Score calculation:**", response)
            self.assertNotIn("deadline ~", response)
            self.assertRegex(response, r"deadline \d{4}-\d{2}-\d{2}")

            target_name = (roadmap_payload.get("recommended_premiere_target") or {}).get("name")
            if target_name:
                calendar = response.split("## Submission Calendar", 1)[1].split(
                    "## Next Actions", 1
                )[0]
                self.assertIn(target_name, calendar)
                self.assertIn("first public festival screening", response)
                allowed_sequence_roles = {
                    "target", "must_follow_target", "alternative_only", "verify"
                }
                self.assertTrue(all(
                    row.get("premiere_sequence", {}).get("status")
                    in allowed_sequence_roles
                    for row in roadmap_payload["festivals"]
                ))
                self.assertNotIn("must_precede_target", response)
                if any(
                    row.get("premiere_sequence", {}).get("status")
                    == "must_follow_target"
                    for row in roadmap_payload["festivals"]
                ):
                    self.assertIn("Premiere sequence (must_follow_target)", response)
                    self.assertIn("do not accept a screening before", response.lower())

            if chat_counts["RoadmapBuilder"] == 2:
                first_replanner = next(
                    step for step in steps if step["module"] == "Replanner"
                )
                self.assertEqual(first_replanner["response"]["decision"], "revise")
                self.assertTrue(any(first_replanner["response"]["defects"].values()))

            festival_headings = re.findall(r"^### (.+?)(?: — \d+/100)?$", response, re.M)
            self.assertGreaterEqual(len(festival_headings), 8)
            self.assertEqual(len(festival_headings), len(set(festival_headings)))

            sections = re.split(r"^### ", response, flags=re.M)[1:]
            for section in sections:
                heading = section.splitlines()[0]
                match = re.match(r"(.+?) — (\d+)/100$", heading)
                self.assertIsNotNone(match, heading)
                name, displayed_score = match.group(1), int(match.group(2))
                festival = festivals[name]

                breakdown_line = next(
                    line for line in section.splitlines()
                    if line.startswith("- **Score breakdown:**")
                )
                ratings = {}
                for part in breakdown_line.split(":** ", 1)[1].split(", "):
                    label, value = part.rsplit(" ", 1)
                    ratings[label_to_dimension[label]] = float(value.split("/")[0])
                computed_score = scoring.compute_score(
                    ratings, festival["premiere_risk"]
                )["score"]
                self.assertEqual(displayed_score, festival["score"], name)
                self.assertEqual(displayed_score, computed_score, name)

                deadline = festival.get("deadline") or {}
                if deadline.get("next_submission_open") and deadline.get("next_deadline"):
                    self.assertLess(
                        deadline["next_submission_open"], deadline["next_deadline"], name
                    )
                if deadline.get("is_projection"):
                    self.assertIn("(projected)", section, name)
                    self.assertIn("Verify the projected date", section, name)
                sequence = festival.get("premiere_sequence") or {}
                if sequence.get("status") == "alternative_only":
                    self.assertIn("alternative premiere path only", section.lower(), name)
                if festival.get("eligibility_issue"):
                    self.assertIn("**Eligibility:** ineligible", section, name)

            by_id = {row["id"]: row for row in roadmap_payload["festivals"]}
            if "this-human-world" in by_id:
                self.assertEqual(
                    by_id["this-human-world"]["premiere_constraint"]["territory"], "Austria"
                )
            corrected_id = "golden-apricot-yerenan-international-film-festival"
            if corrected_id in by_id:
                self.assertIn("Yerevan", by_id[corrected_id]["name"])


if __name__ == "__main__":
    unittest.main()
