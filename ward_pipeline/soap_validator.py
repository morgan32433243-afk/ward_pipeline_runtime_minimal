from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from .config import WardConfig


SOAP_NOTE_JSON_FILE = "soap_note.json"
CLINICAL_FACTS_FILE = "clinical_facts.json"
UNCERTAIN_TERMS_FILE = "uncertain_terms.json"
CONFIRMED_TERMS_FILE = "confirmed_terms.json"
STT_RECOVERY_CANDIDATES_FILE = "stt_recovery_candidates.json"
CONFIRMED_STT_RECOVERY_FILE = "confirmed_stt_recovery.json"
SOAP_VALIDATION_FILE = "soap_validation.json"
DEFAULT_POLICY_FILE = Path(__file__).with_name("auto_soap_policy.yml")
SCHEMA_VERSION = "1.0"
STAGE_NAME = "soap_validation"


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


def _load_policy() -> dict:
    policy_path = Path(os.environ.get("AUTO_SOAP_POLICY_PATH") or DEFAULT_POLICY_FILE).expanduser()
    if not policy_path.exists():
        return {
            "version": SCHEMA_VERSION,
            "rollout_level": 0,
            "auto_finalize_enabled": False,
            "block_on": {
                "high_risk_excluded_facts": True,
                "missing_source_fact_coverage": True,
                "missing_clinical_facts": True,
                "missing_required_sections": True,
                "unresolved_uncertain_terms": True,
                "unresolved_stt_recovery_candidates": True,
            },
            "required_sections": ["subjective", "objective", "assessment", "plan"],
            "minimum_source_fact_refs": 1,
            "rollout_selector": {},
            "policy_path": str(policy_path),
            "loaded": False,
        }
    payload = yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        payload = {}
    payload["policy_path"] = str(policy_path)
    payload["loaded"] = True
    return payload


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _term_key(item: dict) -> tuple[str, str]:
    return (
        str(item.get("original") or "").strip().lower(),
        str(item.get("candidate") or item.get("corrected") or "").strip().lower(),
    )


def _pending_uncertain_terms(job_dir: Path) -> int:
    uncertain_terms = _read_json(job_dir / UNCERTAIN_TERMS_FILE)
    confirmed_terms = _read_json(job_dir / CONFIRMED_TERMS_FILE)
    if not isinstance(uncertain_terms, list):
        return 0
    confirmed_keys = {
        _term_key(item)
        for item in confirmed_terms
        if isinstance(item, dict) and _term_key(item) != ("", "")
    } if isinstance(confirmed_terms, list) else set()
    pending = 0
    for item in uncertain_terms:
        if not isinstance(item, dict):
            continue
        if item.get("requires_human_confirmation") is False:
            continue
        key = _term_key(item)
        if key != ("", "") and key in confirmed_keys:
            continue
        pending += 1
    return pending


def _pending_stt_recovery_candidates(job_dir: Path) -> int:
    payload = _read_json(job_dir / STT_RECOVERY_CANDIDATES_FILE)
    confirmed = _read_json(job_dir / CONFIRMED_STT_RECOVERY_FILE)
    if not isinstance(payload, dict):
        return 0
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return 0
    confirmed_ids = {
        str(item.get("candidate_id") or "").strip()
        for item in confirmed
        if isinstance(item, dict) and str(item.get("candidate_id") or "").strip()
    } if isinstance(confirmed, list) else set()
    pending = 0
    for item in candidates:
        if not isinstance(item, dict):
            continue
        if item.get("requires_human_confirmation") is False:
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if candidate_id and candidate_id in confirmed_ids:
            continue
        pending += 1
    return pending


