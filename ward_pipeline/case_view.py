from __future__ import annotations

import json
import re
from pathlib import Path

from .config import WardConfig


STATE_FILE = "state.json"
TRANSCRIPT_FILE = "transcript.manual.txt"
SPEAKER_TRANSCRIPT_FILE = "transcript.speaker.txt"
DIARIZATION_RENDER_FILE = "diarization_render.md"
RAW_TRANSCRIPT_FILE = "raw_transcript.txt"
NORMALIZED_TRANSCRIPT_FILE = "normalized_transcript.md"
CORRECTION_LOG_FILE = "correction_log.json"
UNCERTAIN_TERMS_FILE = "uncertain_terms.json"
CONFIRMED_TERMS_FILE = "confirmed_terms.json"
HERMES_RESULT_FILE = "result.hermes.md"
SOAP_DRAFT_FILE = "soap_draft.md"
PROMPT_PACKAGE_FILE = "prompt.chatgpt.md"
LLM_NORMALIZED_TRANSCRIPT_FILE = "llm_normalized_transcript.md"
LLM_NORMALIZATION_FILE = "llm_normalization.json"
LLM_NORMALIZATION_AUDIT_FILE = "llm_normalization_audit.json"
CLINICAL_FACTS_FILE = "clinical_facts.json"
CLINICAL_FACTS_AUDIT_FILE = "clinical_facts_audit.json"
SOAP_NOTE_FILE = "soap_note.md"
SOAP_NOTE_JSON_FILE = "soap_note.json"
SOAP_AUDIT_FILE = "soap_audit.json"
SOAP_VALIDATION_FILE = "soap_validation.json"
DELIVERY_REPORT_FILE = "delivery.report.json"
DELIVERY_INTENT_FILE = "delivery.intent.json"
REVIEW_SUMMARY_FILE = "review_summary.md"
ARTIFACTS_DIR = "artifacts"

PRIMARY_ROOT_FILES = (
    "audio",
    "transcript.txt",
    SOAP_DRAFT_FILE,
)

SECONDARY_FILES = (
    REVIEW_SUMMARY_FILE,
    RAW_TRANSCRIPT_FILE,
    NORMALIZED_TRANSCRIPT_FILE,
    CORRECTION_LOG_FILE,
    UNCERTAIN_TERMS_FILE,
    CONFIRMED_TERMS_FILE,
    HERMES_RESULT_FILE,
    PROMPT_PACKAGE_FILE,
    LLM_NORMALIZED_TRANSCRIPT_FILE,
    LLM_NORMALIZATION_FILE,
    LLM_NORMALIZATION_AUDIT_FILE,
    CLINICAL_FACTS_FILE,
    CLINICAL_FACTS_AUDIT_FILE,
    SOAP_NOTE_FILE,
    SOAP_NOTE_JSON_FILE,
    SOAP_AUDIT_FILE,
    SOAP_VALIDATION_FILE,
    DELIVERY_INTENT_FILE,
    DELIVERY_REPORT_FILE,
    SPEAKER_TRANSCRIPT_FILE,
    DIARIZATION_RENDER_FILE,
    "ward_job",
)


def _job_dir(config: WardConfig, job_id: str) -> Path:
    return config.output_dir / job_id


def _read_state(config: WardConfig, job_id: str) -> dict:
    state_path = _job_dir(config, job_id) / STATE_FILE
    if not state_path.exists():
        raise FileNotFoundError(f"missing job state: {state_path}")
    return json.loads(state_path.read_text(encoding="utf-8"))


def _part_name(audio_path: Path) -> str:
    match = re.search(r"part-(\d+)", audio_path.stem)
    if match:
        return f"part-{match.group(1)}"
    return "part-001"


def _case_root(config: WardConfig, state: dict) -> Path:
    routing = state.get("routing") or {}
    encounter_dir = Path(routing.get("encounter_dir") or "")
    bed_id = str(routing.get("bed_id") or "unknown")
    date_name = encounter_dir.parent.parent.name if len(encounter_dir.parents) >= 2 else state.get("created_at", "")[:10]
    case_name = f"{date_name}_bed-{bed_id}"
    return config.case_view_dir / case_name


