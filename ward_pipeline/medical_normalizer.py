from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml


LEXICON_PATH = Path(__file__).with_name("medical_lexicon.yml")
HIGH_CONFIDENCE = "high"
STT_WARNING_TERMS = {
    "medication": (
        "aspirin",
        "clopidogrel",
        "plavix",
        "warfarin",
        "heparin",
        "enoxaparin",
        "insulin",
        "metformin",
        "ceftriaxone",
        "azithromycin",
        "vancomycin",
        "statin",
        "atorvastatin",
        "rosuvastatin",
        "amlodipine",
        "bisoprolol",
        "furosemide",
    ),
    "lab": (
        "wbc",
        "crp",
        "creatinine",
        "hemoglobin",
        "platelet",
        "sodium",
        "potassium",
        "glucose",
        "hba1c",
        "troponin",
        "lactate",
        "bnp",
        "nt-probnp",
        "spo2",
    ),
}
STT_WARNING_NUMERIC_CONTEXT = re.compile(
    r"(\d+(\.\d+)?\s*(mg|mcg|g|unit|units|u|iu|ml|meq|mmol|mmhg|bpm|/min|%|度|顆|錠|次|天|週|周|日|分|小時|cc)?)",
    re.IGNORECASE,
)


def _load_lexicon(path: Path = LEXICON_PATH) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(category): list(entries or []) for category, entries in payload.items()}


def _pattern(term: str) -> re.Pattern[str]:
    if re.search(r"[A-Za-z0-9]", term):
        return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(re.escape(term))


def _line_number(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _replacement_label(original: str, corrected: str) -> str:
    if original == corrected:
        return corrected
    return corrected


def _context_window(text: str, start: int, end: int, radius: int = 36) -> str:
    return text[max(0, start - radius) : min(len(text), end + radius)]


def _stt_warning_terms(text: str) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for category, terms in STT_WARNING_TERMS.items():
        for term in terms:
            for match in _pattern(term).finditer(text):
                context = _context_window(text, match.start(), match.end())
                reason = f"possible STT {category} term; verify against audio/source transcript before using in SOAP"
                if STT_WARNING_NUMERIC_CONTEXT.search(context):
                    reason = f"possible STT {category} term with nearby numeric context; verify exact value/dose/result before using in SOAP"
                key = (category, match.group(0).lower(), _line_number(text, match.start()))
                if key in seen:
                    continue
                seen.add(key)
                warnings.append(
                    {
                        "original": match.group(0),
                        "candidate": match.group(0),
                        "reason": reason,
                        "confidence": "stt_warning",
                        "category": category,
                        "line": _line_number(text, match.start()),
                        "context": context.strip(),
                        "requires_human_confirmation": True,
                    }
                )
    return warnings


def normalize_transcript(text: str, lexicon_path: Path = LEXICON_PATH) -> dict[str, Any]:
    """Conservative medical transcript normalization.

    Only high-confidence lexicon entries are changed automatically. Medium and
    low confidence matches are surfaced for clinician/SOAP review.
    """
    lexicon = _load_lexicon(lexicon_path)
    normalized = text
    corrections: list[dict[str, Any]] = []
    uncertain_terms: list[dict[str, Any]] = []

    for category, entries in lexicon.items():
        for entry in entries:
            canonical = str(entry.get("canonical") or "").strip()
            confidence = str(entry.get("confidence") or "").strip().lower()
            variants = [str(item).strip() for item in (entry.get("variants") or []) if str(item).strip()]
            if not canonical or not variants:
                continue

            for variant in sorted(set(variants), key=len, reverse=True):
                pattern = _pattern(variant)
                source_text = normalized if confidence == HIGH_CONFIDENCE else text
                matches = list(pattern.finditer(source_text))
                if not matches:
                    continue

                if confidence == HIGH_CONFIDENCE:
                    def replace(match: re.Match[str]) -> str:
                        original = match.group(0)
                        if original == canonical:
                            return original
                        corrections.append(
                            {
                                "original": original,
                                "corrected": _replacement_label(original, canonical),
                                "reason": f"high-confidence {category} lexicon match",
                                "confidence": confidence,
                                "category": category,
                                "line": _line_number(normalized, match.start()),
                            }
                        )
                        return canonical

                    normalized = pattern.sub(replace, normalized)
                else:
                    for match in matches:
                        uncertain_terms.append(
                            {
                                "original": match.group(0),
                                "candidate": canonical,
                                "reason": f"possible {category} term from {confidence}-confidence lexicon match",
                                "confidence": confidence or "unknown",
                                "category": category,
                                "line": _line_number(text, match.start()),
                                "requires_human_confirmation": True,
                            }
                        )

    stt_warnings = _stt_warning_terms(normalized)
    all_uncertain_terms = uncertain_terms + stt_warnings

    return {
        "normalized_transcript": normalized,
        "correction_log": corrections,
        "uncertain_terms": all_uncertain_terms,
        "summary": {
            "corrections": len(corrections),
            "uncertain_terms": len(all_uncertain_terms),
            "stt_warnings": len(stt_warnings),
            "lexicon_path": str(lexicon_path),
        },
    }


def dumps_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
