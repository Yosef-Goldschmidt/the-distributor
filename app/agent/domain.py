"""Deterministic festival-distribution domain rules.

The source workbook mixes hard dates with shorthand planning fields.  This
module keeps those facts separate, projects recurring submission cycles
explicitly, and never asks an LLM to decide calendar arithmetic, format
eligibility or premiere availability.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

CONTINENT_TERRITORIES = {
    "europe": "Europe",
    "european": "Europe",
    "asia": "Asia",
    "asian": "Asia",
    "africa": "Africa",
    "african": "Africa",
    "north america": "North America",
    "north american": "North America",
    "latin america": "Latin America",
    "latin american": "Latin America",
    "middle east": "Middle East",
    "oceania": "Oceania",
}

TERRITORY_ALIASES = {
    "austia": "Austria",
    "finaland": "Finland",
    "luxemburg": "Luxembourg",
    "swedish": "Sweden",
}

FESTIVAL_NAME_CORRECTIONS = {
    "golden-apricot-yerenan-international-film-festival": (
        "Golden Apricot Yerevan International Film Festival"
    ),
}


YOUTH_AUDIENCE_PATTERNS = [
    r"\b(?:target|intended|primary|core)\s+audience\s*(?::|is|are|includes?)?\s*(?:children|kids|teens?|teenagers|youth|young\s+(?:people|audiences?)|families)\b",
    r"\b(?:made|intended|designed|created|suitable)\s+for\s+(?:children|kids|teens?|teenagers|youth|young\s+(?:people|audiences?)|family\s+audiences?)\b",
    r"\b(?:made|intended|designed|created|suitable)\s+for\s+(?:schools?|classrooms?|students?)\b",
    r"\b(?:children(?:'s)?|kids?|youth|young\s+audience|teen)\s+(?:film|cinema|documentary|animation|audience|programming)\b",
    r"\b(?:family[- ]friendly|family\s+audience|youth[- ]facing|child[- ]friendly|school\s+audience)\b",
    r"\b(?:family|children(?:'s)?|kids?|youth)\s+(?:film|animation|documentary|programming)\b",
    r"\b(?:viewers?|audiences?)\s+(?:aged?|ages?)\s+\d{1,2}(?:\s*(?:-|–|—|to)\s*\d{1,2})?\b",
    r"\b(?:for\s+)?ages?\s+\d{1,2}(?:\s*(?:-|–|—|to)\s*\d{1,2})\b",
]

WOMEN_AUTHORSHIP_PATTERNS = [
    r"\b(?:directed|written|created|made|produced)\s+by\s+(?:a\s+)?(?:woman|women|female\s+filmmakers?)\b",
    r"\b(?:woman|women|female)\s+(?:director|directors|filmmaker|filmmakers|writer|writers|producer|producers)\b",
    r"\b(?:women|woman|female)[- ](?:directed|authored|led)\b",
    r"\b(?:the\s+)?(?:director|filmmaker|writer|producer)\s+(?:is|identifies\s+as)\s+(?:a\s+)?woman\b",
]

INDIGENOUS_AUTHORSHIP_PATTERNS = [
    r"\b(?:directed|written|created|made|produced)\s+by\s+(?:an?\s+)?indigenous\b",
    r"\bindigenous\s+(?:director|directors|filmmaker|filmmakers|writer|writers|producer|producers|authorship)\b",
    r"\bindigenous[- ](?:directed|authored|led)\b",
    r"\b(?:the\s+)?(?:director|filmmaker|writer|producer)\s+(?:is|identifies\s+as)\s+indigenous\b",
]


def analyse_critical_input(text: str) -> dict[str, Any]:
    """Extract only evidence needed to stop critical LLM over-inference.

    This is intentionally conservative. It does not replace FilmAnalyzer; it
    records whether the original request supplies evidence for strategically
    irreversible states and whether explicit statements conflict.
    """

    normalized = re.sub(r"[’‘]", "'", text or "").lower()
    runtimes = sorted({
        int(value)
        for value in re.findall(
            r"(?<!\d)(\d{1,3})\s*(?:-|–|—)?\s*(?:minutes?|mins?\b)",
            normalized,
        )
        if 1 <= int(value) <= 600
    })

    premiere_positive_patterns = [
        r"\b(?:has|had|was)\s+(?:already\s+)?premiered\b",
        r"(?<!not\s)(?<!never\s)\bpremiered\s+(?:publicly|at|in|on|last|already)\b",
        r"\b(?:was|has\s+been|had\s+been)\s+publicly\s+screened\b",
        r"(?<!not\s)(?<!never\s)\b(?:publicly\s+)?screened\s+(?:at|in|on|last|before|already)\b",
        r"\bpublic\s+(?:festival\s+)?screening\s+(?:at|in|occurred|took\s+place)\b",
        r"\breleased\s+(?:theatrically|online|publicly|on\s+(?:television|tv|streaming))\b",
    ]
    public_online_patterns = [
        r"\bunrestricted\s+(?:public\s+)?online\s+availability\b",
        r"\b(?:publicly|freely)\s+available\s+online\b",
        r"\bavailable\s+online\s+(?:to\s+(?:the\s+)?public|to\s+anyone|without\s+restriction|without\s+restrictions|unrestricted)\b",
        r"\bavailable\s+to\s+(?:the\s+)?public\s+online\b",
    ]
    premiere_negative_patterns = [
        r"\bnever\s+(?:publicly\s+)?(?:screened|premiered|shown|released)\b",
        r"\b(?:has|have|had)\s+never\s+been\s+(?:publicly\s+)?(?:screened|premiered|shown|released)\b",
        r"\b(?:has|have|had)\s+not\s+(?:been\s+)?(?:screened|premiered|shown|released)\b",
        r"\bnot\s+(?:yet\s+)?(?:been\s+)?(?:screened|premiered|shown|released)\b",
        r"\bno\s+(?:public\s+)?(?:screening|screenings|premiere)\s+(?:yet|to\s+date)\b",
        r"\b(?:has|have|had)\s+(?:had\s+)?no\s+public\s+(?:screening|screenings|premiere|premieres)\b",
        r"\bno\s+public\s+(?:screening|screenings)\b(?!\s+(?:information|details?|records?|history)\b)",
        r"\b(?:world[- ]premiere(?:\s+(?:status|rights))?\s+(?:(?:is|remains?)\s+)?available|unpremiered|unscreened)\b",
    ]
    premiere_unknown_patterns = [
        r"\bpremiere\s+status\s+(?:is\s+)?(?:unknown|uncertain|not\s+known)\b",
        r"\b(?:do\s+not|don't)\s+know\s+(?:whether|if).{0,40}\b(?:screened|premiered)\b",
        r"\bnot\s+sure\s+(?:whether|if).{0,40}\b(?:screened|premiered)\b",
    ]
    premiere_negative = any(re.search(pattern, normalized) for pattern in premiere_negative_patterns)
    public_online = any(re.search(pattern, normalized) for pattern in public_online_patterns)
    premiere_international = bool(
        re.search(
            r"\binternational\s+premiere(?:\s+(?:status|rights))?\s+"
            r"(?:(?:is|remains?)\s+)?available\b",
            normalized,
        )
    )
    positive_source = normalized
    for pattern in premiere_negative_patterns:
        positive_source = re.sub(pattern, "", positive_source)
    premiere_positive = any(
        re.search(pattern, positive_source) for pattern in premiere_positive_patterns
    ) or public_online
    premiere_unknown = any(re.search(pattern, normalized) for pattern in premiere_unknown_patterns)

    format_labels: list[str] = []
    format_value = (
        r"feature\s+documentary|feature\s+doc|short\s+documentary|short\s+doc|"
        r"feature\s+fiction|fiction\s+feature|narrative\s+feature|short\s+fiction|"
        r"fiction\s+short|narrative\s+short|animation|animated\s+(?:feature|short|film)|"
        r"experimental(?:\s+film)?"
    )
    format_pattern = re.compile(
        rf"\b(?:format\s*(?::|is)|(?:the\s+)?(?:film|movie)\s+is|this\s+is|it(?:'s|\s+is))"
        rf"\s+(?:an?\s+)?({format_value})\b"
    )
    format_mapping = {
        "feature documentary": "feature_doc",
        "feature doc": "feature_doc",
        "short documentary": "short_doc",
        "short doc": "short_doc",
        "feature fiction": "feature_fiction",
        "fiction feature": "feature_fiction",
        "narrative feature": "feature_fiction",
        "short fiction": "short_fiction",
        "fiction short": "short_fiction",
        "narrative short": "short_fiction",
        "animation": "animation",
        "animated feature": "animation",
        "animated short": "animation",
        "animated film": "animation",
        "experimental": "experimental",
        "experimental film": "experimental",
    }
    for match in format_pattern.finditer(normalized):
        format_labels.append(format_mapping[match.group(1)])

    incomplete_patterns = [
        r"\b(?:rough\s+cut|work\s+in\s+progress|in\s+post[- ]production|unfinished)\b",
        r"\b(?:nearly|almost)\s+(?:finished|complete|completed)\b",
        r"\bnot\s+(?:yet\s+)?(?:finished|complete|completed|picture[- ]locked)\b",
    ]
    complete_patterns = [
        r"\b(?:picture[- ]locked|sound[- ]locked|ready\s+for\s+delivery)\b",
        r"(?<!nearly\s)(?<!almost\s)(?<!not\s)\b(?:finished|completed)\b",
    ]
    unreleased_patterns = [
        r"\b(?:unreleased|not\s+(?:yet\s+)?released|no\s+release\s+yet)\b",
    ]
    released_patterns = [
        r"\b(?:already\s+released|released\s+(?:theatrically|online|publicly|on\s+(?:television|tv|streaming)))\b",
    ]
    incomplete = any(re.search(pattern, normalized) for pattern in incomplete_patterns)
    complete = any(re.search(pattern, normalized) for pattern in complete_patterns)
    unreleased = any(re.search(pattern, normalized) for pattern in unreleased_patterns)
    released = any(re.search(pattern, normalized) for pattern in released_patterns)

    youth_matches = [
        pattern for pattern in YOUTH_AUDIENCE_PATTERNS if re.search(pattern, normalized)
    ]
    women_authorship = any(
        re.search(pattern, normalized) for pattern in WOMEN_AUTHORSHIP_PATTERNS
    )
    indigenous_authorship = any(
        re.search(pattern, normalized) for pattern in INDIGENOUS_AUTHORSHIP_PATTERNS
    )
    home_country_screening = bool(
        re.search(
            r"\b(?:public(?:ly)?\s+)?screen(?:ed|ing)\b.{0,50}\b(?:home\s+country|domestically)\b"
            r"|\b(?:domestic|home-country)\s+public\s+screening\b",
            normalized,
        )
    )
    single_public_screening = bool(
        re.search(
            r"\b(?:one|single|only)\s+(?:prior\s+|occurred\s+)?public\s+(?:screening|premiere)\b"
            r"|\bonly\s+(?:prior\s+)?public\s+(?:screening|premiere)\b",
            normalized,
        )
    )
    screening_date_match = re.search(
        r"\b(20\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))\b",
        normalized,
    )
    return {
        "runtime": {"values": runtimes, "contradictory": len(runtimes) > 1},
        "premiere": {
            "positive_screening": premiere_positive,
            "explicitly_unscreened": premiere_negative,
            "international_premiere_available": premiere_international,
            "explicitly_unknown": premiere_unknown,
            "public_online_availability": public_online,
            "home_country_screening": home_country_screening,
            "single_public_screening": single_public_screening,
            "screening_date": screening_date_match.group(1) if screening_date_match else None,
            "evidence_present": (
                premiere_positive
                or premiere_negative
                or premiere_international
                or premiere_unknown
            ),
            "contradictory": (
                (premiere_positive and premiere_negative)
                or (premiere_unknown and (premiere_positive or premiere_negative))
            ),
        },
        "format": {
            "explicit_labels": sorted(set(format_labels)),
            "contradictory": len(set(format_labels)) > 1,
        },
        "completion_release": {
            "incomplete": incomplete,
            "complete": complete,
            "unreleased": unreleased,
            "released": released,
            "contradictory": (incomplete and complete) or (unreleased and released),
        },
        "youth_audience": {
            "established": bool(youth_matches),
            "basis": "explicit youth-audience language" if youth_matches else "not established",
        },
        "semantic_attributes": {
            "women_authorship": {
                "established": women_authorship,
                "basis": "explicit authorship language" if women_authorship else "not established",
            },
            "indigenous_authorship": {
                "established": indigenous_authorship,
                "basis": "explicit authorship language" if indigenous_authorship else "not established",
            },
        },
    }


def normalise_festival_facts(festival: dict[str, Any]) -> dict[str, Any]:
    """Apply small, auditable display corrections without mutating source stores."""

    record = dict(festival)
    adjustments = []
    corrected_name = FESTIVAL_NAME_CORRECTIONS.get(record.get("id"))
    if corrected_name and record.get("name") != corrected_name:
        adjustments.append(
            {"field": "name", "source": record.get("name"), "normalized": corrected_name}
        )
        record["name"] = corrected_name
    record["data_quality_adjustments"] = adjustments
    return record


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _with_year(value: date, year: int) -> date:
    day = min(value.day, calendar.monthrange(year, value.month)[1])
    return date(year, value.month, day)


def _align_to_deadline_cycle(value: date | None, final_deadline: date) -> date | None:
    """Place a recurring month/day inside the cycle ending at final_deadline.

    Source rows are updated at different times, so their stored years can refer
    to adjacent cycles. Month/day ordering is the stable signal: an opening in
    September for a May deadline belongs to the preceding calendar year.
    """

    if not value:
        return None
    aligned = _with_year(value, final_deadline.year)
    if aligned > final_deadline:
        aligned = _with_year(value, final_deadline.year - 1)
    return aligned


def _deadline_rating(status: str, days_until: int | None, days_until_open: int | None) -> float:
    if status == "closed":
        return 1.0
    if status == "unknown":
        return 2.0
    if status == "upcoming":
        if days_until_open is not None and days_until_open <= 42:
            return 3.0
        return 2.0 if days_until_open is not None and days_until_open <= 120 else 1.0
    if days_until is None:
        return 2.0
    if days_until <= 42:
        return 5.0
    if days_until <= 90:
        return 4.0
    if days_until <= 180:
        return 3.0
    return 2.0


def assess_deadline(festival: dict[str, Any], today: date) -> dict[str, Any]:
    """Return an auditable submission-window assessment.

    Exact recorded dates win.  When the recorded cycle is stale, the same
    month/day pattern is projected forward and labelled as a projection.  The
    workbook's ``typical_deadline_month`` is used only when no final date exists.
    """

    recorded_final = _parse_date(festival.get("final_deadline"))
    recorded_open = _parse_date(festival.get("submission_open"))
    recorded_next = _parse_date(festival.get("next_deadline"))

    if recorded_final:
        if recorded_final >= today:
            target = recorded_final
            year_shift = 0
            basis = "recorded_final_deadline"
            confidence = "high"
        else:
            target = _with_year(recorded_final, today.year)
            if target < today:
                target = _with_year(recorded_final, today.year + 1)
            year_shift = target.year - recorded_final.year
            basis = "projected_annual_pattern"
            age = today.year - recorded_final.year
            confidence = "medium" if age <= 2 else "low"

        if year_shift == 0:
            projected_open = recorded_open
            projected_next = recorded_next
            if projected_open and projected_open > target:
                projected_open = _align_to_deadline_cycle(projected_open, target)
            if projected_next and projected_next > target:
                projected_next = _align_to_deadline_cycle(projected_next, target)
        else:
            projected_open = _align_to_deadline_cycle(recorded_open, target)
            projected_next = _align_to_deadline_cycle(recorded_next, target)
        cycle_anomalies = []
        if (
            projected_open
            and basis == "recorded_final_deadline"
            and (target - projected_open).days > 366
        ):
            cycle_anomalies.append("submission_open_outside_recorded_deadline_cycle")
            projected_open = None
            confidence = "low"
        if projected_open and projected_open >= target:
            cycle_anomalies.append("submission_open_not_before_final_deadline")
            projected_open = None
            confidence = "low"
        days_until = (target - today).days
        days_until_open = (projected_open - today).days if projected_open else None

        if projected_open and today < projected_open:
            status = "upcoming"
            if basis == "projected_annual_pattern":
                reason = (
                    f"The recorded {recorded_final.isoformat()} cycle is closed; the next "
                    f"window is projected to open {projected_open.isoformat()} and close "
                    f"{target.isoformat()}."
                )
            else:
                reason = (
                    f"The recorded submission window opens {projected_open.isoformat()} "
                    f"and closes {target.isoformat()}."
                )
        elif cycle_anomalies:
            status = "upcoming"
            reason = (
                f"The final deadline is {'projected' if basis == 'projected_annual_pattern' else 'recorded'} "
                f"for {target.isoformat()}, but the stored opening date is not before it; "
                "the current submission-window status must be verified."
            )
        elif basis == "projected_annual_pattern" and not projected_open:
            status = "upcoming"
            reason = (
                f"The recorded {recorded_final.isoformat()} cycle is closed and the next "
                f"final deadline is projected for {target.isoformat()}, but its opening "
                "date is unknown."
            )
        elif days_until <= 42:
            status = "closing_soon"
            verb = "recorded" if basis == "recorded_final_deadline" else "projected"
            unit = "day" if days_until == 1 else "days"
            reason = f"The {verb} final deadline is {target.isoformat()} ({days_until} {unit} away)."
        else:
            status = "open"
            verb = "recorded" if basis == "recorded_final_deadline" else "projected"
            unit = "day" if days_until == 1 else "days"
            reason = f"The {verb} final deadline is {target.isoformat()} ({days_until} {unit} away)."

        urgency = _deadline_rating(status, days_until, days_until_open)
        return {
            "status": status,
            "urgency": urgency,
            "reason": reason,
            "next_deadline": target.isoformat(),
            "next_submission_open": projected_open.isoformat() if projected_open else None,
            "next_intermediate_deadline": projected_next.isoformat() if projected_next else None,
            "recorded_final_deadline": recorded_final.isoformat(),
            "basis": basis,
            "confidence": confidence,
            "is_projection": basis == "projected_annual_pattern",
            "days_until_deadline": days_until,
            "days_until_open": days_until_open,
            "recorded_cycle_closed": recorded_final < today,
            "cycle_anomalies": cycle_anomalies,
        }

    month_name = festival.get("typical_deadline_month")
    if month_name in MONTHS:
        month = MONTHS.index(month_name) + 1
        months_until = (month - today.month) % 12
        if months_until <= 1:
            urgency = 4.0
        elif months_until == 2:
            urgency = 3.0
        elif months_until <= 4:
            urgency = 2.0
        else:
            urgency = 1.0
        return {
            # A recurring month cannot establish whether the call is currently
            # open or closing soon, so keep the state prospective and avoid
            # inventing a representative day inside the month.
            "status": "upcoming",
            "urgency": urgency,
            "reason": (
                f"Only the recurring month ({month_name}) is known; the exact deadline "
                "must be verified with the festival."
            ),
            "next_deadline": None,
            "next_submission_open": None,
            "next_intermediate_deadline": None,
            "recorded_final_deadline": None,
            "basis": "typical_month_only",
            "confidence": "low",
            "is_projection": True,
            "months_until_typical_deadline": months_until,
            "days_until_deadline": None,
            "days_until_open": None,
            "recorded_cycle_closed": None,
            "cycle_anomalies": [],
        }

    return {
        "status": "unknown",
        "urgency": 2.0,
        "reason": "No usable final deadline or recurring deadline month is available.",
        "next_deadline": None,
        "next_submission_open": None,
        "next_intermediate_deadline": None,
        "recorded_final_deadline": None,
        "basis": "missing",
        "confidence": "low",
        "is_projection": False,
        "days_until_deadline": None,
        "days_until_open": None,
        "recorded_cycle_closed": None,
        "cycle_anomalies": [],
    }


def _normalise(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def premiere_constraint(festival: dict[str, Any]) -> dict[str, Any]:
    """Interpret the workbook's premiere shorthand without overstating certainty."""

    raw = (festival.get("premiere_requirement_raw") or "").strip()
    normalised = _normalise(raw)
    fallback = _normalise(festival.get("premiere_requirement"))

    if normalised.startswith("no requirement") or fallback == "none":
        return {
            "scope": "none", "territory": None, "raw": raw or None,
            "confidence": "high", "reason": "The source records no premiere requirement.",
        }
    if not normalised or normalised.startswith("no info"):
        return {
            "scope": "unknown", "territory": None, "raw": raw or None,
            "confidence": "low", "reason": "The source does not establish a premiere rule.",
        }
    if normalised in {"world", "world premiere"}:
        return {
            "scope": "world", "territory": None, "raw": raw,
            "confidence": "high", "reason": "The source explicitly records a world-premiere requirement.",
        }

    if normalised.startswith("world "):
        territory_raw = re.sub(r"^world\s*-?\s*", "", raw, flags=re.I).strip(" -")
        territory_key = _normalise(territory_raw)
        if territory_key == "international":
            scope, territory = "international", "International"
        elif territory_key in CONTINENT_TERRITORIES:
            scope, territory = "continental", CONTINENT_TERRITORIES[territory_key]
        else:
            scope = "territorial"
            territory = TERRITORY_ALIASES.get(
                territory_key, territory_raw or festival.get("country")
            )
        return {
            "scope": scope,
            "territory": territory,
            "raw": raw,
            "confidence": "medium",
            "reason": (
                f"The source shorthand '{raw}' is treated as allowing a premiere between "
                f"world level and the stated {territory or 'territorial'} level; official rules must be verified."
            ),
        }

    return {
        "scope": "unknown", "territory": raw or None, "raw": raw or None,
        "confidence": "low", "reason": f"Unrecognised premiere shorthand '{raw}'; verify official rules.",
    }


