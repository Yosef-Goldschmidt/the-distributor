"""Focused tests for deterministic festival-distribution rules."""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import domain, modules, scoring  # noqa: E402


class DeadlineSemanticsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        festivals = json.loads((ROOT / "data" / "festivals.json").read_text(encoding="utf-8"))
        cls.by_id = {festival["id"]: festival for festival in festivals}

    def test_stale_shorthand_does_not_override_documentary_edge_final_date(self) -> None:
        result = domain.assess_deadline(
            self.by_id["documentary-edge-iff"], date(2026, 8, 17)
        )
        self.assertEqual(result["status"], "upcoming")
        self.assertEqual(result["next_deadline"], "2027-01-30")
        self.assertEqual(result["next_submission_open"], "2026-09-01")
        self.assertEqual(result["basis"], "projected_annual_pattern")

    def test_missed_molodist_cycle_is_upcoming_until_next_window_opens(self) -> None:
        result = domain.assess_deadline(
            self.by_id["the-molodist-kyiv-iff"], date(2026, 8, 17)
        )
        self.assertEqual(result["status"], "upcoming")
        self.assertEqual(result["recorded_final_deadline"], "2026-07-15")
        self.assertEqual(result["next_deadline"], "2027-07-15")
        self.assertTrue(result["recorded_cycle_closed"])
        self.assertGreater(result["days_until_open"], 42)

    def test_deadline_status_has_no_arbitrary_ninety_day_discontinuity(self) -> None:
        festival = {
            "submission_open": "2026-10-01",
            "final_deadline": "2026-06-01",
        }
        before = domain.assess_deadline(festival, date(2026, 8, 30))
        after = domain.assess_deadline(festival, date(2026, 8, 31))
        self.assertEqual(before["status"], "upcoming")
        self.assertEqual(after["status"], "upcoming")
        self.assertEqual(before["days_until_open"] - after["days_until_open"], 1)

    def test_zero_length_submission_window_is_not_projected_as_fact(self) -> None:
        result = domain.assess_deadline(
            {"submission_open": "2026-06-01", "final_deadline": "2026-06-01"},
            date(2026, 8, 23),
        )
        self.assertIsNone(result["next_submission_open"])
        self.assertEqual(result["confidence"], "low")
        self.assertIn("submission_open_not_before_final_deadline", result["cycle_anomalies"])

    def test_stale_open_date_does_not_make_future_cycle_look_open(self) -> None:
        result = domain.assess_deadline(
            {
                "submission_open": "2025-06-01",
                "final_deadline": "2026-10-06",
            },
            date(2026, 8, 23),
        )

        self.assertEqual(result["status"], "upcoming")
        self.assertEqual(result["confidence"], "low")
        self.assertIsNone(result["next_submission_open"])
        self.assertIn(
            "submission_open_outside_recorded_deadline_cycle",
            result["cycle_anomalies"],
        )

    def test_exact_upcoming_deadline_is_not_projected(self) -> None:
        festival = {
            "final_deadline": "2026-09-01",
            "submission_open": "2026-05-01",
            "typical_deadline_month": "January",
        }
        result = domain.assess_deadline(festival, date(2026, 8, 17))
        self.assertEqual(result["status"], "closing_soon")
        self.assertEqual(result["basis"], "recorded_final_deadline")
        self.assertFalse(result["is_projection"])

    def test_month_only_is_low_confidence(self) -> None:
        result = domain.assess_deadline(
            {"typical_deadline_month": "September"}, date(2026, 8, 17)
        )
        self.assertEqual(result["status"], "upcoming")
        self.assertEqual(result["confidence"], "low")
        self.assertIsNone(result["next_deadline"])
        self.assertIsNone(result["days_until_deadline"])

    def test_month_only_never_invents_a_mid_month_deadline(self) -> None:
        early = domain.assess_deadline(
            {"typical_deadline_month": "May"}, date(2026, 4, 1)
        )
        late = domain.assess_deadline(
            {"typical_deadline_month": "May"}, date(2026, 4, 30)
        )

        self.assertEqual(early["status"], "upcoming")
        self.assertEqual(early["urgency"], late["urgency"])
        self.assertEqual(early["months_until_typical_deadline"], 1)

    def test_stale_cross_year_cycle_is_aligned_to_projected_final(self) -> None:
        result = domain.assess_deadline(
            {
                "submission_open": "2023-09-10",
                "next_deadline": "2024-12-15",
                "final_deadline": "2023-05-01",
            },
            date(2026, 8, 23),
        )
        self.assertEqual(result["next_deadline"], "2027-05-01")
        self.assertEqual(result["next_submission_open"], "2026-09-10")
        self.assertLess(result["next_submission_open"], result["next_deadline"])


