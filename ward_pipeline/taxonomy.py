from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_DIR = REPO_ROOT / "taxonomy"
CLINICAL_SPECIALTY_MAP_FILE = TAXONOMY_DIR / "clinical_specialty_map.yml"
LITERATURE_TAXONOMY_APPROVED_FILE = TAXONOMY_DIR / "literature_taxonomy_approved.yml"
LITERATURE_TAXONOMY_CANDIDATES_FILE = TAXONOMY_DIR / "literature_taxonomy_candidates.yml"


def load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return {"_load_error": str(exc)}
    return loaded if isinstance(loaded, dict) else {}


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _clean_terms(values: Any) -> tuple[str, ...]:
    if not isinstance(values, list):
        return ()
    return tuple(str(value).strip() for value in values if str(value).strip())


def _canonical_specialty_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("specialties"), dict):
        items = []
        for specialty_id, item in payload["specialties"].items():
            if isinstance(item, dict):
                items.append({"id": str(specialty_id), **item})
        return items
    items = payload.get("canonical_specialties") or payload.get("specialties") or []
    return [item for item in items if isinstance(item, dict)]


def _classification_specialty_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    canonical_items = _canonical_specialty_items(payload)
    canonical_ids = {str(item.get("id") or "").strip() for item in canonical_items}
    legacy_aliases: set[str] = set()
    for item in canonical_items:
        legacy_aliases.update(_clean_terms(item.get("legacy_aliases")))
    items = list(canonical_items)
    for item in payload.get("legacy_specialties") or []:
        if not isinstance(item, dict):
            continue
        specialty_id = str(item.get("id") or "").strip()
        if specialty_id and specialty_id not in canonical_ids and specialty_id not in legacy_aliases:
            items.append(item)
    return items


@lru_cache(maxsize=1)
def load_specialty_records() -> tuple[dict[str, Any], ...]:
    payload = load_yaml_dict(CLINICAL_SPECIALTY_MAP_FILE)
    if payload.get("_load_error"):
        return ()
    records: list[dict[str, Any]] = []
    for item in _classification_specialty_items(payload):
        specialty_id = str(item.get("id") or "").strip()
        if specialty_id:
            records.append({"id": specialty_id, **item})
    return tuple(records)


def _specialty_terms(item: dict[str, Any], *, field: str = "strong_keywords") -> tuple[str, ...]:
    legacy_field = {"strong_keywords": "keywords", "weak_clues": "clues"}.get(field)
    terms = list(_clean_terms(item.get(field)))
    if legacy_field:
        terms.extend(_clean_terms(item.get(legacy_field)))
    for subcategory in item.get("subcategories") or []:
        if isinstance(subcategory, dict):
            terms.extend(_clean_terms(subcategory.get(field)))
            if legacy_field:
                terms.extend(_clean_terms(subcategory.get(legacy_field)))
    return tuple(dict.fromkeys(terms))


@lru_cache(maxsize=1)
def load_specialty_keyword_sets() -> tuple[tuple[str, tuple[str, ...]], ...]:
    payload = load_yaml_dict(CLINICAL_SPECIALTY_MAP_FILE)
    if payload.get("_load_error"):
        return ()
    sets: list[tuple[str, tuple[str, ...]]] = []
    for item in _classification_specialty_items(payload):
        specialty_id = str(item.get("id") or "").strip()
        terms = _specialty_terms(item)
        if specialty_id and terms:
            sets.append((specialty_id, terms))
    return tuple(sets)


@lru_cache(maxsize=1)
def load_specialty_required_any() -> dict[str, tuple[str, ...]]:
    payload = load_yaml_dict(CLINICAL_SPECIALTY_MAP_FILE)
    if payload.get("_load_error"):
        return {}
    rules: dict[str, tuple[str, ...]] = {}
    for item in _classification_specialty_items(payload):
        specialty_id = str(item.get("id") or "").strip()
        terms = _specialty_terms(item, field="required_any")
        if specialty_id and terms:
            rules[specialty_id] = terms
    return rules