def runtime_constraint(festival: dict[str, Any]) -> dict[str, Any] | None:
    text = " ".join([festival.get("focus") or "", festival.get("notes") or ""])
    match = re.search(
        r"(\d+)\s*(?:-|–|—)\s*(?:to\s*)?(\d+)\s*[- ]?minute",
        text,
        flags=re.I,
    )
    if not match:
        return None
    minimum, maximum = int(match.group(1)), int(match.group(2))
    return {
        "minimum_minutes": minimum,
        "maximum_minutes": maximum,
        "source_excerpt": match.group(0),
        "confidence": "medium",
    }


def assess_premiere(profile: dict[str, Any], festival: dict[str, Any]) -> dict[str, Any]:
    constraint = premiere_constraint(festival)
    film_format = profile.get("format")
    accepts = festival.get("accepts") or []
    status = profile.get("premiere_status") or "unknown"
    history = [row for row in (profile.get("premiere_history") or []) if isinstance(row, dict)]
    public_online = any(
        str(row.get("event_kind") or "").casefold() == "online_availability"
        for row in history
    )
    film_country = _normalise(profile.get("country"))
    screened_countries = {
        _normalise(row.get("country")) for row in history if _normalise(row.get("country"))
    }
    runtime_rule = runtime_constraint(festival)
    if history and status == "world_premiere_available":
        status = "already_premiered"
    if status == "international_premiere_available" and any(
        country != film_country for country in screened_countries
    ):
        status = "already_premiered"

    if film_format and accepts and film_format not in accepts:
        return {
            "premiere_risk": "none",
            "premiere_opportunity": False,
            "eligible": False,
            "reason": f"The source does not list {film_format} among the festival's accepted formats.",
            "constraint": constraint,
            "runtime_constraint": runtime_rule,
            "runtime_warning": None,
            "eligibility_issue": "format_not_accepted",
        }
    runtime = profile.get("runtime_minutes")
    runtime_mismatch = runtime_rule and isinstance(runtime, (int, float)) and not (
        runtime_rule["minimum_minutes"] <= runtime <= runtime_rule["maximum_minutes"]
    )
    runtime_warning = None
    if runtime_mismatch:
        runtime_warning = (
            f"The film is {runtime:g} minutes, outside the descriptive "
            f"{runtime_rule['minimum_minutes']}-{runtime_rule['maximum_minutes']} minute "
            "range; this is not treated as an official eligibility rule and must be verified."
        )

    scope = constraint["scope"]
    if scope == "none":
        risk, opportunity, eligible = "none", False, True
        reason = "No premiere restriction is recorded."
        verification_required = False
    elif public_online:
        raw_rule = str(constraint.get("raw") or "")
        online_disqualifies = bool(
            re.search(
                r"\b(?:online|streaming|internet|digital)\b.{0,50}"
                r"\b(?:disqualif|not\s+permitted|not\s+allowed|ineligible|prohibited)\w*\b"
                r"|\bno\s+prior\s+(?:public\s+)?(?:online|streaming|internet|digital)\b",
                raw_rule,
                flags=re.I,
            )
        )
        online_allowed = bool(
            re.search(
                r"\b(?:online|streaming|internet|digital)\b.{0,50}"
                r"\b(?:allowed|permitted|accepted|does\s+not\s+affect)\b",
                raw_rule,
                flags=re.I,
            )
        )
        if online_disqualifies:
            risk, opportunity, eligible = "high", False, False
            verification_required = False
            reason = (
                "Unrestricted public online availability is recorded, and the festival's "
                "retrieved rule explicitly treats that availability as disqualifying."
            )
        elif online_allowed:
            risk, opportunity, eligible = "low", False, True
            verification_required = False
            reason = (
                "Unrestricted public online availability is recorded, and the festival's "
                "retrieved rule explicitly permits it."
            )
        else:
            risk, opportunity, eligible = "medium", False, True
            verification_required = True
            reason = (
                "Unrestricted public online availability is recorded, but the current "
                f"festival evidence does not establish how it affects the {scope} premiere "
                "requirement; verify the official rule before submission."
            )
    elif scope == "unknown":
        risk, opportunity, eligible = "medium", False, True
        verification_required = True
        reason = "Premiere eligibility is unknown and must be confirmed before submission."
    elif status == "world_premiere_available":
        risk, opportunity, eligible = "none", True, True
        verification_required = False
        reason = f"The film still holds its world premiere, so the {scope} premiere constraint is currently satisfiable."
    elif status == "international_premiere_available":
        verification_required = False
        if scope == "world":
            risk, opportunity, eligible = "high", False, False
            reason = "The world premiere is no longer available, so a strict world-premiere requirement cannot be met."
        elif scope == "international":
            risk, opportunity, eligible = "none", True, True
            reason = "The film still holds its international premiere, so the requirement is currently satisfiable."
        elif scope == "territorial" and _normalise(festival.get("country")) == film_country:
            risk, opportunity, eligible = "high", False, False
            reason = "A domestic screening has already occurred, so the festival's territorial premiere is no longer available."
        else:
            risk, opportunity, eligible = "medium", False, True
            verification_required = True
            reason = f"The international premiere remains available, but the {scope} rule needs a territory-specific check."
    elif status == "already_premiered":
        verification_required = False
        if scope == "world":
            risk, opportunity, eligible = "high", False, False
            reason = "The film has already premiered, so a strict world-premiere requirement cannot be met."
        elif scope == "international" and screened_countries and any(
            country != film_country for country in screened_countries
        ):
            risk, opportunity, eligible = "high", False, False
            reason = "The recorded screening history includes an international screening, so the international premiere is consumed."
        elif scope == "territorial":
            territory = _normalise(constraint.get("territory"))
            candidate_country = _normalise(festival.get("country"))
            consumed = bool(screened_countries & {territory, candidate_country})
            if consumed:
                risk, opportunity, eligible = "high", False, False
                reason = "The recorded screening history already consumes this territorial premiere."
            else:
                risk, opportunity, eligible = "medium", False, True
                verification_required = True
                reason = "The film has premiered elsewhere; this territorial premiere may remain, subject to official verification."
        else:
            risk, opportunity, eligible = "medium", False, True
            verification_required = True
            reason = f"The film has premiered; the remaining {scope} premiere must be verified from screening history."
    else:
        risk, opportunity, eligible = "medium", False, True
        verification_required = True
        reason = "The film's premiere status is unknown, so eligibility cannot yet be confirmed."

    if runtime_warning:
        reason = f"{reason} {runtime_warning}"

    return {
        "premiere_risk": risk,
        "premiere_opportunity": opportunity,
        "eligible": eligible,
        "reason": reason,
        "constraint": constraint,
        "runtime_constraint": runtime_rule,
        "runtime_warning": runtime_warning,
        "verification_required": verification_required,
        "eligibility_issue": None,
    }