class PremiereSemanticsTest(unittest.TestCase):
    def test_world_to_country_shorthand_is_not_overstated_as_strict_world(self) -> None:
        result = domain.premiere_constraint(
            {"premiere_requirement": "world", "premiere_requirement_raw": "World - Spain"}
        )
        self.assertEqual(result["scope"], "territorial")
        self.assertEqual(result["territory"], "Spain")
        self.assertEqual(result["confidence"], "medium")

    def test_explicit_world_is_strict(self) -> None:
        result = domain.premiere_constraint(
            {"premiere_requirement": "world", "premiere_requirement_raw": "World"}
        )
        self.assertEqual(result["scope"], "world")
        self.assertEqual(result["confidence"], "high")

    def test_already_premiered_film_cannot_meet_strict_world_requirement(self) -> None:
        result = domain.assess_premiere(
            {"format": "feature_doc", "premiere_status": "already_premiered"},
            {
                "accepts": ["feature_doc"],
                "premiere_requirement": "world",
                "premiere_requirement_raw": "World",
            },
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["premiere_risk"], "high")

    def test_format_ineligibility_wins_over_premiere_availability(self) -> None:
        result = domain.assess_premiere(
            {"format": "feature_doc", "premiere_status": "world_premiere_available"},
            {
                "accepts": ["feature_fiction"],
                "premiere_requirement": "none",
                "premiere_requirement_raw": "No Requirements",
            },
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["premiere_risk"], "none")
        self.assertEqual(result["eligibility_issue"], "format_not_accepted")

    def test_descriptive_runtime_range_warns_without_claiming_ineligibility(self) -> None:
        result = domain.assess_premiere(
            {
                "format": "feature_fiction",
                "runtime_minutes": 104,
                "premiere_status": "world_premiere_available",
            },
            {
                "accepts": ["feature_fiction"],
                "focus": "Festival for medium-length work.",
                "notes": "Excellent niche target for 30- to 60-minute work.",
                "premiere_requirement_raw": "No Requirements",
            },
        )
        self.assertTrue(result["eligible"])
        self.assertIsNone(result["eligibility_issue"])
        self.assertEqual(result["runtime_constraint"]["maximum_minutes"], 60)
        self.assertIn("not treated as an official eligibility rule", result["runtime_warning"])

    def test_feature_fiction_with_supported_format_remains_eligible(self) -> None:
        result = domain.assess_premiere(
            {
                "format": "feature_fiction",
                "runtime_minutes": 104,
                "premiere_status": "world_premiere_available",
            },
            {
                "accepts": ["feature_fiction"],
                "focus": "Arthouse feature fiction.",
                "notes": "Programming commonly includes 90- to 120-minute work.",
                "premiere_requirement_raw": "No Requirements",
            },
        )
        self.assertTrue(result["eligible"])
        self.assertIsNone(result["runtime_warning"])

    def test_unknown_premiere_status_stays_uncertain_and_selects_no_target(self) -> None:
        assessment = domain.assess_candidate(
            {"format": "feature_fiction", "premiere_status": "unknown"},
            {
                "id": "festival",
                "accepts": ["feature_fiction"],
                "premiere_requirement_raw": "No Info",
                "final_deadline": "2026-10-01",
            },
            date(2026, 8, 25),
        )
        ranked = [{
            "id": "festival", "name": "Festival", "country": "France",
            "region": "Western Europe", "tier": "A", "score": 85,
            "bucket": "submit_first", "eligible": assessment["eligible"],
            "deadline_status": assessment["deadline_status"],
            "premiere_opportunity": assessment["premiere_opportunity"],
            "premiere_constraint": assessment["premiere_constraint"],
            "ratings": {"strategic_value": 5},
        }]

        target = modules.apply_premiere_strategy(
            {"premiere_status": "unknown", "country": "Georgia"}, ranked
        )

        self.assertEqual(assessment["premiere_risk"], "medium")
        self.assertTrue(assessment["uncertainties"])
        self.assertIsNone(target)
        self.assertEqual(
            ranked[0]["premiere_sequence"]["status"], "not_applicable"
        )

    def test_known_source_typos_are_normalized_without_mutating_source(self) -> None:
        source = {
            "id": "golden-apricot-yerenan-international-film-festival",
            "name": "Golden Apricot Yerenan International Film Festival",
        }
        normalized = domain.normalise_festival_facts(source)
        self.assertEqual(
            normalized["name"], "Golden Apricot Yerevan International Film Festival"
        )
        self.assertEqual(source["name"], "Golden Apricot Yerenan International Film Festival")
        territory = domain.premiere_constraint(
            {"premiere_requirement_raw": "World - Austia"}
        )
        self.assertEqual(territory["territory"], "Austria")

    def test_screening_history_consumes_matching_territorial_premiere(self) -> None:
        result = domain.assess_premiere(
            {
                "format": "feature_doc",
                "country": "Israel",
                "premiere_status": "already_premiered",
                "premiere_history": [{"festival": "Test", "country": "Spain"}],
            },
            {
                "country": "Spain",
                "accepts": ["feature_doc"],
                "premiere_requirement": "world",
                "premiere_requirement_raw": "World - Spain",
            },
        )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["premiere_risk"], "high")

    def test_screening_elsewhere_can_preserve_territorial_premiere(self) -> None:
        result = domain.assess_premiere(
            {
                "format": "feature_doc",
                "country": "Israel",
                "premiere_status": "already_premiered",
                "premiere_history": [{"festival": "Test", "country": "France"}],
            },
            {
                "country": "Spain",
                "accepts": ["feature_doc"],
                "premiere_requirement": "world",
                "premiere_requirement_raw": "World - Spain",
            },
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["premiere_risk"], "medium")

    def test_film_profile_validation_resolves_screening_history_conflict(self) -> None:
        class StubLLM:
            def complete_json(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
                return {
                    "title": "Test",
                    "format": "unsupported",
                    "premiere_status": "world_premiere_available",
                    "premiere_history": [{"festival": "Prior Festival", "country": "France"}],
                    "genres": "documentary",
                    "themes": [],
                    "festival_angles": [],
                    "missing_info": None,
                }

        profile = modules.film_analyzer(StubLLM(), modules.Trace(), "test")
        self.assertIsNone(profile["format"])
        self.assertEqual(profile["premiere_status"], "already_premiered")
        self.assertIn("format", profile["missing_info"])
        self.assertFalse(profile["_validation"]["valid"])


class ExplainableScoringTest(unittest.TestCase):
    def test_company_relationship_is_computed_from_history(self) -> None:
        rating, evidence, facts = scoring.company_relationship_rating(
            [{"screenings": 20, "years": [2012, 2025], "awards": [{"award": "Best Film"}]}],
            2026,
        )
        self.assertEqual(rating, 5.0)
        self.assertIn("20 recorded screening", evidence)
        self.assertEqual(facts["award_count"], 1)

    def test_low_confidence_lineup_rating_is_capped_in_code(self) -> None:
        ratings, evidence, meta = scoring.apply_rating_guardrails(
            {
                "thematic_fit": 5,
                "genre_fit": 5,
                "lineup_similarity": 5,
                "strategic_value": 5,
            },
            {
                "thematic_fit": "supported theme",
                "genre_fit": "supported genre",
                "lineup_similarity": "unsupported assertion",
                "strategic_value": "supported strategy",
            },
            {"identity_confidence": "low", "tier": "C"},
            (0.0, "No prior company relationship is recorded.", {}),
        )
        self.assertEqual(ratings["lineup_similarity"], 2.0)
        self.assertEqual(ratings["strategic_value"], 3.0)
        self.assertEqual(ratings["company_relationship"], 0.0)
        self.assertEqual(len(meta["adjustments"]), 2)
        self.assertIn("Capped", evidence["lineup_similarity"])

    def test_rating_without_evidence_is_zeroed(self) -> None:
        ratings, evidence, meta = scoring.apply_rating_guardrails(
            {
                "thematic_fit": 5,
                "genre_fit": 4,
                "lineup_similarity": 3,
                "strategic_value": 4,
            },
            {"thematic_fit": "Supported by supplied themes"},
            {"identity_confidence": "high", "tier": "A"},
            (0.0, "No prior company relationship is recorded.", {}),
        )
        self.assertEqual(ratings["thematic_fit"], 5.0)
        self.assertEqual(ratings["genre_fit"], 0.0)
        self.assertEqual(ratings["lineup_similarity"], 0.0)
        self.assertEqual(ratings["strategic_value"], 0.0)
        self.assertIn("No grounded evidence", evidence["genre_fit"])
        self.assertEqual(len(meta["adjustments"]), 3)

    def test_company_memory_changes_the_computed_decision_score(self) -> None:
        creative = {
            "thematic_fit": 4, "genre_fit": 4, "lineup_similarity": 3,
            "strategic_value": 3, "deadline_urgency": 3,
        }
        no_history, _, _ = scoring.company_relationship_rating([], 2026)
        strong_history, _, _ = scoring.company_relationship_rating(
            [{"screenings": 10, "years": [2025], "awards": [{"award": "Best Film"}]}],
            2026,
        )
        without_memory = scoring.compute_score(
            {**creative, "company_relationship": no_history}, "none"
        )["score"]
        with_memory = scoring.compute_score(
            {**creative, "company_relationship": strong_history}, "none"
        )["score"]
        self.assertGreater(with_memory, without_memory)
        self.assertGreaterEqual(with_memory - without_memory, 10)

    def test_match_scorer_repairs_structurally_invalid_llm_output_once(self) -> None:
        class RepairingLLM:
            def __init__(self) -> None:
                self.calls = 0

            def complete_json(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
                self.calls += 1
                if self.calls == 1:
                    return {"scores": [{"id": "festival", "ratings": {}, "evidence": {}}]}
                return {
                    "scores": [{
                        "id": "festival",
                        "ratings": {dimension: 3 for dimension in scoring.LLM_DIMENSIONS},
                        "evidence": {
                            dimension: "Supplied evidence" for dimension in scoring.LLM_DIMENSIONS
                        },
                    }]
                }

        llm = RepairingLLM()
        trace = modules.Trace()
        result = modules.match_scorer(
            llm,
            trace,
            {"title": "Test", "format": "feature_doc"},
            [{
                "id": "festival", "name": "Festival", "tier": "B",
                "identity_confidence": "high", "accepts": ["feature_doc"],
            }],
            {"company": {}, "history": []},
        )
        self.assertEqual(llm.calls, 2)
        self.assertIn("festival", result)
        self.assertEqual(
            trace.steps[0]["prompt"]["operation"],
            "deterministic_llm_output_validation",
        )


class RoadmapValidationTest(unittest.TestCase):
    def test_no_premiere_target_does_not_mark_every_festival_as_target(self) -> None:
        compatibility = domain.post_target_compatibility(
            {"id": "a", "premiere_constraint": {"scope": "none"}}, None, "Israel"
        )
        self.assertEqual(compatibility["status"], "not_applicable")

    def test_earlier_screening_cannot_consume_continental_premiere_target(self) -> None:
        target = {
            "id": "tallinn",
            "country": "Estonia",
            "region": "Northern Europe",
            "premiere_constraint": {"scope": "continental", "territory": "Europe"},
        }
        european = domain.pre_target_compatibility(
            {"id": "thessaloniki", "country": "Greece", "region": "Eastern Europe"},
            target,
            "Georgia",
        )
        outside = domain.pre_target_compatibility(
            {"id": "toronto", "country": "Canada", "region": "North America"},
            target,
            "Georgia",
        )

        self.assertEqual(european["status"], "must_follow_target")
        self.assertEqual(outside["status"], "compatible_before_target")

    def test_malformed_llm_roadmap_is_repaired_to_exact_invariants(self) -> None:
        ranked = [
            {
                "id": "a", "name": "Festival A", "bucket": "submit_first",
                "deadline_status": "open", "deadline": {"next_deadline": "2026-10-01"},
                "evidence": {"thematic_fit": "Strong supplied theme match"},
                "ratings": {"thematic_fit": 4},
                "post_target_compatibility": {"status": "target"},
            },
            {
                "id": "b", "name": "Festival B", "bucket": "hold_avoid",
                "deadline_status": "closed", "deadline": {"next_deadline": "2027-02-01", "is_projection": True},
                "evidence": {"genre_fit": "Weak supplied genre match"},
                "ratings": {"genre_fit": 1},
                "post_target_compatibility": {"status": "backup_only"},
                "uncertainties": ["verify"],
            },
        ]
        malformed = {
            "premiere_target": "a",
            "buckets": {"submit_first": [{"id": "b"}, {"id": "b"}], "hold_avoid": "bad"},
            "next_actions": "bad",
        }
        target = {"id": "a", "name": "Festival A", "reason": "Best viable target."}
        trace = modules.Trace()
        first = modules.replanner(trace, ranked, malformed, target)
        self.assertEqual(first["decision"], "revise")
        self.assertGreater(first["defects"]["malformed_entries"], 0)

        repaired = modules.normalise_roadmap(malformed, ranked, target)
        final = modules.replanner(trace, ranked, repaired, target)
        self.assertEqual(final["decision"], "accept")
        ids = [
            entry["id"]
            for entries in repaired["buckets"].values()
            for entry in entries
        ]
        self.assertEqual(ids, ["a", "b"])
        self.assertEqual(len(repaired["next_actions"]), 3)

    def test_closed_alternative_action_preserves_premiere_sequence(self) -> None:
        roadmap = modules.normalise_roadmap(
            {"buckets": {}, "next_actions": []},
            [{
                "id": "backup", "name": "Backup Festival", "bucket": "prioritize_next",
                "score": 75, "eligible": True, "premiere_risk": "none",
                "premiere_opportunity": True,
                "deadline_status": "closed",
                "deadline": {"next_deadline": "2027-08-17", "is_projection": True},
                "evidence": {"strategic_value": "Strong backup value"},
                "ratings": {"strategic_value": 4},
                "post_target_compatibility": {"status": "backup_only"},
            }],
            {"id": "target", "name": "Target Festival"},
        )
        action = roadmap["buckets"]["prioritize_next"][0]["action"]
        self.assertIn("alternative premiere path only", action)
        self.assertIn("next projected cycle", action)
        self.assertIn("Verify the projected date", action)

    def test_candidate_that_cannot_follow_is_an_alternative_to_first_screening_target(self) -> None:
        can_precede = modules.premiere_sequence_role({
            "post_target_compatibility": {"status": "backup_only", "reason": "Cannot follow."},
            "pre_target_compatibility": {
                "status": "compatible_before_target", "reason": "Can precede."
            },
        })
        alternative = modules.premiere_sequence_role({
            "post_target_compatibility": {"status": "backup_only", "reason": "Cannot follow."},
            "pre_target_compatibility": {
                "status": "must_follow_target", "reason": "Cannot precede."
            },
        })

        self.assertEqual(can_precede["status"], "alternative_only")
        self.assertEqual(alternative["status"], "alternative_only")

    def test_submit_first_alternative_still_has_a_submission_action(self) -> None:
        roadmap = modules.normalise_roadmap(
            {"buckets": {}},
            [{
                "id": "backup", "name": "Backup Festival", "bucket": "submit_first",
                "score": 80, "eligible": True, "premiere_risk": "none",
                "premiere_opportunity": True, "deadline_status": "open",
                "deadline": {"next_deadline": "2026-11-01"},
                "evidence": {"strategic_value": "Strong alternative launch value"},
                "ratings": {"strategic_value": 4},
                "post_target_compatibility": {"status": "backup_only"},
                "pre_target_compatibility": {"status": "must_follow_target"},
            }],
            {"id": "target", "name": "Target Festival"},
        )

        action = roadmap["buckets"]["submit_first"][0]["action"]
        self.assertIn("Submit in the first wave as an alternative", action)
        self.assertIn("unless the selected target is abandoned", action)

    def test_low_score_hold_action_never_tells_user_to_prepare(self) -> None:
        roadmap = modules.normalise_roadmap(
            {"buckets": {}},
            [{
                "id": "weak", "name": "Weak Festival", "bucket": "hold_avoid",
                "score": 30, "eligible": True, "premiere_risk": "none",
                "premiere_opportunity": False, "deadline_status": "upcoming",
                "deadline": {"next_deadline": "2027-05-01", "is_projection": True},
                "evidence": {"thematic_fit": "Weak fit"},
                "ratings": {"thematic_fit": 1},
                "post_target_compatibility": {"status": "not_applicable"},
            }],
            None,
        )
        action = roadmap["buckets"]["hold_avoid"][0]["action"]
        self.assertIn("Do not prioritize", action)
        self.assertNotIn("Prepare", action)

    def test_action_preserves_a_later_premiere_target(self) -> None:
        roadmap = modules.normalise_roadmap(
            {"buckets": {}},
            [{
                "id": "earlier", "name": "Earlier Festival", "bucket": "submit_first",
                "score": 80, "eligible": True, "premiere_risk": "none",
                "premiere_opportunity": True, "deadline_status": "open",
                "deadline": {"next_deadline": "2026-11-01"},
                "evidence": {"strategic_value": "Strong strategic value"},
                "ratings": {"strategic_value": 4},
                "post_target_compatibility": {"status": "compatible"},
                "pre_target_compatibility": {"status": "must_follow_target"},
            }],
            {"id": "target", "name": "Later Target"},
        )

        action = roadmap["buckets"]["submit_first"][0]["action"]
        self.assertIn("do not accept a screening before", action.lower())

    def test_next_cycle_premiere_target_is_never_dropped_from_calendar(self) -> None:
        ranked = []
        for index in range(9):
            festival_id = f"festival-{index}"
            ranked.append({
                "id": festival_id,
                "name": f"Festival {index}",
                "bucket": "prioritize_next",
                "score": 70,
                "eligible": True,
                "premiere_risk": "none",
                "premiere_opportunity": index == 8,
                "deadline_status": "upcoming",
                "deadline": {"next_deadline": f"2027-{index + 1:02d}-01"},
                "evidence": {"strategic_value": "Supported strategic value"},
                "ratings": {"strategic_value": 4},
                "post_target_compatibility": {
                    "status": "target" if index == 8 else "compatible"
                },
            })
        target = {"id": "festival-8", "name": "Festival 8", "scope": "continental"}

        roadmap = modules.normalise_roadmap(
            {"buckets": {}}, ranked, target, {"premiere_status": "world_premiere_available"}
        )

        self.assertEqual(len(roadmap["calendar"]), 9)
        self.assertTrue(
            any("Festival 8" in item["action"] for item in roadmap["calendar"])
        )
        self.assertIn("Next-cycle premiere target", roadmap["headline"])

    def test_open_questions_do_not_repeat_missing_field_intent(self) -> None:
        roadmap = modules.normalise_roadmap(
            {
                "buckets": {},
                "open_questions": [
                    "Confirm producer names and the legal production company.",
                    "Is the film picture- and sound-locked?",
                    "Confirm any prior festival submission commitments.",
                ],
            },
            [],
            None,
            {
                "premiere_status": "unknown",
                "missing_info": [
                    "producer and production company",
                    "completion/lock status (picture and sound locked)",
                ],
            },
        )

        self.assertEqual(len(roadmap["open_questions"]), 3)
        self.assertEqual(
            roadmap["open_questions"][-1],
            "Confirm any prior festival submission commitments.",
        )


if __name__ == "__main__":
    unittest.main()
