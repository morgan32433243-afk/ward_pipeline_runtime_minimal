#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from ward_pipeline.literature import infer_clinical_classification
from ward_pipeline.taxonomy import load_specialty_records


def _specialty_folders() -> dict[str, str]:
    folders: dict[str, str] = {}
    for record in load_specialty_records():
        specialty_id = str(record.get("id") or "").strip()
        folder = str(record.get("obsidian_folder") or "").strip()
        if specialty_id and folder:
            folders[specialty_id] = folder
    return folders


def _unmapped_folder_for_specialty(primary: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(primary or "").strip()).strip("_")
    if not normalized:
        return ""
    pretty = "_".join(part.capitalize() for part in normalized.split("_") if part)
    return f"Medicine/Unmapped_Clinical_Domain/{pretty}"


def _numeric_confidence(candidates: list[dict[str, Any]]) -> float:
    if not candidates:
        return 0.0
    top = float(candidates[0].get("score") or 0.0)
    runner_up = float(candidates[1].get("score") or 0.0) if len(candidates) > 1 else 0.0
    if top <= 0:
        return 0.0
    separation = (top - runner_up) / max(top, 1.0)
    volume = min(top / 30.0, 1.0)
    confidence = 0.45 + 0.35 * max(separation, 0.0) + 0.20 * volume
    return round(min(confidence, 0.99), 2)


def _routing_numeric_confidence(confidence: str) -> float:
    mapping = {"high": 0.92, "moderate": 0.78, "medium": 0.78, "low": 0.58, "uncertain": 0.5}
    return mapping.get(str(confidence or "").strip().casefold(), 0.68)


def _section_label(section: str) -> str:
    labels = {
        "assessment": "Assessment",
        "impression": "Impression",
        "problem_list": "Problem list",
        "diagnosis": "Diagnosis",
        "plan": "Plan",
        "medication": "Medication",
        "lab_imaging": "Lab / imaging",
        "past_history": "Past history",
        "family_history": "Family history",
        "body": "Body",
    }
    return labels.get(section, section.replace("_", " ").title())


def _matched_terms(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        specialty = str(candidate.get("label") or "")
        for detail in candidate.get("match_details") or []:
            category = str(detail.get("field") or "")
            if category == "negative_context":
                continue
            term = str(detail.get("term") or "")
            section = _section_label(str(detail.get("section") or "body"))
            key = (term.casefold(), specialty, category, section)
            if not term or key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "term": term,
                    "specialty": specialty,
                    "category": category,
                    "section": section,
                    "weight": detail.get("keyword_weight", 0),
                }
            )
    return rows


def _negative_matches(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        specialty = str(candidate.get("label") or "")
        for term in candidate.get("negative_matches") or []:
            key = (str(term).casefold(), specialty)
            if not term or key in seen:
                continue
            seen.add(key)
            rows.append({"term": str(term), "specialty": specialty})
    return rows


def _reason_summary(primary: str, matched: list[dict[str, Any]]) -> str:
    if not primary:
        return "No specialty selected because no taxonomy terms reached a positive deterministic score."
    primary_terms = [
        item["term"]
        for item in matched
        if item.get("specialty") == primary and item.get("section") in {"Assessment", "Impression", "Diagnosis", "Problem list"}
    ]
    if not primary_terms:
        primary_terms = [item["term"] for item in matched if item.get("specialty") == primary]
    top_terms = list(dict.fromkeys(primary_terms))[:3]
    if top_terms:
        return f"{primary} selected because high-weight clinical terms matched {', '.join(top_terms)}."
    return f"{primary} selected because it had the highest deterministic taxonomy score."


def build_classification_json(text: str) -> dict[str, Any]:
    raw = infer_clinical_classification(text)
    candidates = list(raw.get("service_candidates") or [])
    primary = str(raw.get("primary_specialty") or "")
    secondary = [str(item.get("label")) for item in candidates[1:3] if item.get("label")]
    scores = {str(item.get("label")): item.get("score", 0) for item in candidates if item.get("label")}
    matched = _matched_terms(candidates)
    folders = _specialty_folders()
    routing_source = str(raw.get("routing_source") or "")
    raw_confidence = str(raw.get("confidence") or "")
    return {
        "primary_specialty": primary,
        "secondary_specialties": secondary,
        "confidence": _routing_numeric_confidence(raw_confidence) if routing_source == "hermes" else _numeric_confidence(candidates),
        "scores": scores,
        "matched_terms": matched,
        "negative_matches": _negative_matches(candidates),
        "suggested_obsidian_folder": folders.get(primary, "") or _unmapped_folder_for_specialty(primary),
        "classification_reason_summary": _reason_summary(primary, matched),
        "diagnosis_topics": list(raw.get("diagnosis_topics") or []),
        "routing_source": routing_source,
    }


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        print("usage: classify_soap.py SOAP_PATH [OUTPUT_JSON_PATH]", file=sys.stderr)
        return 2
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    payload = build_classification_json(text)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if len(sys.argv) == 3:
        output_path = Path(sys.argv[2])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