def assess_candidate(profile: dict[str, Any], festival: dict[str, Any], today: date) -> dict[str, Any]:
    deadline = assess_deadline(festival, today)
    premiere = assess_premiere(profile, festival)
    uncertainties = []
    if deadline["confidence"] != "high":
        uncertainties.append(deadline["reason"])
    if premiere["constraint"]["confidence"] != "high":
        uncertainties.append(premiere["constraint"]["reason"])
    if premiere.get("verification_required") and premiere["reason"] not in uncertainties:
        uncertainties.append(premiere["reason"])
    if premiere.get("runtime_warning"):
        uncertainties.append(premiere["runtime_warning"])
    return {
        "id": festival.get("id"),
        "premiere_risk": premiere["premiere_risk"],
        "premiere_opportunity": premiere["premiere_opportunity"],
        "eligible": premiere["eligible"],
        "deadline_status": deadline["status"],
        "deadline": deadline,
        "premiere_constraint": premiere["constraint"],
        "runtime_constraint": premiere.get("runtime_constraint"),
        "runtime_warning": premiere.get("runtime_warning"),
        "eligibility_issue": premiere.get("eligibility_issue"),
        "risk_note": f"{premiere['reason']} {deadline['reason']}",
        "uncertainties": uncertainties,
    }


def _territory_matches_target(constraint: dict[str, Any], candidate: dict[str, Any], target: dict[str, Any]) -> bool:
    scope = constraint.get("scope")
    territory = _normalise(constraint.get("territory"))
    if scope == "continental":
        target_region = _normalise(target.get("region"))
        if territory == "europe":
            return "europe" in target_region
        return bool(territory and territory in target_region)
    if scope == "territorial":
        target_country = _normalise(target.get("country"))
        target_city = _normalise(target.get("city"))
        candidate_country = _normalise(candidate.get("country"))
        return bool(
            territory
            and (territory in {target_country, target_city} or candidate_country == target_country)
        )
    return False


