"""Narrow reusable runner for the existing Quick Strategy evidence stages."""

from __future__ import annotations

import time
from datetime import date

from app import config
from app.agent import modules
from app.agent.modules import Trace
from app.campaign.adapter import LegacyEvidenceAdapter, LegacyEvidenceBundle
from app.campaign.models import CampaignProfile
from app.llm import LLMClient


class LegacyEvidencePipeline:
    """Run analyzer/memory/retrieval/risk/scoring without RoadmapBuilder."""

    def __init__(self, *, adapter: LegacyEvidenceAdapter | None = None) -> None:
        self.adapter = adapter or LegacyEvidenceAdapter()

    def run(
        self,
        *,
        as_of_date: date,
        free_text: str | None = None,
        structured_profile: CampaignProfile | None = None,
    ) -> LegacyEvidenceBundle:
        del as_of_date  # Legacy date-sensitive stages use their existing clock contract.
        if (free_text is None) == (structured_profile is None):
            raise ValueError("provide exactly one of free_text or structured_profile")
        started = time.monotonic()
        trace = Trace()
        llm = LLMClient(
            trace_callback=trace.add,
            deadline_monotonic=started + config.RUN_DEADLINE_SECONDS,
        )
        if free_text is not None:
            profile = modules.film_analyzer(llm, trace, free_text)
        else:
            assert structured_profile is not None
            profile = self.adapter.profile_for_legacy_pipeline(structured_profile)
        memory = modules.company_memory(trace)
        candidates = modules.festival_search(trace, profile, memory)
        risks = modules.risk_checker(trace, profile, candidates)
        scores = modules.match_scorer(llm, trace, profile, candidates, memory)
        ranked = modules.assemble(
            candidates, scores, risks, profile, memory, trace
        )
        embedding_attempts = sum(
            1
            for step in trace.steps
            if isinstance(step.get("prompt"), dict)
            and isinstance(step["prompt"].get("provider"), dict)
            and step["prompt"]["provider"].get("kind") == "embedding"
        )
        return LegacyEvidenceBundle(
            profile=profile,
            retrieval_candidates=tuple(candidates),
            creative_scores=scores,
            risks=risks,
            ranked_candidates=tuple(ranked),
            company_memory=memory,
            trace=tuple(trace.steps),
            chat_attempts=int(llm.usage.get("attempts", 0)),
            embedding_attempts=embedding_attempts,
        )


__all__ = ["LegacyEvidencePipeline"]
