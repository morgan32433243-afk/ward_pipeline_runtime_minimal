#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PHASE1_CANONICAL_SPECIALTIES = {
    "cardiology",
    "pulmonology",
    "gastroenterology_hepatology",
    "nephrology",
    "endocrinology",
    "infectious_disease",
    "hematology_oncology",
    "rheumatology",
    "neurology",
    "critical_care",
    "general_internal_medicine",
}
PHASE1_MIN_COUNTS = {
    "strong_keywords": 30,
    "aliases": 20,
    "weak_clues": 10,
}
REQUIRED_SPECIALTY_FIELDS = (
    "display_name",
    "obsidian_folder",
    "strong_keywords",
    "aliases",
    "weak_clues",
    "medications",
    "labs_imaging",
    "procedures",
    "negative_context",
    "legacy_aliases",
)
BROAD_TERMS_NOT_STRONG = {
    "cardiology": {"troponin"},
    "infectious_disease": {"fever"},
    "pulmonology": {"dyspnea"},
    "nephrology": {"creatinine"},
}
BROAD_STRONG_KEYWORDS = {
    "pain",
    "fever",
    "dyspnea",
    "creatinine",
    "troponin",
    "oxygen",
    "weakness",
    "cough",
    "fatigue",
    "dizziness",
    "nausea",
    "vomiting",
    "edema",
}
ALLOWED_SHARED_ALIASES = {
    "ards",
    "bipap",
    "cap",
    "crrt",
    "dm",
    "hap",
    "pcp",
    "pd",
    "pe",
    "pjp",
    "rrt",
    "vap",
}


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return payload


def _require_nonempty_strings(path: Path, owner: str, values: list) -> None:
    if not isinstance(values, list) or not values:
        raise ValueError(f"{path}: {owner} must be a non-empty list")
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{path}: {owner} contains an empty/non-string value")


def validate_specialty_map(path: Path) -> int:
    payload = _load_yaml(path)
    if payload.get("taxonomy_type") != "clinical_specialty_map":
        raise ValueError(f"{path}: taxonomy_type must be clinical_specialty_map")
    seen: set[str] = set()
    specialties = payload.get("specialties")
    if not isinstance(specialties, dict):
        raise ValueError(f"{path}: specialties must be a mapping keyed by canonical specialty id")
    aliases_by_term: dict[str, set[str]] = {}
    for specialty_id, item in specialties.items():
        specialty_id = str(specialty_id or "").strip()
        if not specialty_id or not isinstance(item, dict):
            raise ValueError(f"{path}: specialty entries must be mappings keyed by id")
        if specialty_id in seen:
            raise ValueError(f"{path}: duplicate specialty id {specialty_id}")
        seen.add(specialty_id)
        for field in REQUIRED_SPECIALTY_FIELDS:
            if field not in item:
                raise ValueError(f"{path}: {specialty_id} missing required field {field}")
            if field not in {"display_name", "obsidian_folder"} and not isinstance(item.get(field), list):
                raise ValueError(f"{path}: {specialty_id}.{field} must be a list")
        _require_nonempty_strings(path, f"{specialty_id}.strong_keywords", item.get("strong_keywords"))
        for field, minimum in PHASE1_MIN_COUNTS.items():
            values = item.get(field) or []
            if len(values) < minimum:
                raise ValueError(f"{path}: {specialty_id}.{field} needs at least {minimum} entries")
            if len(values) != len(set(values)):
                raise ValueError(f"{path}: {specialty_id}.{field} contains duplicate entries")
        broad_terms = BROAD_TERMS_NOT_STRONG.get(specialty_id, set())
        strong_terms = {str(value).casefold() for value in item.get("strong_keywords") or []}
        disallowed = sorted(broad_terms & strong_terms)
        if disallowed:
            raise ValueError(f"{path}: {specialty_id}.strong_keywords contains broad clue terms {disallowed}")
        vague_terms = sorted(BROAD_STRONG_KEYWORDS & strong_terms)
        if vague_terms:
            raise ValueError(f"{path}: {specialty_id}.strong_keywords contains overly vague terms {vague_terms}")
        for field in ("aliases", "weak_clues"):
            if field in item:
                _require_nonempty_strings(path, f"{specialty_id}.{field}", item.get(field))
        for alias in item.get("aliases") or []:
            normalized_alias = str(alias).strip().casefold()
            if normalized_alias:
                aliases_by_term.setdefault(normalized_alias, set()).add(specialty_id)
        if specialty_id == "critical_care":
            required_any = item.get("required_any") or []
            _require_nonempty_strings(path, "critical_care.required_any", required_any)
            required_set = {str(value).casefold() for value in required_any}
            for required_term in ("shock", "vasopressor", "mechanical ventilation", "icu"):
                if required_term not in required_set:
                    raise ValueError(f"{path}: critical_care.required_any missing {required_term}")
    if seen != PHASE1_CANONICAL_SPECIALTIES:
        missing = sorted(PHASE1_CANONICAL_SPECIALTIES - seen)
        extra = sorted(seen - PHASE1_CANONICAL_SPECIALTIES)
        raise ValueError(f"{path}: canonical specialty mismatch missing={missing} extra={extra}")
    gi = specialties.get("gastroenterology_hepatology") or {}
    gi_aliases = set(gi.get("legacy_aliases") or [])
    if not {"gastroenterology", "hepatobiliary_pancreatic"}.issubset(gi_aliases):
        raise ValueError(f"{path}: gastroenterology_hepatology must retain GI/hepatobiliary legacy aliases")
    conflicts = {
        alias: sorted(owners)
        for alias, owners in aliases_by_term.items()
        if len(owners) > 1 and alias not in ALLOWED_SHARED_ALIASES
    }
    if conflicts:
        raise ValueError(f"{path}: alias conflict across specialties {conflicts}")
    return len(seen)