def post_target_compatibility(
    candidate: dict[str, Any], target: dict[str, Any] | None, film_country: str | None
) -> dict[str, str]:
    if not target:
        return {
            "status": "not_applicable",
            "reason": "No premiere target was selected, so no post-target sequence is asserted.",
        }
    if candidate.get("id") == target.get("id"):
        return {"status": "target", "reason": "This is the intended premiere target."}
    constraint = candidate.get("premiere_constraint") or {}
    scope = constraint.get("scope")
    if scope == "none":
        return {"status": "compatible", "reason": "No premiere restriction is recorded."}
    if scope == "world":
        return {
            "status": "backup_only",
            "reason": "A strict world-premiere festival can only replace, not follow, the chosen world-premiere target.",
        }
    if scope == "international":
        if _normalise(target.get("country")) != _normalise(film_country):
            return {
                "status": "backup_only",
                "reason": "An international world premiere would consume the film's international premiere.",
            }
        return {"status": "compatible", "reason": "A domestic world premiere can preserve the international premiere."}
    if scope in {"continental", "territorial"}:
        if _territory_matches_target(constraint, candidate, target):
            return {
                "status": "backup_only",
                "reason": f"The chosen premiere would consume this festival's {scope} premiere territory.",
            }
        return {
            "status": "compatible",
            "reason": f"The chosen premiere is outside this festival's stated {scope} territory.",
        }
    return {"status": "verify", "reason": "Premiere compatibility cannot be established from the source data."}