def _case_dir(config: WardConfig, state: dict, audio_path: Path, job_id: str) -> Path:
    case_root = _case_root(config, state)
    candidate = case_root / _part_name(audio_path)
    existing_job = candidate / "ward_job"
    if not existing_job.exists() or not existing_job.is_symlink():
        return candidate
    if existing_job.resolve() == _job_dir(config, job_id):
        return candidate
    return case_root / audio_path.stem


def _replace_symlink(link_path: Path, target: Path) -> dict:
    target = target.expanduser().resolve()
    if not target.exists():
        return {"name": link_path.name, "ok": False, "skipped": True, "reason": f"missing target: {target}"}

    if link_path.is_symlink():
        current = link_path.resolve()
        if current == target:
            return {"name": link_path.name, "ok": True, "target": str(target), "changed": False}
        link_path.unlink()
    elif link_path.exists():
        return {
            "name": link_path.name,
            "ok": False,
            "skipped": True,
            "reason": f"refusing to replace non-symlink: {link_path}",
        }

    link_path.symlink_to(target)
    return {"name": link_path.name, "ok": True, "target": str(target), "changed": True}


def _cleanup_legacy_root_entries(case_dir: Path) -> None:
    for name in SECONDARY_FILES:
        legacy = case_dir / name
        if legacy.is_symlink() or legacy.is_file():
            legacy.unlink()
        elif legacy.exists():
            # Leave directories alone; the view only manages files/symlinks.
            continue


def _read_optional_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": f"invalid json: {path.name}"}


def _status_line(payload: dict | list, key: str = "status") -> str:
    if isinstance(payload, dict):
        return str(payload.get(key) or "missing")
    return "missing"


def _review_summary(state: dict, job_dir: Path) -> str:
    llm = _read_optional_json(job_dir / LLM_NORMALIZATION_FILE)
    facts = _read_optional_json(job_dir / CLINICAL_FACTS_FILE)
    soap = _read_optional_json(job_dir / SOAP_NOTE_JSON_FILE)
    validation = _read_optional_json(job_dir / SOAP_VALIDATION_FILE)
    blocking = validation.get("blocking_reasons") if isinstance(validation, dict) else []
    artifacts = state.get("artifacts") or {}
    lines = [
        "# Ward Case Review Summary",
        "",
        f"- Job ID: `{state.get('job_id', 'unknown')}`",
        f"- Job status: `{state.get('status', 'unknown')}`",
        f"- Current step: `{state.get('current_step', 'unknown')}`",
        f"- Needs human review: `{state.get('needs_human_review', 'unknown')}`",
        f"- LLM normalization: `{_status_line(llm)}`",
        f"- Clinical facts: `{_status_line(facts)}`",
        f"- SOAP draft: `{_status_line(soap)}`",
        f"- SOAP validation: `{_status_line(validation)}`",
        "",
        "## Blocking Reasons",
    ]
    if blocking:
        lines.extend(f"- `{item}`" for item in blocking)
    else:
        lines.append("- none recorded")
    lines.extend(
        [
            "",
            "## Review Reasons",
        ]
    )
    review_reasons = state.get("review_reasons") or []
    if review_reasons:
        lines.extend(f"- `{item}`" for item in review_reasons)
    else:
        lines.append("- none recorded")
    lines.extend(
        [
            "",
            "## Key Artifacts",
        ]
    )
    for key in (
        "raw_transcript",
        "normalized_transcript",
        "llm_normalized_transcript",
        "llm_normalization",
        "clinical_facts",
        "hermes_result",
        "soap_draft",
        "prompt_package",
        "soap_note",
        "soap_validation",
        "delivery_report",
        "diarization_render",
    ):
        value = artifacts.get(key)
        if key == "soap_draft" and not value:
            value = artifacts.get("hermes_result")
        if value:
            lines.append(f"- `{key}`: `{value}`")
    return "\n".join(lines) + "\n"