def _valid_specialty_ids(path: Path) -> set[str]:
    payload = _load_yaml(path)
    specialties = payload.get("specialties") or {}
    ids = set(str(value).strip() for value in specialties if str(value).strip())
    for item in payload.get("legacy_specialties") or []:
        if isinstance(item, dict) and str(item.get("id") or "").strip():
            ids.add(str(item["id"]).strip())
    return ids


def validate_literature_taxonomy(path: Path, *, approved: bool = False, specialty_ids: set[str] | None = None) -> int:
    payload = _load_yaml(path)
    seen: set[tuple[str, str]] = set()
    for item in payload.get("items") or []:
        kind = str(item.get("kind") or "").strip()
        label = str(item.get("label") or "").strip()
        if kind not in {"service", "diagnosis"} or not label:
            raise ValueError(f"{path}: each item needs kind service/diagnosis and label")
        key = (kind, label)
        if key in seen:
            raise ValueError(f"{path}: duplicate item {kind}:{label}")
        seen.add(key)
        _require_nonempty_strings(path, f"{kind}:{label}.keywords", item.get("keywords"))
        if approved:
            specialty_primary = str(item.get("specialty_primary") or "").strip()
            if not specialty_primary:
                raise ValueError(f"{path}: {kind}:{label}.specialty_primary is required")
            if specialty_ids is not None and specialty_primary not in specialty_ids:
                raise ValueError(f"{path}: {kind}:{label}.specialty_primary {specialty_primary} is not in clinical_specialty_map.yml")
            confidence = item.get("confidence")
            if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
                raise ValueError(f"{path}: {kind}:{label}.confidence must be a number between 0 and 1")
            source_trace = item.get("source_trace")
            _require_nonempty_strings(path, f"{kind}:{label}.source_trace", source_trace)
    for item in payload.get("problem_keywords") or []:
        label = str(item.get("label") or "").strip()
        if not label:
            raise ValueError(f"{path}: problem keyword label is required")
        _require_nonempty_strings(path, f"problem:{label}.keywords", item.get("keywords"))
    return len(seen)


def main() -> int:
    clinical_path = ROOT / "clinical_specialty_map.yml"
    specialty_ids = _valid_specialty_ids(clinical_path)
    counts = {
        "specialties": validate_specialty_map(clinical_path),
        "approved_items": validate_literature_taxonomy(ROOT / "literature_taxonomy_approved.yml", approved=True, specialty_ids=specialty_ids),
        "candidate_items": validate_literature_taxonomy(ROOT / "literature_taxonomy_candidates.yml"),
    }
    print(json.dumps({"ok": True, "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
