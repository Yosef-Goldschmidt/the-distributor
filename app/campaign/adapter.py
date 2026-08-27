"""Typed choke point between the legacy evidence pipeline and campaigns.

Raw dictionaries produced by ``app.agent.modules`` are intentionally confined
to this module.  Everything returned to campaign orchestration is an immutable
campaign contract with canonical festival IDs and explicit provenance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from app import config
from app.agent import domain, scoring
from app.campaign.contracts import (
    campaign_profile_hash,
    canonical_hash,
    creative_evidence_hash,
    frozen_candidate_hash,
    retrieval_input_hash,
    risk_evidence_hash,
)
from app.campaign.models import (
    CampaignProfile,
    CandidateCreativeEvidence,
    CandidateRiskEvidence,
    CompanyRelationshipEvidence,
    DeadlineAssessment,
    DeadlineStatus,
    DecisionGrade,
    DimensionEvidence,
    Fact,
    FactStatus,
    FeeFact,
    FestivalIdentitySnapshot,
    FrozenCandidateEvidence,
    PremiereAvailability,
    PremiereConstraint,
    PremiereLedgerSnapshot,
    PremiereScope,
    RetrievedFestivalEvidence,
    RetrievalInput,
    ScoreBreakdown,
    ScreeningAccess,
    ScreeningSnapshot,
    ScreeningState,
    VerificationItem,
    calculate_future_quality,
)


LEGACY_ADAPTER_VERSION = "legacy-evidence-adapter-v1"
LEGACY_RETRIEVAL_POLICY_VERSION = "legacy-hybrid-retrieval-v1"
LEGACY_RISK_POLICY_VERSION = "legacy-risk-checker-v1"
LEGACY_GUARDRAIL_VERSION = "legacy-scoring-guardrails-v1"


class LegacyEvidenceError(ValueError):
    """Legacy evidence cannot satisfy the frozen campaign boundary."""


@dataclass(frozen=True, slots=True)
class LegacyEvidenceBundle:
    """Untrusted legacy output accepted only by :class:`LegacyEvidenceAdapter`."""

    profile: Mapping[str, Any]
    retrieval_candidates: tuple[Mapping[str, Any], ...]
    creative_scores: Mapping[str, Mapping[str, Any]]
    risks: Mapping[str, Mapping[str, Any]]
    ranked_candidates: tuple[Mapping[str, Any], ...]
    company_memory: Mapping[str, Any]
    trace: tuple[Mapping[str, Any], ...] = ()
    chat_attempts: int = 0
    embedding_attempts: int = 0


@dataclass(frozen=True, slots=True)
class AdaptedCampaignEvidence:
    profile: CampaignProfile
    retrieval_input: RetrievalInput
    candidates: tuple[FrozenCandidateEvidence, ...]
    screenings: tuple[ScreeningSnapshot, ...]
    company_memory_summary: Mapping[str, Any]
    trace: tuple[Mapping[str, Any], ...]
    chat_attempts: int
    embedding_attempts: int


def _as_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(text for item in value if (text := _as_text(item))))


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _fact(
    value: Any,
    *,
    observed_at: datetime,
    source_ref: str,
    status: FactStatus = FactStatus.INFERRED,
) -> Fact[Any]:
    if value is None or value == "" or value == ():
        return Fact[Any](status=FactStatus.UNKNOWN, observed_at=observed_at)
    return Fact[Any](
        value=value,
        status=status,
        source_refs=(source_ref,),
        observed_at=observed_at,
    )


def _confidence(value: Any, *, unknown_for_low: bool = False) -> FactStatus:
    normalized = (_as_text(value) or "").casefold()
    if normalized in {"high", "confirmed"}:
        return FactStatus.CONFIRMED
    if normalized in {"medium", "asserted"}:
        return FactStatus.ASSERTED
    if unknown_for_low:
        return FactStatus.UNKNOWN
    return FactStatus.INFERRED


def _rating(value: Any) -> Decimal:
    try:
        number = Decimal(str(value))
    except Exception:  # noqa: BLE001 - normalize an untrusted legacy scalar
        number = Decimal(0)
    return max(Decimal(0), min(Decimal(5), number))


def _premiere_scope(value: Any) -> PremiereScope:
    normalized = (_as_text(value) or "unknown").casefold()
    try:
        return PremiereScope(normalized)
    except ValueError:
        return PremiereScope.UNKNOWN


class LegacyEvidenceAdapter:
    """Translate legacy evidence and rerun its deterministic risk semantics."""

    def adapt(
        self,
        bundle: LegacyEvidenceBundle,
        *,
        as_of_date: date,
        observed_at: datetime | None = None,
        authoritative_profile: CampaignProfile | None = None,
    ) -> AdaptedCampaignEvidence:
        observed_at = observed_at or datetime.combine(
            as_of_date, time.min, tzinfo=timezone.utc
        )
        profile = authoritative_profile or self.profile(
            bundle.profile, observed_at=observed_at
        )
        retrieval = self.retrieval_input(
            profile, bundle.profile, as_of_date=as_of_date
        )
        self._validate_id_chain(bundle)
        by_score = bundle.creative_scores
        by_risk = bundle.risks
        by_ranked = {
            str(item.get("id")): item for item in bundle.ranked_candidates
        }
        candidates = tuple(
            self._candidate(
                raw,
                by_score[str(raw.get("id"))],
                by_risk[str(raw.get("id"))],
                by_ranked[str(raw.get("id"))],
                bundle.company_memory,
                profile,
                retrieval,
                rank=index,
                as_of_date=as_of_date,
                observed_at=observed_at,
            )
            for index, raw in enumerate(bundle.retrieval_candidates, 1)
        )
        return AdaptedCampaignEvidence(
            profile=profile,
            retrieval_input=retrieval,
            candidates=candidates,
            screenings=self.screenings(bundle.profile, observed_at=observed_at),
            company_memory_summary=self.company_memory_summary(bundle.company_memory),
            trace=bundle.trace,
            chat_attempts=bundle.chat_attempts,
            embedding_attempts=bundle.embedding_attempts,
        )

    def profile(
        self, raw: Mapping[str, Any], *, observed_at: datetime
    ) -> CampaignProfile:
        contradictions = {
            str(item.get("field"))
            for item in (raw.get("_validation") or {}).get("contradictions", ())
            if isinstance(item, Mapping)
        }

        def field(name: str, value: Any) -> Fact[Any]:
            if name in contradictions:
                refs = (f"legacy:FilmAnalyzer:{name}:a", f"legacy:FilmAnalyzer:{name}:b")
                return Fact[Any](
                    status=FactStatus.CONTRADICTED,
                    source_refs=refs,
                    observed_at=observed_at,
                )
            return _fact(
                value,
                observed_at=observed_at,
                source_ref=f"legacy:FilmAnalyzer:{name}",
            )

        premiere_assertions: list[Fact[str]] = []
        status = (_as_text(raw.get("premiere_status")) or "unknown").casefold()
        if status == "world_premiere_available":
            premiere_assertions.append(
                _fact(
                    "No prior public screening",
                    observed_at=observed_at,
                    source_ref="legacy:FilmAnalyzer:premiere_status",
                    status=FactStatus.ASSERTED,
                )
            )
        elif status == "international_premiere_available" and bool(
            (raw.get("_film_history_evidence") or {}).get(
                "single_home_country_screening"
            )
        ):
            premiere_assertions.append(
                _fact(
                    "The only prior public screening occurred in the film's home country",
                    observed_at=observed_at,
                    source_ref="legacy:FilmAnalyzer:premiere_status",
                    status=FactStatus.ASSERTED,
                )
            )
        elif "premiere_status" in contradictions:
            premiere_assertions.append(
                Fact[str](
                    status=FactStatus.CONTRADICTED,
                    source_refs=(
                        "legacy:FilmAnalyzer:premiere_status:screened",
                        "legacy:FilmAnalyzer:premiere_status:unscreened",
                    ),
                    observed_at=observed_at,
                )
            )

        synopsis = raw.get("synopsis") or raw.get("logline")
        initial = CampaignProfile(
            title=field("title", _as_text(raw.get("title"))),
            synopsis=field("synopsis", _as_text(synopsis)),
            format=field("format", _as_text(raw.get("format"))),
            country=field("country", _as_text(raw.get("country"))),
            themes=field("themes", _as_tuple(raw.get("themes"))),
            runtime_minutes=field("runtime_minutes", raw.get("runtime_minutes")),
            premiere_assertions=tuple(premiere_assertions),
            target_regions=_as_tuple(raw.get("target_regions")),
            profile_hash="0" * 64,
        )
        return initial.model_copy(
            update={"profile_hash": campaign_profile_hash(initial)}
        )

    def retrieval_input(
        self,
        profile: CampaignProfile,
        raw_profile: Mapping[str, Any],
        *,
        as_of_date: date,
    ) -> RetrievalInput:
        query = _as_text(raw_profile.get("search_query")) or _as_text(
            raw_profile.get("logline")
        )
        if not query:
            query = " ".join(profile.themes.value or ())
        if len(query.strip()) < 3:
            query = profile.synopsis.value or profile.title.value or "film festival"
        initial = RetrievalInput(
            profile_hash=profile.profile_hash,
            semantic_query=query,
            format=profile.format.value or "unknown",
            country=profile.country.value or "unknown",
            themes=profile.themes.value or (),
            target_regions=profile.target_regions,
            retrieval_policy_version=LEGACY_RETRIEVAL_POLICY_VERSION,
            embedding_model=(
                config.LLM_EMBED_MODEL
                or config.PINECONE_EMBED_MODEL
                or "local-tfidf"
            ),
            corpus_identity_version="festival-corpus-355-v1",
            as_of_date=as_of_date,
            retrieval_key="0" * 64,
        )
        return initial.model_copy(
            update={"retrieval_key": retrieval_input_hash(initial)}
        )

    def screenings(
        self, raw_profile: Mapping[str, Any], *, observed_at: datetime
    ) -> tuple[ScreeningSnapshot, ...]:
        results: list[ScreeningSnapshot] = []
        for index, row in enumerate(raw_profile.get("premiere_history") or (), 1):
            if not isinstance(row, Mapping):
                continue
            occurred = _date(row.get("date") or row.get("occurred_at"))
            source_refs = [f"legacy:FilmAnalyzer:premiere_history:{index}"]
            if row.get("home_country") is True:
                source_refs.append(
                    f"legacy:FilmAnalyzer:premiere_history:{index}:home-country"
                )
            if str(row.get("event_kind") or "").casefold() == "online_availability":
                source_refs.append(
                    f"legacy:FilmAnalyzer:premiere_history:{index}:online-availability"
                )
            results.append(
                ScreeningSnapshot(
                    screening_id=f"legacy-screening-{index}",
                    state=ScreeningState.OCCURRED,
                    access=ScreeningAccess.PUBLIC,
                    country=_as_text(row.get("country")),
                    region=_as_text(row.get("region")),
                    occurred_at=(
                        datetime.combine(occurred, time.min, tzinfo=timezone.utc)
                        if occurred
                        else None
                    ),
                    source_refs=tuple(source_refs),
                )
            )
        return tuple(results)

    def refresh_risk(
        self,
        candidates: Sequence[FrozenCandidateEvidence],
        profile: CampaignProfile,
        ledger: PremiereLedgerSnapshot,
        screenings: Sequence[ScreeningSnapshot],
        *,
        as_of_date: date,
    ) -> tuple[FrozenCandidateEvidence, ...]:
        """Run the existing deterministic risk/scoring rules with cached evidence."""

        legacy_profile = self._profile_to_legacy(profile, ledger, screenings)
        refreshed: list[FrozenCandidateEvidence] = []
        for candidate in candidates:
            legacy_festival = self._festival_to_legacy(candidate)
            raw_risk = domain.assess_candidate(
                legacy_profile, legacy_festival, as_of_date
            )
            risk = self._risk(
                raw_risk,
                candidate.retrieved,
                profile,
                as_of_date=as_of_date,
            )
            guarded = {
                item.dimension: float(item.guarded_rating)
                for item in candidate.creative.dimensions
            }
            deadline_urgency = Decimal(
                str((raw_risk.get("deadline") or {}).get("urgency", 2))
            )
            guarded["deadline_urgency"] = float(deadline_urgency)
            computed = scoring.compute_score(guarded, raw_risk.get("premiere_risk"))
            old_dimensions = {
                item.dimension: item for item in candidate.score_breakdown.dimensions
            }
            dimensions = tuple(
                old_dimensions[name].model_copy(
                    update={
                        "raw_rating": deadline_urgency,
                        "guarded_rating": deadline_urgency,
                        "points": Decimal(
                            str(computed["breakdown"][name]["points"])
                        ),
                        "evidence_refs": (
                            f"legacy:RiskChecker:{candidate.festival_id}:deadline",
                        ),
                    }
                )
                if name == "deadline_urgency"
                else old_dimensions[name].model_copy(
                    update={
                        "points": Decimal(
                            str(computed["breakdown"][name]["points"])
                        )
                    }
                )
                for name in scoring.WEIGHTS
            )
            bucket_input = {
                "score": computed["score"],
                "premiere_risk": raw_risk.get("premiere_risk"),
                "deadline_status": raw_risk.get("deadline_status"),
                "deadline": raw_risk.get("deadline") or {},
                "eligible": raw_risk.get("eligible", True),
                "premiere_opportunity": raw_risk.get("premiere_opportunity", False),
                "ratings": guarded,
                "tier": candidate.retrieved.identity.tier.value,
            }
            initial = candidate.model_copy(
                update={
                    "risk": risk,
                    "score_breakdown": ScoreBreakdown(
                        score=int(computed["score"]),
                        base_score=Decimal(str(computed["base_score"])),
                        premiere_penalty=Decimal(
                            str(computed["premiere_penalty"])
                        ),
                        dimensions=dimensions,
                    ),
                    "decision_grade": DecisionGrade(
                        scoring.assign_bucket(bucket_input)
                    ),
                    "future_quality": calculate_future_quality(dimensions),
                    "component_hash": "0" * 64,
                }
            )
            refreshed.append(
                initial.model_copy(
                    update={"component_hash": frozen_candidate_hash(initial)}
                )
            )
        return tuple(refreshed)

    @staticmethod
    def profile_for_legacy_pipeline(profile: CampaignProfile) -> dict[str, Any]:
        """Translate a structured campaign profile for the existing modules.

        This inverse translation remains here so no other campaign component
        learns the legacy FilmAnalyzer dictionary shape.
        """

        has_unscreened_assertion = any(
            assertion.value
            and "no prior public screening" in assertion.value.casefold()
            for assertion in profile.premiere_assertions
        )
        return {
            "title": profile.title.value,
            "logline": profile.synopsis.value,
            "synopsis": profile.synopsis.value,
            "format": profile.format.value,
            "country": profile.country.value,
            "themes": list(profile.themes.value or ()),
            "genres": [],
            "festival_angles": [],
            "runtime_minutes": profile.runtime_minutes.value,
            "premiere_status": (
                "world_premiere_available" if has_unscreened_assertion else "unknown"
            ),
            "premiere_history": [],
            "target_regions": list(profile.target_regions),
            "search_query": profile.synopsis.value,
            "missing_info": [],
            "_validation": {"valid": True, "adjustments": []},
        }

    @staticmethod
    def company_memory_summary(memory: Mapping[str, Any]) -> Mapping[str, Any]:
        history = [row for row in memory.get("history", ()) if isinstance(row, Mapping)]
        return {
            "company": (memory.get("company") or {}).get("name"),
            "history_rows": len(history),
            "festival_relationships": len(
                {str(row.get("festival_id")) for row in history if row.get("festival_id")}
            ),
        }

    @staticmethod
    def _validate_id_chain(bundle: LegacyEvidenceBundle) -> None:
        retrieval_ids = [str(item.get("id")) for item in bundle.retrieval_candidates]
        if any(item in {"", "None"} for item in retrieval_ids):
            raise LegacyEvidenceError("every retrieved festival requires festivals.id")
        if len(retrieval_ids) != len(set(retrieval_ids)):
            raise LegacyEvidenceError("duplicate canonical festival_id in retrieval output")
        expected = set(retrieval_ids)
        scores = {str(item) for item in bundle.creative_scores if item != "_validation"}
        risks = {str(item) for item in bundle.risks}
        ranked = {str(item.get("id")) for item in bundle.ranked_candidates}
        if scores != expected or risks != expected or ranked != expected:
            raise LegacyEvidenceError(
                "retrieval, creative, risk, and assembled outputs must share exact festivals.id values"
            )

    def _candidate(
        self,
        raw: Mapping[str, Any],
        score_row: Mapping[str, Any],
        risk_row: Mapping[str, Any],
        ranked: Mapping[str, Any],
        memory: Mapping[str, Any],
        profile: CampaignProfile,
        retrieval_input: RetrievalInput,
        *,
        rank: int,
        as_of_date: date,
        observed_at: datetime,
    ) -> FrozenCandidateEvidence:
        festival_id = str(raw["id"])
        facts_hash = canonical_hash(
            {
                key: raw.get(key)
                for key in (
                    "id",
                    "name",
                    "country",
                    "region",
                    "tier",
                    "accepts",
                    "premiere_requirement",
                    "premiere_requirement_raw",
                    "premiere_territory",
                    "submission_open",
                    "next_deadline",
                    "final_deadline",
                    "typical_deadline_month",
                    "submission_fee",
                    "identity_confidence",
                )
            }
        )
        source = f"festival:{festival_id}"
        identity_status = _confidence(raw.get("identity_confidence"))
        premiere_raw = _as_text(raw.get("premiere_requirement_raw")) or _as_text(
            raw.get("premiere_requirement")
        )
        deadline_raw = (
            _as_text(raw.get("final_deadline"))
            or _as_text(raw.get("next_deadline"))
            or _as_text(raw.get("typical_deadline_month"))
        )
        retrieved = RetrievedFestivalEvidence(
            festival_id=festival_id,
            identity=FestivalIdentitySnapshot(
                festival_name=_as_text(raw.get("name")) or festival_id,
                country=_fact(
                    _as_text(raw.get("country")),
                    observed_at=observed_at,
                    source_ref=source,
                    status=identity_status,
                ),
                region=_fact(
                    _as_text(raw.get("region")),
                    observed_at=observed_at,
                    source_ref=source,
                    status=identity_status,
                ),
                tier=_fact(
                    _as_text(raw.get("tier")),
                    observed_at=observed_at,
                    source_ref=source,
                    status=identity_status,
                ),
                accepts=_fact(
                    _as_tuple(raw.get("accepts")),
                    observed_at=observed_at,
                    source_ref=source,
                    status=identity_status,
                ),
            ),
            festival_facts_hash=facts_hash,
            retrieval_rank=rank,
            semantic_score=Decimal(str(raw.get("semantic_score") or 0)),
            lexical_score=Decimal(str(raw.get("lexical_score") or 0)),
            retrieval_sources=_as_tuple(raw.get("retrieval_sources")) or (
                "legacy-retrieval",
            ),
            retrieval_backend=_as_text(raw.get("retrieval_backend"))
            or "legacy-retrieval",
            source_confidence=identity_status,
            premiere_rule=_fact(
                premiere_raw,
                observed_at=observed_at,
                source_ref=f"{source}:premiere-rule",
                status=_confidence(
                    (risk_row.get("premiere_constraint") or {}).get("confidence")
                ),
            ),
            deadline_fact=_fact(
                deadline_raw,
                observed_at=observed_at,
                source_ref=f"{source}:deadline",
                status=(
                    FactStatus.CONFIRMED
                    if raw.get("final_deadline") or raw.get("next_deadline")
                    else FactStatus.ASSERTED
                ),
            ),
            fee_fact=self._fee(raw.get("submission_fee"), observed_at, source),
            retrieval_key=retrieval_input.retrieval_key,
        )
        dimensions = self._dimensions(
            festival_id, score_row, ranked, memory, as_of_date
        )
        relation = self._relationship(festival_id, memory, as_of_date.year)
        creative_initial = CandidateCreativeEvidence(
            festival_id=festival_id,
            profile_hash=profile.profile_hash,
            festival_facts_hash=facts_hash,
            dimensions=dimensions,
            guardrail_adjustments=tuple(
                str(item)
                for item in (ranked.get("validation") or {}).get("adjustments", ())
            ),
            company_relationship=relation,
            prompt_version="legacy-match-scorer-prompt-v1",
            model_version=config.LLM_MODEL or "configured-legacy-model",
            guardrail_version=LEGACY_GUARDRAIL_VERSION,
            creative_key="0" * 64,
        )
        creative = creative_initial.model_copy(
            update={"creative_key": creative_evidence_hash(creative_initial)}
        )
        risk = self._risk(
            risk_row, retrieved, profile, as_of_date=as_of_date
        )
        score_dimensions = tuple(
            item.model_copy(
                update={
                    "points": Decimal(
                        str(
                            ((ranked.get("breakdown") or {}).get(item.dimension) or {}).get(
                                "points", item.points
                            )
                        )
                    )
                }
            )
            for item in dimensions
        )
        initial = FrozenCandidateEvidence(
            festival_id=festival_id,
            retrieved=retrieved,
            creative=creative,
            risk=risk,
            score_breakdown=ScoreBreakdown(
                score=int(ranked.get("score") or 0),
                base_score=Decimal(str(ranked.get("base_score") or 0)),
                premiere_penalty=Decimal(
                    str(ranked.get("premiere_penalty") or 0)
                ),
                dimensions=score_dimensions,
            ),
            decision_grade=str(ranked.get("bucket") or "hold_avoid"),
            future_quality=calculate_future_quality(score_dimensions),
            component_hash="0" * 64,
        )
        return initial.model_copy(
            update={"component_hash": frozen_candidate_hash(initial)}
        )

    @staticmethod
    def _fee(value: Any, observed_at: datetime, source: str) -> FeeFact:
        text = _as_text(value)
        if text:
            match = re.fullmatch(r"\$\s*(\d+(?:\.\d{1,2})?)", text)
            if match:
                return FeeFact(
                    amount=Decimal(match.group(1)),
                    currency="USD",
                    status=FactStatus.CONFIRMED,
                    source_refs=(f"{source}:fee",),
                    observed_at=observed_at,
                )
        return FeeFact(status=FactStatus.UNKNOWN, observed_at=observed_at)

    def _dimensions(
        self,
        festival_id: str,
        score_row: Mapping[str, Any],
        ranked: Mapping[str, Any],
        memory: Mapping[str, Any],
        as_of_date: date,
    ) -> tuple[DimensionEvidence, ...]:
        raw_ratings = score_row.get("ratings") or {}
        guarded_ratings = ranked.get("ratings") or {}
        breakdown = ranked.get("breakdown") or {}
        relation = self._relationship(festival_id, memory, as_of_date.year)
        results: list[DimensionEvidence] = []
        for dimension, weight in scoring.WEIGHTS.items():
            guarded = _rating(guarded_ratings.get(dimension))
            raw = (
                relation.rating
                if dimension == "company_relationship"
                else guarded
                if dimension == "deadline_urgency"
                else _rating(raw_ratings.get(dimension))
            )
            points = ((breakdown.get(dimension) or {}).get("points"))
            if points is None:
                points = guarded * Decimal(weight) / Decimal(5)
            results.append(
                DimensionEvidence(
                    dimension=dimension,
                    raw_rating=raw,
                    guarded_rating=guarded,
                    points=Decimal(str(points)),
                    evidence_refs=(f"legacy:score:{festival_id}:{dimension}",),
                )
            )
        return tuple(results)

    @staticmethod
    def _relationship(
        festival_id: str, memory: Mapping[str, Any], current_year: int
    ) -> CompanyRelationshipEvidence:
        rows = [
            dict(row)
            for row in memory.get("history", ())
            if isinstance(row, Mapping) and row.get("festival_id") == festival_id
        ]
        rating, _evidence, facts = scoring.company_relationship_rating(
            rows, current_year
        )
        return CompanyRelationshipEvidence(
            rating=Decimal(str(rating)),
            screenings=int(facts["screenings"]),
            latest_year=facts["latest_year"],
            award_count=int(facts["award_count"]),
            evidence_refs=tuple(
                f"company-memory:{festival_id}:{index}"
                for index in range(1, len(rows) + 1)
            ),
        )

    def _risk(
        self,
        raw: Mapping[str, Any],
        retrieved: RetrievedFestivalEvidence,
        profile: CampaignProfile,
        *,
        as_of_date: date,
    ) -> CandidateRiskEvidence:
        constraint = raw.get("premiere_constraint") or {}
        rule_status = _confidence(
            constraint.get("confidence"), unknown_for_low=True
        )
        deadline = raw.get("deadline") or {}
        deadline_status_text = _as_text(
            raw.get("deadline_status") or deadline.get("status")
        ) or "unknown"
        try:
            deadline_status = DeadlineStatus(deadline_status_text)
        except ValueError:
            deadline_status = DeadlineStatus.UNKNOWN
        deadline_confidence = _confidence(deadline.get("confidence"))
        uncertainty_values = tuple(
            str(item) for item in raw.get("uncertainties", ()) if _as_text(item)
        )
        uncertainties = tuple(
            VerificationItem(
                item_id=f"risk-{retrieved.festival_id}-{index}-{canonical_hash(text)[:8]}",
                fact_key=(
                    "premiere.rule"
                    if any(
                        marker in text.casefold()
                        for marker in ("premiere", "online availability", "streaming")
                    )
                    or not any(
                        marker in text.casefold()
                        for marker in ("deadline", "submission")
                    )
                    else "deadline.current"
                ),
                status=FactStatus.UNKNOWN,
                blocking=not bool(raw.get("eligible", True)),
                source_refs=(f"festival:{retrieved.festival_id}:risk",),
            )
            for index, text in enumerate(uncertainty_values, 1)
        )
        risk_input_hash = canonical_hash(
            {
                "profile_hash": profile.profile_hash,
                "festival_facts_hash": retrieved.festival_facts_hash,
                "raw_risk": raw,
                "as_of_date": as_of_date,
                "policy": LEGACY_RISK_POLICY_VERSION,
            }
        )
        eligible = bool(raw.get("eligible", True))
        hard_reason = _as_text(raw.get("eligibility_issue"))
        if not eligible and not hard_reason:
            hard_reason = (
                "premiere_ineligible"
                if raw.get("premiere_risk") == "high"
                else "hard_ineligible"
            )
        initial = CandidateRiskEvidence(
            festival_id=retrieved.festival_id,
            profile_hash=profile.profile_hash,
            festival_facts_hash=retrieved.festival_facts_hash,
            eligible=eligible,
            hard_eligibility_reason=hard_reason,
            premiere_constraint=PremiereConstraint(
                scope=_premiere_scope(constraint.get("scope")),
                territory=_as_text(constraint.get("territory")),
                rule_status=rule_status,
                evidence_refs=(
                    (f"festival:{retrieved.festival_id}:premiere-rule",)
                    if rule_status != FactStatus.UNKNOWN
                    else ()
                ),
            ),
            premiere_risk=str(raw.get("premiere_risk") or "medium"),
            deadline=DeadlineAssessment(
                status=deadline_status,
                next_deadline=_date(deadline.get("next_deadline")),
                confidence=deadline_confidence,
                material_eligible=(deadline_status != DeadlineStatus.CLOSED),
                evidence_refs=(f"festival:{retrieved.festival_id}:deadline",),
            ),
            runtime_eligible=(
                False
                if raw.get("eligibility_issue") == "runtime_not_accepted"
                else None
            ),
            uncertainties=uncertainties,
            as_of_date=as_of_date,
            risk_policy_version=LEGACY_RISK_POLICY_VERSION,
            risk_input_hash=risk_input_hash,
            risk_key="0" * 64,
        )
        return initial.model_copy(
            update={"risk_key": risk_evidence_hash(initial)}
        )

    @staticmethod
    def _profile_to_legacy(
        profile: CampaignProfile,
        ledger: PremiereLedgerSnapshot,
        screenings: Sequence[ScreeningSnapshot],
    ) -> dict[str, Any]:
        scopes = {(item.scope, item.territory): item for item in ledger.scopes}
        world = scopes.get((PremiereScope.WORLD, None))
        international = scopes.get((PremiereScope.INTERNATIONAL, None))
        if world and world.availability == PremiereAvailability.AVAILABLE:
            premiere_status = "world_premiere_available"
        elif (
            world
            and world.availability == PremiereAvailability.CONSUMED
            and international
            and international.availability == PremiereAvailability.AVAILABLE
        ):
            premiere_status = "international_premiere_available"
        elif world and world.availability == PremiereAvailability.CONSUMED:
            premiere_status = "already_premiered"
        else:
            premiere_status = "unknown"
        return {
            "title": profile.title.value,
            "logline": profile.synopsis.value,
            "format": profile.format.value,
            "country": profile.country.value,
            "themes": list(profile.themes.value or ()),
            "runtime_minutes": profile.runtime_minutes.value,
            "premiere_status": premiere_status,
            "premiere_history": [
                {
                    "country": item.country,
                    "region": item.region,
                    "event_kind": (
                        "online_availability"
                        if any(
                            "online-availability" in ref.casefold()
                            for ref in item.source_refs
                        )
                        else "screening"
                    ),
                    "occurred_at": (
                        item.occurred_at.isoformat() if item.occurred_at else None
                    ),
                }
                for item in screenings
                if item.state == ScreeningState.OCCURRED
                and item.access == ScreeningAccess.PUBLIC
            ],
        }

    @staticmethod
    def _festival_to_legacy(candidate: FrozenCandidateEvidence) -> dict[str, Any]:
        retrieved = candidate.retrieved
        constraint = candidate.risk.premiere_constraint
        raw_rule = retrieved.premiere_rule.value
        if not raw_rule:
            if constraint.scope == PremiereScope.NONE:
                raw_rule = "No requirement"
            elif constraint.scope == PremiereScope.WORLD:
                raw_rule = "World"
            elif constraint.territory:
                raw_rule = f"World - {constraint.territory}"
        return {
            "id": candidate.festival_id,
            "name": retrieved.identity.festival_name,
            "country": retrieved.identity.country.value,
            "region": retrieved.identity.region.value,
            "tier": retrieved.identity.tier.value,
            "accepts": list(retrieved.identity.accepts.value or ()),
            "premiere_requirement_raw": raw_rule,
            "premiere_requirement": constraint.scope.value,
            "final_deadline": (
                candidate.risk.deadline.next_deadline.isoformat()
                if candidate.risk.deadline.next_deadline
                else None
            ),
            "identity_confidence": retrieved.source_confidence.value,
        }


__all__ = [
    "AdaptedCampaignEvidence",
    "LEGACY_ADAPTER_VERSION",
    "LegacyEvidenceAdapter",
    "LegacyEvidenceBundle",
    "LegacyEvidenceError",
]
