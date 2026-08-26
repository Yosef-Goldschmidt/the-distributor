"""Offline release evaluation is executable, deterministic, and provider-free."""

from __future__ import annotations

from evals.run_campaign import evaluate_campaign


def test_campaign_evaluation_gate_passes_without_providers_or_external_writes() -> None:
    first = evaluate_campaign()
    second = evaluate_campaign()
    assert first == second
    assert first["status"] == "PASS"
    assert first["provider_calls"] == 0
    assert first["external_writes"] == 0
    assert all(
        section["status"] == "PASS"
        for section in first["sections"].values()
    )
    reuse = first["sections"]["incremental_replanning_and_scenarios"]
    assert reuse["rejection"]["reuse_manifest"]["chat_attempts"] == 0
    assert reuse["rejection"]["reuse_manifest"]["embedding_attempts"] == 0
    assert reuse["screening_scenario"]["campaign_unchanged"] is True
    assert first["sections"]["isolation_and_corpus"]["sitges"] == {
        "present": False,
        "classification": "known corpus-coverage issue",
    }
