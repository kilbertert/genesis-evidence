"""Review-first PICO and evidence-claim extraction contracts."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PicoProfile:
    population: tuple[str, ...]
    intervention: tuple[str, ...]
    comparator: tuple[str, ...]
    outcomes: tuple[str, ...]
    study_design: str | None


@dataclass(frozen=True, slots=True)
class EvidenceClaim:
    claim_text: str
    direction: str
    source_section: str
    source_excerpt: str
    confidence: float
    review_status: str = "pending_human_review"


@dataclass(frozen=True, slots=True)
class InterventionDetail:
    intervention_name: str
    ingredient_form_strain: str
    dose_value: float | None
    dose_unit: str
    frequency: str
    duration: str
    route: str
    baseline_nutrient_status: str
    adverse_events: tuple[str, ...]
    eligibility_exclusion: tuple[str, ...]
    source_section: str
    source_excerpt: str
    extraction_confidence: float
    review_status: str = "pending_human_review"


@dataclass(frozen=True, slots=True)
class EvidenceExtraction:
    pico: PicoProfile
    claims: tuple[EvidenceClaim, ...]
    intervention_details: tuple[InterventionDetail, ...]
    extractor_name: str
    extractor_version: str
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class HeuristicPicoEvidenceExtractor:
    """Conservative baseline used to populate a human-review queue.

    This extractor intentionally produces low-confidence candidates. It is not a
    clinical conclusion engine and its output must not be shown to end users
    without evidence review.
    """

    name = "heuristic-pico-claim"
    version = "2"

    _population_patterns = (
        r"\bparticipants?\b",
        r"\bpatients?\b",
        r"\bsubjects?\b",
        r"\badults?\b",
        r"\bolder (?:adults?|people|patients?)\b",
        r"\bmen\b|\bwomen\b",
    )
    _intervention_patterns = (
        r"\bsupplement(?:ation|ed|s)?\b",
        r"\bintervention\b",
        r"\btreatment\b",
        r"\bdiet(?:ary)?\b",
        r"\bprotein\b",
        r"\bvitamin\b",
        r"\bmineral\b",
        r"\bnutr(?:ient|ition|itional)\b",
    )
    _comparator_patterns = (
        r"\bplacebo\b",
        r"\bcontrol group\b",
        r"\busual care\b",
        r"\bcompared with\b",
        r"\bversus\b|\bvs\.?\b",
    )
    _outcome_patterns = (
        r"\boutcome\b",
        r"\bmortality\b",
        r"\bincidence\b",
        r"\brisk\b",
        r"\bstrength\b",
        r"\bfunction\b",
        r"\bquality of life\b",
        r"\bbiomarker\b",
        r"\bserum\b",
    )
    _claim_patterns = (
        r"\bsignificant(?:ly)?\b",
        r"\bassociated with\b",
        r"\bincreased?\b",
        r"\bdecreased?\b",
        r"\bimproved?\b",
        r"\breduced?\b",
        r"\bno (?:significant )?(?:difference|effect|association)\b",
    )

    def extract(self, document: dict[str, Any]) -> EvidenceExtraction:
        abstract = _clean(document.get("abstract"))
        sections = _sections(document)
        all_sentences = _sentences(abstract) + [
            (title, sentence)
            for title, text in sections
            for _, sentence in _sentences(text, default_section=title)
        ]
        pico = PicoProfile(
            population=_matching_sentences(all_sentences, self._population_patterns),
            intervention=_matching_sentences(all_sentences, self._intervention_patterns),
            comparator=_matching_sentences(all_sentences, self._comparator_patterns),
            outcomes=_matching_sentences(all_sentences, self._outcome_patterns),
            study_design=_study_design(f"{abstract} {' '.join(text for _, text in sections)}"),
        )
        claims: list[EvidenceClaim] = []
        preferred = [
            (section, sentence)
            for section, sentence in all_sentences
            if any(key in section.casefold() for key in ("result", "conclusion", "discussion"))
        ]
        candidates = preferred or all_sentences
        for section, sentence in candidates:
            if not _matches(sentence, self._claim_patterns):
                continue
            claims.append(
                EvidenceClaim(
                    claim_text=sentence,
                    direction=_direction(sentence),
                    source_section=section,
                    source_excerpt=sentence,
                    confidence=0.35 if preferred else 0.2,
                )
            )
            if len(claims) >= 10:
                break
        intervention_details = _intervention_details(all_sentences)
        return EvidenceExtraction(
            pico=pico,
            claims=tuple(claims),
            intervention_details=intervention_details,
            extractor_name=self.name,
            extractor_version=self.version,
        )


def _sections(document: dict[str, Any]) -> list[tuple[str, str]]:
    raw = document.get("sections")
    if not isinstance(raw, (list, tuple)):
        return []
    sections: list[tuple[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title")) or "Body"
        text = _clean(item.get("text"))
        if text:
            sections.append((title, text))
    return sections


def _sentences(text: str, *, default_section: str = "Abstract") -> list[tuple[str, str]]:
    if not text:
        return []
    values = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text)
    return [
        (default_section, value[:2000])
        for raw in values
        if (value := " ".join(raw.split())) and len(value) >= 20
    ]


def _matching_sentences(
    sentences: list[tuple[str, str]], patterns: tuple[str, ...], *, limit: int = 5
) -> tuple[str, ...]:
    matched: list[str] = []
    for _, sentence in sentences:
        if _matches(sentence, patterns) and sentence not in matched:
            matched.append(sentence)
        if len(matched) >= limit:
            break
    return tuple(matched)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _study_design(text: str) -> str | None:
    normalized = text.casefold()
    for label, patterns in (
        ("systematic_review_meta_analysis", ("systematic review", "meta-analysis")),
        ("randomized_controlled_trial", ("randomized controlled", "randomised controlled")),
        ("cohort_study", ("cohort study", "prospective cohort", "retrospective cohort")),
        ("case_control_study", ("case-control", "case control")),
        ("cross_sectional_study", ("cross-sectional", "cross sectional")),
    ):
        if any(pattern in normalized for pattern in patterns):
            return label
    return None


_INGREDIENTS = (
    ("Omega-3 EPA/DHA", r"\b(?:omega[- ]?3|epa|dha|fish oil)\b"),
    ("Vitamin D", r"\b(?:vitamin d3?|cholecalciferol)\b"),
    ("Vitamin B12", r"\b(?:vitamin b12|cobalamin|methylcobalamin|cyanocobalamin)\b"),
    ("Iron", r"\b(?:iron|ferrous sulfate|ferrous fumarate|ferric)\b"),
    ("Protein", r"\b(?:protein|whey|casein|oral nutrition)\b"),
    ("Soluble fiber", r"\b(?:soluble fiber|fibre|psyllium|beta-glucan)\b"),
    ("Calcium", r"\bcalcium\b"),
)
_DOSE_RE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mcg|μg|ug|mg|g|kg|iu|kcal|ml)"
    r"(?:\s*(?:/|per)\s*(?:day|daily|d|week|wk))?",
    flags=re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"\b(?:for|over|during)\s+(\d+(?:\.\d+)?)\s*"
    r"(days?|weeks?|months?|years?)\b",
    flags=re.IGNORECASE,
)
_FREQUENCY_RE = re.compile(
    r"\b(?:once|twice|three times|four times)\s+(?:a|per)\s+day\b|"
    r"\b(?:daily|weekly|monthly|every other day)\b|\b\d+\s*(?:x|times)\s*/\s*day\b",
    flags=re.IGNORECASE,
)


def _intervention_details(
    sentences: list[tuple[str, str]], *, limit: int = 10
) -> tuple[InterventionDetail, ...]:
    adverse_events = tuple(
        sentence
        for _, sentence in sentences
        if re.search(
            r"\b(?:adverse events?|side effects?|safety|well tolerated|tolerability)\b",
            sentence,
            flags=re.IGNORECASE,
        )
    )[:3]
    eligibility = tuple(
        sentence
        for _, sentence in sentences
        if re.search(
            r"\b(?:eligib|inclusion criteria|exclusion criteria|excluded?)\b",
            sentence,
            flags=re.IGNORECASE,
        )
    )[:3]
    baseline = next(
        (
            sentence
            for _, sentence in sentences
            if re.search(
                r"\b(?:baseline|deficien|insufficien|nutrient status|serum level)\b",
                sentence,
                flags=re.IGNORECASE,
            )
        ),
        "",
    )
    details: list[InterventionDetail] = []
    seen: set[tuple[str, str, float | None, str]] = set()
    for section, sentence in sentences:
        ingredient_name, ingredient_form = _ingredient(sentence)
        dose = _DOSE_RE.search(sentence)
        if not ingredient_name or not (
            dose
            or re.search(
                r"\b(?:supplement|intervention|treatment|received|administered)\b",
                sentence,
                flags=re.IGNORECASE,
            )
        ):
            continue
        dose_value = float(dose.group("value")) if dose else None
        dose_unit = dose.group("unit") if dose else ""
        key = (ingredient_name, ingredient_form, dose_value, dose_unit.casefold())
        if key in seen:
            continue
        seen.add(key)
        frequency = _match_text(_FREQUENCY_RE, sentence)
        duration = _match_text(_DURATION_RE, sentence)
        route = _route(sentence)
        confidence = 0.6 if dose and frequency else 0.5 if dose else 0.35
        details.append(
            InterventionDetail(
                intervention_name=ingredient_name,
                ingredient_form_strain=ingredient_form,
                dose_value=dose_value,
                dose_unit=dose_unit,
                frequency=frequency,
                duration=duration,
                route=route,
                baseline_nutrient_status=baseline,
                adverse_events=adverse_events,
                eligibility_exclusion=eligibility,
                source_section=section,
                source_excerpt=sentence,
                extraction_confidence=confidence,
            )
        )
        if len(details) >= limit:
            break
    details.sort(
        key=lambda item: (
            item.dose_value is not None,
            bool(item.frequency),
            bool(item.duration),
            item.extraction_confidence,
        ),
        reverse=True,
    )
    return tuple(details)


def _ingredient(sentence: str) -> tuple[str, str]:
    for name, pattern in _INGREDIENTS:
        match = re.search(pattern, sentence, flags=re.IGNORECASE)
        if match:
            return name, match.group(0)
    return "", ""


def _route(sentence: str) -> str:
    normalized = sentence.casefold()
    if "intravenous" in normalized or re.search(r"\biv\b", normalized):
        return "intravenous"
    if any(value in normalized for value in ("oral", "capsule", "tablet", "drink", "powder")):
        return "oral"
    return "unspecified"


def _match_text(pattern: re.Pattern[str], sentence: str) -> str:
    match = pattern.search(sentence)
    return match.group(0) if match else ""


def _direction(sentence: str) -> str:
    normalized = sentence.casefold()
    if re.search(r"\bno (?:significant )?(?:difference|effect|association)\b", normalized):
        return "no_effect"
    positive = bool(re.search(r"\b(improved?|increased?|higher|benefit)\b", normalized))
    negative = bool(re.search(r"\b(reduced?|decreased?|lower|harm|adverse)\b", normalized))
    if positive and negative:
        return "mixed"
    if positive:
        return "positive"
    if negative:
        return "negative"
    return "unclear"


def _clean(value: object) -> str:
    return " ".join(str(value or "").split())
