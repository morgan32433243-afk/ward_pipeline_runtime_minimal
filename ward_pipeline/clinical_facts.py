from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import WardConfig


RAW_TRANSCRIPT_FILE = "raw_transcript.txt"
NORMALIZED_TRANSCRIPT_FILE = "normalized_transcript.md"
LLM_NORMALIZED_TRANSCRIPT_FILE = "llm_normalized_transcript.md"
LLM_NORMALIZATION_FILE = "llm_normalization.json"
CLINICAL_FACTS_FILE = "clinical_facts.json"
CLINICAL_FACTS_AUDIT_FILE = "clinical_facts_audit.json"
SCHEMA_VERSION = "1.0"
STAGE_NAME = "clinical_fact_extraction"
HIGH_RISK_FLAGS = {
    "contains_medication",
    "contains_dose",
    "contains_diagnosis",
    "contains_procedure",
    "contains_lab_value",
    "contains_imaging",
    "contains_allergy",
}

SYMPTOM_HINTS = (
    "主述",
    "發燒",
    "乾咳",
    "咳嗽",
    "fever",
    "cough",
    "dyspnea",
)

HISTORY_HINTS = (
    "social history",
    "危險的性行為",
    "體重",
    "半年",
    "既往",
    "病史",
)

OBJECTIVE_HINTS = (
    "vital",
    "血壓",
    "體溫",
    "心跳",
    "呼吸",
    "SpO2",
    "X光",
    "interstitial",
    "infiltration",
    "HIV combo test",
    "CD4",
    "PCP",
    "lab",
    "檢驗",
)

PLAN_HINTS = (
    "治療",
    "經驗性",
    "先不要",
    "開上去",
    "排",
    "bronchoscopy",
    "prednisolone",
    "trimethoprim-sulfamethoxazole",
    "驗一下",
    "送過了",
    "訂",
    "處理",
)


class ClinicalFactsError(Exception):
    pass