def build_case_view(config: WardConfig, job_id: str) -> dict:
    state = _read_state(config, job_id)
    job_dir = _job_dir(config, job_id)
    audio_path = Path(state.get("routing", {}).get("archive_path") or state.get("input", {}).get("original_path") or "")
    if not audio_path:
        raise ValueError(f"job has no audio path: {job_id}")

    case_dir = _case_dir(config, state, audio_path, job_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_root_entries(case_dir)
    artifacts_dir = case_dir / ARTIFACTS_DIR
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    summary_path = artifacts_dir / REVIEW_SUMMARY_FILE
    if summary_path.is_symlink() or summary_path.is_file():
        summary_path.unlink()
    summary_path.write_text(_review_summary(state, job_dir), encoding="utf-8")

    links = [
        _replace_symlink(case_dir / f"audio{audio_path.suffix.lower() or '.audio'}", audio_path),
        _replace_symlink(case_dir / "transcript.txt", job_dir / TRANSCRIPT_FILE),
        _replace_symlink(case_dir / SOAP_DRAFT_FILE, job_dir / HERMES_RESULT_FILE),
        _replace_symlink(artifacts_dir / RAW_TRANSCRIPT_FILE, job_dir / RAW_TRANSCRIPT_FILE),
        _replace_symlink(artifacts_dir / NORMALIZED_TRANSCRIPT_FILE, job_dir / NORMALIZED_TRANSCRIPT_FILE),
        _replace_symlink(artifacts_dir / CORRECTION_LOG_FILE, job_dir / CORRECTION_LOG_FILE),
        _replace_symlink(artifacts_dir / UNCERTAIN_TERMS_FILE, job_dir / UNCERTAIN_TERMS_FILE),
        _replace_symlink(artifacts_dir / CONFIRMED_TERMS_FILE, job_dir / CONFIRMED_TERMS_FILE),
        _replace_symlink(artifacts_dir / HERMES_RESULT_FILE, job_dir / HERMES_RESULT_FILE),
        _replace_symlink(artifacts_dir / PROMPT_PACKAGE_FILE, job_dir / PROMPT_PACKAGE_FILE),
        _replace_symlink(artifacts_dir / LLM_NORMALIZED_TRANSCRIPT_FILE, job_dir / LLM_NORMALIZED_TRANSCRIPT_FILE),
        _replace_symlink(artifacts_dir / LLM_NORMALIZATION_FILE, job_dir / LLM_NORMALIZATION_FILE),
        _replace_symlink(artifacts_dir / LLM_NORMALIZATION_AUDIT_FILE, job_dir / LLM_NORMALIZATION_AUDIT_FILE),
        _replace_symlink(artifacts_dir / CLINICAL_FACTS_FILE, job_dir / CLINICAL_FACTS_FILE),
        _replace_symlink(artifacts_dir / CLINICAL_FACTS_AUDIT_FILE, job_dir / CLINICAL_FACTS_AUDIT_FILE),
        _replace_symlink(artifacts_dir / SOAP_NOTE_FILE, job_dir / SOAP_NOTE_FILE),
        _replace_symlink(artifacts_dir / SOAP_NOTE_JSON_FILE, job_dir / SOAP_NOTE_JSON_FILE),
        _replace_symlink(artifacts_dir / SOAP_AUDIT_FILE, job_dir / SOAP_AUDIT_FILE),
        _replace_symlink(artifacts_dir / SOAP_VALIDATION_FILE, job_dir / SOAP_VALIDATION_FILE),
        _replace_symlink(artifacts_dir / DELIVERY_INTENT_FILE, job_dir / DELIVERY_INTENT_FILE),
        _replace_symlink(artifacts_dir / DELIVERY_REPORT_FILE, job_dir / DELIVERY_REPORT_FILE),
        _replace_symlink(artifacts_dir / SPEAKER_TRANSCRIPT_FILE, job_dir / SPEAKER_TRANSCRIPT_FILE),
        _replace_symlink(artifacts_dir / DIARIZATION_RENDER_FILE, job_dir / DIARIZATION_RENDER_FILE),
        _replace_symlink(artifacts_dir / "ward_job", job_dir),
    ]

    return {
        "ok": all(item.get("ok") or item.get("skipped") for item in links),
        "action": "case-view",
        "job_id": job_id,
        "case_dir": str(case_dir),
        "review_summary": str(summary_path),
        "links": links,
        "source_of_truth": {
            "job_dir": str(job_dir),
            "audio_path": str(audio_path),
        },
    }
