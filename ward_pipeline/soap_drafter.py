from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import WardConfig


CLINICAL_FACTS_FILE = "clinical_facts.json"
SOAP_NOTE_FILE = "soap_note.md"
SOAP_NOTE_JSON_FILE = "soap_note.json"
SOAP_AUDIT_FILE = "soap_audit.json"
SCHEMA_VERSION = "1.0"
STAGE_NAME = "soap_drafting"
SECTION_BY_FACT_TYPE = {
    "symptom": "subjective",
    "history": "subjective",
    "transcript_observation": "subjective",
    "objective": "objective",
    "lab": "objective",
    "imaging": "objective",
    "assessment": "assessment",
    "diagnosis": "assessment",
    "plan": "plan",
    "procedure": "plan",
    "medication": "plan",
}


class SOAPDraftError(Exception):
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
    if payload.get("status") not in {"draft", "auto_finalized", "needs_review", "blocked", "failed"}:
        raise SOAPDraftError(f"invalid SOAP status: {payload.get('status')}")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise SOAPDraftError("SOAP payload must contain sections")
    for section_name in ("subjective", "objective", "assessment", "plan"):
        if section_name not in sections or not isinstance(sections[section_name], dict):
            raise SOAPDraftError(f"SOAP payload missing section: {section_name}")


def _empty_sections() -> dict:
    return {
        "subjective": {"text": "Not documented in transcript.", "source_fact_ids": []},
        "objective": {"text": "Not documented in transcript.", "source_fact_ids": []},
        "assessment": {"text": "Not documented in transcript.", "source_fact_ids": []},
        "plan": {"text": "Not documented in transcript.", "source_fact_ids": []},
    }


def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    if text.endswith((".", "。", "!", "！", "?", "？")):
        return text
    return f"{text}."


def _section_text(items: list[str]) -> str:
    sentences = [_sentence(item) for item in items if _sentence(item)]
    if not sentences:
        return "Not documented in transcript."
    return "\n".join(f"- {sentence}" for sentence in sentences)


def _sections_from_facts(facts: list[dict]) -> tuple[dict, list[str]]:
    section_items = {
        "subjective": [],
        "objective": [],
        "assessment": [],
        "plan": [],
    }
    source_ids = {
        "subjective": [],
        "objective": [],
        "assessment": [],
        "plan": [],
    }
    warnings = []
    for fact in facts:
        fact_type = str(fact.get("type") or "").strip()
        section = SECTION_BY_FACT_TYPE.get(fact_type, "subjective")
        fact_id = str(fact.get("fact_id") or "").strip()
        text = str(fact.get("normalized_text") or fact.get("text") or "").strip()
        if not text or not fact_id:
            warnings.append("fact_missing_text_or_id")
            continue
        if fact.get("certainty") not in {"confirmed", "probable"}:
            warnings.append(f"fact_not_confirmed_or_probable:{fact_id}")
            continue
        section_items[section].append(text)
        source_ids[section].append(fact_id)

    sections = {}
    for name in ("subjective", "objective", "assessment", "plan"):
        sections[name] = {
            "text": _section_text(section_items[name]),
            "source_fact_ids": source_ids[name],
        }
    return sections, list(dict.fromkeys(warnings))


def _markdown(payload: dict) -> str:
    sections = payload["sections"]
    return "\n".join(
        [
            "# SOAP Note",
            "",
            "## Subjective",
            sections["subjective"]["text"],
            "",
            "## Objective",
            sections["objective"]["text"],
            "",
            "## Assessment",
            sections["assessment"]["text"],
            "",
            "## Plan",
            sections["plan"]["text"],
            "",
            "## Status",
            payload["status"],
            "",
            "## Blocking Reasons",
            "\n".join(f"- {item}" for item in payload.get("blocking_reasons") or []) or "- none",
            "",
        ]
    )


def draft_soap_note(
    config: WardConfig,
    job_id: str,
    *,
    job_dir: Path,
    state: dict,
) -> dict:
    started_at = _iso_now(config)
    input_artifacts = [_artifact_meta(job_dir, CLINICAL_FACTS_FILE)]
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

    facts_payload = _read_json(job_dir / CLINICAL_FACTS_FILE)
    facts = facts_payload.get("facts") if isinstance(facts_payload, dict) else []
    eligible_facts = [
        fact
        for fact in facts or []
        if isinstance(fact, dict) and fact.get("allowed_in_final_soap") is True
    ]
    sections, warnings = _sections_from_facts(eligible_facts)
    source_fact_ids = []
    for section in sections.values():
        source_fact_ids.extend(section["source_fact_ids"])

    blocking_reasons = []
    if not isinstance(facts_payload, dict) or facts_payload.get("status") in {None, "blocked", "failed"}:
        blocking_reasons.append("missing_or_unusable_clinical_facts")
    if not eligible_facts:
        blocking_reasons.append("no_eligible_evidence_grounded_facts")

    payload = {
        "version": SCHEMA_VERSION,
        "job_id": job_id,
        "status": "blocked" if blocking_reasons else "needs_review",
        "sections": _empty_sections() if blocking_reasons else sections,
        "warnings": warnings,
        "blocking_reasons": blocking_reasons,
        "source_fact_ids": source_fact_ids,
        "input_artifacts": input_artifacts,
    }
    _validate_payload(payload)
    _write_json(job_dir / SOAP_NOTE_JSON_FILE, payload)
    (job_dir / SOAP_NOTE_FILE).write_text(_markdown(payload), encoding="utf-8")

    audit["status"] = payload["status"]
    audit["blocked_reason"] = ";".join(blocking_reasons) if blocking_reasons else None
    audit["completed_at"] = _iso_now(config)
    audit["output_artifacts"] = {
        SOAP_NOTE_FILE: _artifact_meta(job_dir, SOAP_NOTE_FILE),
        SOAP_NOTE_JSON_FILE: _artifact_meta(job_dir, SOAP_NOTE_JSON_FILE),
    }
    _write_json(job_dir / SOAP_AUDIT_FILE, audit)
    return {
        "ok": not blocking_reasons,
        "status": payload["status"],
        "message": audit["blocked_reason"] or "SOAP draft created",
        "artifacts": {
            "soap_note": SOAP_NOTE_FILE,
            "soap_note_json": SOAP_NOTE_JSON_FILE,
            "soap_audit": SOAP_AUDIT_FILE,
        },
    }