def _iso_now(config: WardConfig) -> str:
    return datetime.now(ZoneInfo(config.timezone)).isoformat(timespec="seconds")


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_meta(job_dir: Path, name: str) -> dict:
    path = job_dir / name
    return {
        "artifact": name,
        "exists": path.exists(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size if path.exists() else None,
    }


def _read_json(path: Path) -> dict | list:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": f"invalid json: {path.name}"}


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _validate_payload(payload: dict) -> None:
    if payload.get("status") not in {"ok", "partial", "blocked", "failed"}:
        raise ClinicalFactsError(f"invalid clinical facts status: {payload.get('status')}")
    if not isinstance(payload.get("facts"), list):
        raise ClinicalFactsError("clinical facts must contain a facts list")
    if not isinstance(payload.get("excluded_facts"), list):
        raise ClinicalFactsError("clinical facts must contain an excluded_facts list")


def _excluded_from_uncertain(llm_payload: dict | list) -> list[dict]:
    if not isinstance(llm_payload, dict):
        return []
    excluded = []
    for item in llm_payload.get("uncertain_items") or []:
        if not isinstance(item, dict):
            continue
        risk_level = item.get("risk_level") or "medium"
        reason = "uncertain_high_risk" if risk_level == "high" else "uncertain_requires_review"
        excluded.append(
            {
                "text": item.get("text") or "",
                "candidate_normalization": item.get("candidate_normalization") or "",
                "reason": reason,
                "risk_level": risk_level,
                "source_refs": item.get("source_refs") or [],
            }
        )
    return excluded


def _fact_type(flags: list) -> str:
    return _fact_type_from_text("", flags)


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    haystack = text.lower()
    return any(hint.lower() in haystack for hint in hints)


def _fact_type_from_text(text: str, flags: list | None = None) -> str:
    flag_set = set(flags or [])
    normalized_text = str(text or "")
    if "contains_symptom" in flag_set or _contains_any(normalized_text, SYMPTOM_HINTS):
        return "symptom"
    if "contains_history" in flag_set or _contains_any(normalized_text, HISTORY_HINTS):
        return "history"
    if "contains_objective" in flag_set or _contains_any(normalized_text, OBJECTIVE_HINTS):
        return "objective"
    if "contains_diagnosis" in flag_set:
        return "assessment"
    if (
        "contains_medication" in flag_set
        or "contains_procedure" in flag_set
        or _contains_any(normalized_text, PLAN_HINTS)
    ):
        return "plan"
    if "contains_lab_value" in flag_set or "contains_imaging" in flag_set:
        return "objective"
    return "transcript_observation"


def _facts_from_blocks(llm_payload: dict | list) -> tuple[list[dict], list[dict]]:
    if not isinstance(llm_payload, dict):
        return [], []
    facts = []
    excluded = []
    for index, block in enumerate(llm_payload.get("blocks") or [], start=1):
        if not isinstance(block, dict):
            continue
        flags = block.get("flags") or []
        flag_set = set(flags)
        text = str(block.get("normalized_text") or "").strip()
        source_refs = block.get("source_refs") or []
        confidence = block.get("confidence") or "low"
        block_id = str(block.get("block_id") or f"b{index}")
        if not text:
            continue
        if "skeleton_noop" in flag_set:
            excluded.append(
                {
                    "text": text,
                    "reason": "skeleton_noop_not_promoted",
                    "risk_level": "low",
                    "source_refs": source_refs,
                    "derived_from": [f"llm_normalization:block:{block_id}"],
                }
            )
            continue
        if flag_set & HIGH_RISK_FLAGS:
            excluded.append(
                {
                    "text": text,
                    "reason": "high_risk_block_requires_review",
                    "risk_level": "high",
                    "source_refs": source_refs,
                    "derived_from": [f"llm_normalization:block:{block_id}"],
                }
            )
            continue
        if confidence not in {"high", "medium"}:
            excluded.append(
                {
                    "text": text,
                    "reason": "normalization_block_requires_review",
                    "risk_level": "medium",
                    "source_refs": source_refs,
                    "derived_from": [f"llm_normalization:block:{block_id}"],
                }
            )
            continue
        facts.append(
            {
                "fact_id": f"f{len(facts) + 1}",
                "type": _fact_type_from_text(text, flags),
                "text": text,
                "normalized_text": text,
                "certainty": "confirmed" if confidence == "high" else "probable",
                "risk_level": "low",
                "source_refs": source_refs,
                "derived_from": [f"llm_normalization:block:{block_id}"],
                "allowed_in_final_soap": confidence == "high",
                "review_required": "needs_review" in flag_set,
            }
        )
    return facts, excluded


def extract_clinical_facts(
    config: WardConfig,
    job_id: str,
    *,
    job_dir: Path,
    state: dict,
) -> dict:
    started_at = _iso_now(config)
    input_artifacts = [
        _artifact_meta(job_dir, RAW_TRANSCRIPT_FILE),
        _artifact_meta(job_dir, NORMALIZED_TRANSCRIPT_FILE),
        _artifact_meta(job_dir, LLM_NORMALIZED_TRANSCRIPT_FILE),
        _artifact_meta(job_dir, LLM_NORMALIZATION_FILE),
    ]

    audit = {
        "version": SCHEMA_VERSION,
        "job_id": job_id,
        "stage": STAGE_NAME,
        "started_at": started_at,
        "completed_at": None,
        "input_artifacts": input_artifacts,
        "output_artifacts": {},
        "status": None,
        "blocked_reason": None,
    }

    llm_payload = _read_json(job_dir / LLM_NORMALIZATION_FILE)
    if not isinstance(llm_payload, dict) or llm_payload.get("status") in {None, "blocked", "failed"}:
        payload = {
            "version": SCHEMA_VERSION,
            "job_id": job_id,
            "status": "blocked",
            "facts": [],
            "excluded_facts": [],
            "summary": {
                "facts": 0,
                "excluded_facts": 0,
                "high_risk_excluded": 0,
            },
            "blocked_reason": "missing_or_unusable_llm_normalization",
            "input_artifacts": input_artifacts,
        }
        _validate_payload(payload)
        _write_json(job_dir / CLINICAL_FACTS_FILE, payload)
        audit["status"] = "blocked"
        audit["blocked_reason"] = payload["blocked_reason"]
        audit["completed_at"] = _iso_now(config)
        audit["output_artifacts"] = {CLINICAL_FACTS_FILE: _artifact_meta(job_dir, CLINICAL_FACTS_FILE)}
        _write_json(job_dir / CLINICAL_FACTS_AUDIT_FILE, audit)
        return {
            "ok": False,
            "status": "blocked",
            "message": payload["blocked_reason"],
            "artifacts": {
                "clinical_facts": CLINICAL_FACTS_FILE,
                "clinical_facts_audit": CLINICAL_FACTS_AUDIT_FILE,
            },
        }

    facts, block_exclusions = _facts_from_blocks(llm_payload)
    excluded_facts = _excluded_from_uncertain(llm_payload) + block_exclusions
    high_risk_excluded = sum(1 for item in excluded_facts if item.get("risk_level") == "high")
    status = "ok" if facts and not high_risk_excluded else "partial"
    payload = {
        "version": SCHEMA_VERSION,
        "job_id": job_id,
        "status": status,
        "facts": facts,
        "excluded_facts": excluded_facts,
        "summary": {
            "facts": len(facts),
            "excluded_facts": len(excluded_facts),
            "high_risk_excluded": high_risk_excluded,
        },
        "note": "MVP fact extractor promotes only low-risk normalization blocks with source refs.",
        "input_artifacts": input_artifacts,
    }
    _validate_payload(payload)
    _write_json(job_dir / CLINICAL_FACTS_FILE, payload)

    audit["status"] = status
    audit["completed_at"] = _iso_now(config)
    audit["output_artifacts"] = {
        CLINICAL_FACTS_FILE: _artifact_meta(job_dir, CLINICAL_FACTS_FILE),
    }
    _write_json(job_dir / CLINICAL_FACTS_AUDIT_FILE, audit)
    return {
        "ok": True,
        "status": status,
        "message": "clinical fact extraction completed",
        "artifacts": {
            "clinical_facts": CLINICAL_FACTS_FILE,
            "clinical_facts_audit": CLINICAL_FACTS_AUDIT_FILE,
        },
    }