def pre_target_compatibility(
    candidate: dict[str, Any], target: dict[str, Any] | None, film_country: str | None
) -> dict[str, str]:
    """Whether screening this candidate before the target would consume its premiere."""

    if not target:
        return {
            "status": "not_applicable",
            "reason": "No premiere target was selected, so no pre-target sequence is asserted.",
        }
    if candidate.get("id") == target.get("id"):
        return {"status": "target", "reason": "This is the intended premiere target."}

    constraint = target.get("premiere_constraint") or {}
    scope = constraint.get("scope")
    if scope == "world":
        return {
            "status": "must_follow_target",
            "reason": "Any earlier public screening would consume the target's world premiere.",
        }
    if scope == "international":
        if _normalise(candidate.get("country")) != _normalise(film_country):
            return {
                "status": "must_follow_target",
                "reason": "Under the current source interpretation, an earlier international screening would consume the target's international premiere.",
            }
        return {
            "status": "compatible_before_target",
            "reason": "A domestic screening can preserve the target's international premiere.",
        }
    if scope in {"continental", "territorial"}:
        if _territory_matches_target(constraint, target, candidate):
            return {
                "status": "must_follow_target",
                "reason": f"Under the current source interpretation, an earlier screening here would consume the target's {scope} premiere territory.",
            }
        return {
            "status": "compatible_before_target",
            "reason": f"This screening is outside the target's stated {scope} premiere territory.",
        }
    return {
        "status": "verify_before_target",
        "reason": "Verify whether an earlier screening would consume the selected premiere target.",
    }