@lru_cache(maxsize=1)
def load_obsidian_service_keyword_sets() -> tuple[tuple[str, tuple[str, ...]], ...]:
    payload = load_yaml_dict(CLINICAL_SPECIALTY_MAP_FILE)
    if payload.get("_load_error"):
        return ()
    sets: list[tuple[str, tuple[str, ...]]] = []
    for item in _canonical_specialty_items(payload):
        specialty_id = str(item.get("obsidian_id") or item.get("id") or "").strip()
        terms = _specialty_terms(item, field="obsidian_keywords") or _specialty_terms(item)
        if specialty_id and terms:
            sets.append((specialty_id, tuple(dict.fromkeys(terms))))
    return tuple(sets)


@lru_cache(maxsize=1)
def load_specialty_legacy_aliases() -> dict[str, tuple[str, ...]]:
    payload = load_yaml_dict(CLINICAL_SPECIALTY_MAP_FILE)
    if payload.get("_load_error"):
        return {}
    aliases: dict[str, tuple[str, ...]] = {}
    for item in _canonical_specialty_items(payload):
        specialty_id = str(item.get("id") or "").strip()
        values = list(_clean_terms(item.get("legacy_aliases")))
        for subcategory in item.get("subcategories") or []:
            if isinstance(subcategory, dict):
                subcategory_id = str(subcategory.get("id") or "").strip()
                if subcategory_id:
                    values.append(subcategory_id)
        if specialty_id:
            aliases[specialty_id] = tuple(dict.fromkeys(values))
    return aliases


@lru_cache(maxsize=1)
def load_legacy_specialty_map() -> dict[str, str]:
    legacy_map: dict[str, str] = {}
    for canonical_id, aliases in load_specialty_legacy_aliases().items():
        legacy_map[canonical_id] = canonical_id
        for alias in aliases:
            legacy_map[alias] = canonical_id
    return legacy_map


def canonicalize_specialty_id(specialty_id: str) -> str:
    normalized = str(specialty_id or "").strip()
    return load_legacy_specialty_map().get(normalized, normalized)


@lru_cache(maxsize=1)
def load_literature_diagnosis_keyword_sets() -> tuple[tuple[str, tuple[str, ...]], ...]:
    payload = load_yaml_dict(LITERATURE_TAXONOMY_APPROVED_FILE)
    if payload.get("_load_error"):
        return ()
    sets: list[tuple[str, tuple[str, ...]]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "").strip() != "diagnosis":
            continue
        label = str(item.get("label") or "").strip()
        terms = _clean_terms(item.get("keywords"))
        if label and terms:
            sets.append((label, terms))
    return tuple(sets)


@lru_cache(maxsize=1)
def load_literature_diagnosis_records() -> tuple[dict[str, Any], ...]:
    payload = load_yaml_dict(LITERATURE_TAXONOMY_APPROVED_FILE)
    if payload.get("_load_error"):
        return ()
    records: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "").strip() != "diagnosis":
            continue
        label = str(item.get("label") or "").strip()
        keywords = _clean_terms(item.get("keywords"))
        if label and keywords:
            records.append({"label": label, "keywords": keywords})
    return tuple(records)


@lru_cache(maxsize=1)
def load_obsidian_diagnosis_keyword_sets() -> tuple[tuple[str, tuple[str, ...]], ...]:
    payload = load_yaml_dict(LITERATURE_TAXONOMY_APPROVED_FILE)
    if payload.get("_load_error"):
        return ()
    sets: list[tuple[str, tuple[str, ...]]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "").strip() != "diagnosis":
            continue
        label = str(item.get("obsidian_label") or item.get("label") or "").strip()
        terms = _clean_terms(item.get("keywords"))
        if label and terms:
            sets.append((label, terms))
    return tuple(sets)


@lru_cache(maxsize=1)
def load_obsidian_problem_keyword_sets() -> tuple[tuple[str, tuple[str, ...]], ...]:
    payload = load_yaml_dict(LITERATURE_TAXONOMY_APPROVED_FILE)
    if payload.get("_load_error"):
        return ()
    sets: list[tuple[str, tuple[str, ...]]] = []
    for item in payload.get("problem_keywords") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        terms = _clean_terms(item.get("keywords"))
        if label and terms:
            sets.append((label, terms))
    return tuple(sets)