def _rollout_selector_reasons(policy: dict, state: dict) -> list[str]:
    selector = policy.get("rollout_selector")
    if not isinstance(selector, dict):
        return []

    reasons = []
    routing = state.get("routing") if isinstance(state.get("routing"), dict) else {}
    job_id = str(state.get("job_id") or "").strip()
    bed_id = str(routing.get("bed_id") or "").strip()
    encounter_id = str(routing.get("encounter_id") or "").strip()
    same_encounter_confidence = str(routing.get("same_encounter_confidence") or "").strip()

    allowed_job_ids = _string_list(selector.get("allowed_job_ids"))
    if allowed_job_ids and job_id not in allowed_job_ids:
        reasons.append("rollout_selector_job_not_allowed")

    allowed_bed_ids = _string_list(selector.get("allowed_bed_ids"))
    if allowed_bed_ids and bed_id not in allowed_bed_ids:
        reasons.append("rollout_selector_bed_not_allowed")

    allowed_encounter_ids = _string_list(selector.get("allowed_encounter_ids"))
    if allowed_encounter_ids and encounter_id not in allowed_encounter_ids:
        reasons.append("rollout_selector_encounter_not_allowed")

    allowed_confidence = _string_list(selector.get("allowed_same_encounter_confidence"))
    if allowed_confidence and same_encounter_confidence not in allowed_confidence:
        reasons.append("rollout_selector_encounter_confidence_not_allowed")

    if selector.get("allow_requires_identity_review") is False and bool(routing.get("requires_identity_review")):
        reasons.append("rollout_selector_identity_review_required")

    return reasons


def _policy_blocking_reasons(policy: dict, facts_payload: dict | list, state: dict) -> list[str]:
    reasons = []
    if not policy.get("auto_finalize_enabled"):
        reasons.append("auto_finalize_disabled_by_policy")

    block_on = policy.get("block_on") if isinstance(policy.get("block_on"), dict) else {}
    if block_on.get("high_risk_excluded_facts", True) and isinstance(facts_payload, dict):
        summary = facts_payload.get("summary") if isinstance(facts_payload.get("summary"), dict) else {}
        if int(summary.get("high_risk_excluded") or 0) > 0:
            reasons.append("high_risk_excluded_facts")

    allowed_types = policy.get("allowed_encounter_types") or []
    encounter_type = str((state.get("encounter") or {}).get("type") or "").strip()
    if allowed_types and encounter_type and encounter_type not in allowed_types:
        reasons.append("encounter_type_not_allowed_for_auto_finalize")
    reasons.extend(_rollout_selector_reasons(policy, state))
    return reasons


def _required_section_reasons(policy: dict, sections: dict) -> list[str]:
    required_sections = policy.get("required_sections")
    if not isinstance(required_sections, list):
        required_sections = ["subjective", "objective", "assessment", "plan"]
    missing = []
    for name in required_sections:
        section = sections.get(str(name)) if isinstance(sections, dict) else None
        text = str(section.get("text") or "").strip() if isinstance(section, dict) else ""
        if not text:
            missing.append(str(name))
    if not missing:
        return []
    return [f"missing_required_soap_section:{name}" for name in missing]


def validate_soap_note(
    config: WardConfig,
    job_id: str,
    *,
    job_dir: Path,
    state: dict,
) -> dict:
    soap_payload = _read_json(job_dir / SOAP_NOTE_JSON_FILE)
    facts_payload = _read_json(job_dir / CLINICAL_FACTS_FILE)
    policy = _load_policy()
    block_on = policy.get("block_on") if isinstance(policy.get("block_on"), dict) else {}
    checks = []
    blocking_reasons = []

    if not isinstance(soap_payload, dict):
        blocking_reasons.append("missing_or_invalid_soap_note_json")
    elif soap_payload.get("status") in {"blocked", "failed"}:
        blocking_reasons.extend(soap_payload.get("blocking_reasons") or ["soap_note_blocked"])

    if not isinstance(facts_payload, dict) or facts_payload.get("status") in {None, "blocked", "failed"}:
        blocking_reasons.append("missing_or_unusable_clinical_facts")

    sections = soap_payload.get("sections") if isinstance(soap_payload, dict) else {}
    source_fact_ids = []
    if isinstance(sections, dict):
        for section in sections.values():
            if isinstance(section, dict):
                source_fact_ids.extend(section.get("source_fact_ids") or [])
    minimum_source_fact_refs = int(policy.get("minimum_source_fact_refs") or 1)
    if block_on.get("missing_source_fact_coverage", True) and len(source_fact_ids) < minimum_source_fact_refs:
        blocking_reasons.append("soap_has_no_source_fact_coverage")
    if block_on.get("missing_required_sections", True):
        blocking_reasons.extend(_required_section_reasons(policy, sections))
    pending_uncertain_terms = _pending_uncertain_terms(job_dir)
    if block_on.get("unresolved_uncertain_terms", True) and pending_uncertain_terms:
        blocking_reasons.append("unresolved_uncertain_terms")
    pending_stt_recovery_candidates = _pending_stt_recovery_candidates(job_dir)
    if block_on.get("unresolved_stt_recovery_candidates", True) and pending_stt_recovery_candidates:
        blocking_reasons.append("unresolved_stt_recovery_candidates")
    blocking_reasons.extend(_policy_blocking_reasons(policy, facts_payload, state))

    checks.append(
        {
            "name": "source_fact_coverage",
            "passed": len(source_fact_ids) >= minimum_source_fact_refs,
            "detail": f"{len(source_fact_ids)} source fact references found; minimum {minimum_source_fact_refs}",
        }
    )
    checks.append(
        {
            "name": "clinical_facts_available",
            "passed": isinstance(facts_payload, dict) and facts_payload.get("status") not in {None, "blocked", "failed"},
            "detail": facts_payload.get("status") if isinstance(facts_payload, dict) else "missing",
        }
    )
    checks.append(
        {
            "name": "required_sections_present",
            "passed": not _required_section_reasons(policy, sections),
            "detail": policy.get("required_sections") or ["subjective", "objective", "assessment", "plan"],
        }
    )
    checks.append(
        {
            "name": "manual_stt_review_resolved",
            "passed": pending_uncertain_terms == 0 and pending_stt_recovery_candidates == 0,
            "detail": {
                "pending_uncertain_terms": pending_uncertain_terms,
                "pending_stt_recovery_candidates": pending_stt_recovery_candidates,
            },
        }
    )
    checks.append(
        {
            "name": "auto_finalize_policy",
            "passed": bool(policy.get("auto_finalize_enabled")),
            "detail": {
                "policy_path": policy.get("policy_path"),
                "rollout_level": policy.get("rollout_level"),
                "auto_finalize_enabled": bool(policy.get("auto_finalize_enabled")),
            },
        }
    )
    selector_reasons = _rollout_selector_reasons(policy, state)
    checks.append(
        {
            "name": "rollout_selector",
            "passed": not selector_reasons,
            "detail": {
                "selector": policy.get("rollout_selector") if isinstance(policy.get("rollout_selector"), dict) else {},
                "reasons": selector_reasons,
            },
        }
    )

    unique_blocking_reasons = list(dict.fromkeys(blocking_reasons))
    status = "auto_finalized" if not unique_blocking_reasons else "blocked"
    payload = {
        "version": SCHEMA_VERSION,
        "job_id": job_id,
        "stage": STAGE_NAME,
        "status": status,
        "checks": checks,
        "blocking_reasons": unique_blocking_reasons,
        "policy": {
            "policy_path": policy.get("policy_path"),
            "loaded": policy.get("loaded"),
            "version": policy.get("version"),
            "rollout_level": policy.get("rollout_level"),
            "auto_finalize_enabled": bool(policy.get("auto_finalize_enabled")),
            "rollout_selector": policy.get("rollout_selector") if isinstance(policy.get("rollout_selector"), dict) else {},
        },
        "input_artifacts": [
            _artifact_meta(job_dir, SOAP_NOTE_JSON_FILE),
            _artifact_meta(job_dir, CLINICAL_FACTS_FILE),
        ],
        "validated_at": _iso_now(config),
    }
    _write_json(job_dir / SOAP_VALIDATION_FILE, payload)
    return {
        "ok": status == "auto_finalized",
        "status": status,
        "message": ";".join(unique_blocking_reasons) if unique_blocking_reasons else "SOAP validation passed",
        "artifacts": {
            "soap_validation": SOAP_VALIDATION_FILE,
        },
    }
