from __future__ import annotations

import dataclasses
import fnmatch
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .case_view import build_case_view
from .clinical_facts import (
    CLINICAL_FACTS_AUDIT_FILE,
    CLINICAL_FACTS_FILE,
    extract_clinical_facts,
)
from .config import WardConfig, load_config
from .codex_runtime import run_codex_exec
from .platform import is_windows, runtime_root, stt_python_path
from .literature import (
    LITERATURE_TAXONOMY_CANDIDATES_FILE,
    openevidence_login as _openevidence_login,
    plan_literature_queries,
    retrieve_literature_sources,
    summarize_literature_sources,
    _refresh_literature_taxonomy_candidates,
)
from .literature_planner import LITERATURE_QUESTION_PLAN_FILE, validate_literature_question_plan
from .llm_normalizer import (
    LLM_NORMALIZATION_AUDIT_FILE,
    LLM_NORMALIZATION_FILE,
    LLM_NORMALIZED_TRANSCRIPT_FILE,
    run_llm_normalization,
)
from .medical_normalizer import dumps_json, normalize_transcript
from .privacy import default_policy, default_review_reasons, deidentify_text
from .soap_drafter import SOAP_AUDIT_FILE, SOAP_NOTE_FILE, SOAP_NOTE_JSON_FILE, draft_soap_note
from .soap_validator import SOAP_VALIDATION_FILE, validate_soap_note
from .stt_review import STT_REVIEW_CANDIDATES_FILE, stt_promote_approved, stt_review
from .taxonomy import (
    CLINICAL_SPECIALTY_MAP_FILE,
    LITERATURE_TAXONOMY_APPROVED_FILE,
    load_obsidian_diagnosis_keyword_sets,
    load_obsidian_problem_keyword_sets,
    load_obsidian_service_keyword_sets,
    load_yaml_dict,
)
from taxonomy.scripts.classify_soap import build_classification_json

SCHEMA_VERSION = "1.0"
STATE_FILE = "state.json"
INPUT_META_FILE = "input.meta.json"
TRANSCRIPT_FILE = "transcript.manual.txt"
RAW_TRANSCRIPT_FILE = "raw_transcript.txt"
NORMALIZED_TRANSCRIPT_FILE = "normalized_transcript.md"
CORRECTION_LOG_FILE = "correction_log.json"
UNCERTAIN_TERMS_FILE = "uncertain_terms.json"
CONFIRMED_TERMS_FILE = "confirmed_terms.json"
LLM_NORMALIZED_TRANSCRIPT_FILE = "llm_normalized_transcript.md"
LLM_NORMALIZATION_FILE = "llm_normalization.json"
LLM_NORMALIZATION_AUDIT_FILE = "llm_normalization_audit.json"
PROMPT_PACKAGE_FILE = "prompt.chatgpt.md"
IMPORTED_RESULT_FILE = "result.imported.md"
HERMES_RESULT_FILE = "result.hermes.md"
SOAP_DRAFT_FILE = "soap_draft.md"
WORKFLOW_REPORT_FILE = "workflow.report.json"
TRANSCRIPTION_META_FILE = "transcription.meta.json"
STT_COVERAGE_AUDIT_FILE = "stt_coverage_audit.json"
STT_RECOVERY_CANDIDATES_FILE = "stt_recovery_candidates.json"
CONFIRMED_STT_RECOVERY_FILE = "confirmed_stt_recovery.json"
STT_RULE_SYNC_FILE = "stt_rule_sync.json"
SEGMENTS_WHISPERX_FILE = "segments.whisperx.json"
DIARIZATION_SEGMENTS_FILE = "diarization.segments.json"
DIARIZATION_RTTM_FILE = "diarization.rttm"
SPEAKER_TRANSCRIPT_FILE = "transcript.speaker.txt"
DIARIZATION_RENDER_FILE = "diarization_render.md"
LITERATURE_PLAN_FILE = "literature_query_plan.json"
LITERATURE_SOURCES_FILE = "literature_sources.json"
LITERATURE_SUMMARY_FILE = "literature_summary.json"
LITERATURE_NARRATIVE_FILE = "openevidence_narrative.md"
DELIVERY_REPORT_FILE = "delivery.report.json"
CLASSIFICATION_FILE = "classification.json"
KEY_INSIGHTS_FILE = "key_insights.json"
DELIVERY_INTENT_FILE = "delivery.intent.json"
DELIVERY_RESOLUTION_FILE = "delivery.resolution.json"
CLINICAL_CHANGES_FILE = "clinical_changes.json"
CLINICAL_FACTS_FILE = "clinical_facts.json"
CLINICAL_FACTS_AUDIT_FILE = "clinical_facts_audit.json"
SOAP_NOTE_FILE = "soap_note.md"
SOAP_NOTE_JSON_FILE = "soap_note.json"
SOAP_AUDIT_FILE = "soap_audit.json"
SOAP_VALIDATION_FILE = "soap_validation.json"
ALERT_REPORT_FILES = (
    "alert_report.md",
    "alert_report.txt",
    "alert_report.json",
    "alerts.md",
    "alerts.json",
)
DISCORD_CHUNK_LIMIT = 1800
DELIVERY_RETRY_COUNT = 3
DELIVERY_RETRY_DELAY_SECONDS = 2
AUTO_STT_RECOVERY_MIN_TEXT_CHARS = 12
AUTO_STT_RECOVERY_MAX_SECONDS = 45.0
HEALTH_REPORT_FILE = "health.report.json"
HEALTH_SUMMARY_FILE = "health.summary.txt"
DEFAULT_HEALTH_DISCORD_TARGET = os.environ.get("WARD_HEALTH_DISCORD_TARGET", "")
OBSIDIAN_AUTO_ROUTE_MIN_CONFIDENCE = 0.7
AUTO_LITERATURE_MIN_CONFIDENCE = 0.4
WARD_RUNTIME_ROOT = Path(os.environ.get("WARD_RUNTIME_ROOT", runtime_root())).expanduser().resolve()
HERMES_BIN = os.environ.get("HERMES_BIN", "hermes")
WARD_BIN = os.environ.get("WARD_BIN", "ward")
WARD_STT_PYTHON = stt_python_path(WARD_RUNTIME_ROOT)
RETENTION_KEEP_JOB_FILES = {
    HERMES_RESULT_FILE,
    SOAP_NOTE_FILE,
    TRANSCRIPT_FILE,
    SPEAKER_TRANSCRIPT_FILE,
    RAW_TRANSCRIPT_FILE,
    NORMALIZED_TRANSCRIPT_FILE,
    UNCERTAIN_TERMS_FILE,
    CONFIRMED_TERMS_FILE,
    STT_COVERAGE_AUDIT_FILE,
    STT_RECOVERY_CANDIDATES_FILE,
    CONFIRMED_STT_RECOVERY_FILE,
    DIARIZATION_RENDER_FILE,
}
RETENTION_DELETE_JOB_FILES = {
    STATE_FILE,
    INPUT_META_FILE,
    PROMPT_PACKAGE_FILE,
    DELIVERY_INTENT_FILE,
    DELIVERY_REPORT_FILE,
    DELIVERY_RESOLUTION_FILE,
    CORRECTION_LOG_FILE,
    TRANSCRIPTION_META_FILE,
    SEGMENTS_WHISPERX_FILE,
    DIARIZATION_SEGMENTS_FILE,
    DIARIZATION_RTTM_FILE,
    LLM_NORMALIZED_TRANSCRIPT_FILE,
    LLM_NORMALIZATION_FILE,
    LLM_NORMALIZATION_AUDIT_FILE,
    CLINICAL_FACTS_FILE,
    CLINICAL_FACTS_AUDIT_FILE,
    SOAP_NOTE_JSON_FILE,
    SOAP_AUDIT_FILE,
    SOAP_VALIDATION_FILE,
}
RETENTION_DELETE_JOB_GLOBS = (
    "*.timing.json",
    "*.debug.json",
    "*.tmp",
    "*.bak",
)
RETENTION_DELETE_JOB_DIRS = {
    "stt_compare",
}
RETENTION_REVIEW_DIR = "_retention"
STT_RULE_SYNC_DIR = "_stt_rule_sync"
RETENTION_PROTECTED_ROOTS = (
    WARD_RUNTIME_ROOT / "ward_pipeline",
    WARD_RUNTIME_ROOT / "ward_cli.py",
    WARD_RUNTIME_ROOT / "README.md",
    WARD_RUNTIME_ROOT / "install.sh",
    Path.home() / ".hermes",
    Path.home() / ".codex",
)


class WardError(Exception):
    pass


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except FileNotFoundError:
        return path.expanduser().resolve().is_relative_to(root.resolve())
    except ValueError:
        return False


def _retention_path_protected(path: Path) -> bool:
    return any(_path_is_under(path, root) for root in RETENTION_PROTECTED_ROOTS)


def _now(config: WardConfig) -> datetime:
    return datetime.now(ZoneInfo(config.timezone))


def _iso_now(config: WardConfig) -> str:
    return _now(config).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _job_id(config: WardConfig) -> str:
    return f"{_now(config).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"


def _job_dir(config: WardConfig, job_id: str) -> Path:
    return config.output_dir / job_id


def resolve_job_id(config: WardConfig, job_id: str) -> str:
    if job_id != "latest":
        return job_id

    config.output_dir.mkdir(parents=True, exist_ok=True)
    state_files = sorted(
        config.output_dir.glob(f"*/{STATE_FILE}"),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    if not state_files:
        raise WardError("no jobs found")
    return state_files[0].parent.name


def _json_write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _json_read(path: Path) -> dict:
    if not path.exists():
        raise WardError(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_env_value(env_path: Path, key: str) -> str:
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        line_key, value = stripped.split("=", 1)
        if line_key.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def _state_path(config: WardConfig, job_id: str) -> Path:
    return _job_dir(config, resolve_job_id(config, job_id)) / STATE_FILE


def read_state(config: WardConfig, job_id: str) -> dict:
    return _json_read(_state_path(config, job_id))


def write_state(config: WardConfig, state: dict) -> None:
    state["updated_at"] = _iso_now(config)
    _json_write(_state_path(config, state["job_id"]), state)


def _write_stt_rule_sync_report(config: WardConfig, job_id: str, report: dict) -> None:
    job_dir = _job_dir(config, job_id)
    _json_write(job_dir / STT_RULE_SYNC_FILE, report)
    latest_dir = config.output_dir / STT_RULE_SYNC_DIR
    latest_dir.mkdir(parents=True, exist_ok=True)
    _json_write(latest_dir / "latest.json", report)


def _sync_stt_rules(config: WardConfig, job_id: str) -> dict:
    report = {
        "ok": False,
        "action": "stt-rule-sync",
        "job_id": job_id,
        "synced_at": _iso_now(config),
        "review_queue_dir": str(getattr(config, "stt_review_queue_dir", WARD_RUNTIME_ROOT / "data" / "stt_review_queue")),
    }
    try:
        promotion = stt_promote_approved(config, dry_run=False)
    except Exception as exc:
        report["error"] = str(exc)
        _write_stt_rule_sync_report(config, job_id, report)
        raise WardError(f"STT rule sync failed: {exc}") from exc

    report["ok"] = bool(promotion.get("ok"))
    report["promotion"] = promotion
    _write_stt_rule_sync_report(config, job_id, report)
    if not report["ok"]:
        raise WardError("STT rule sync failed")
    return report


def _record_delivery_state(
    config: WardConfig,
    state: dict,
    *,
    status: str,
    delivery_step: str,
    last_error: str | None = None,
) -> None:
    state["status"] = status
    state["current_step"] = "delivery"
    state["steps"]["delivery"] = delivery_step
    state["last_error"] = last_error
    write_state(config, state)


def _parse_delivery_target(target: str) -> dict:
    parts = target.split(":")
    if len(parts) < 2 or parts[0] != "discord":
        raise WardError("only delivery target format discord:CHAT_ID[:THREAD_ID] is supported")
    chat_id = parts[1].strip()
    thread_id = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    if not chat_id:
        raise WardError("delivery target is missing Discord chat_id")
    return {"platform": "discord", "chat_id": chat_id, "thread_id": thread_id}


def _discord_token() -> str:
    return (
        os.environ.get("DISCORD_BOT_TOKEN")
        or _load_env_value(Path.home() / ".hermes" / ".env", "DISCORD_BOT_TOKEN")
    )


def _discord_post_message(target: dict, content: str) -> dict:
    token = _discord_token()
    if not token:
        raise WardError("DISCORD_BOT_TOKEN is not configured")

    channel_id = target.get("thread_id") or target["chat_id"]
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "ward-cli-delivery/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            if response.status not in (200, 201):
                raise WardError(f"Discord API error ({response.status}): {body}")
            data = json.loads(body)
            return {
                "ok": True,
                "status": response.status,
                "message_id": data.get("id"),
                "channel_id": data.get("channel_id"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WardError(f"Discord API error ({exc.code}): {body}") from exc
    except urllib.error.URLError as exc:
        raise WardError(f"Discord delivery connection error: {exc.reason}") from exc


def _discord_edit_message(target: dict, message_id: str, content: str) -> dict:
    token = _discord_token()
    if not token:
        raise WardError("DISCORD_BOT_TOKEN is not configured")

    channel_id = target.get("thread_id") or target["chat_id"]
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}"
    payload = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="PATCH",
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": "ward-cli-delivery/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
            if response.status not in (200, 201):
                raise WardError(f"Discord API error ({response.status}): {body}")
            data = json.loads(body)
            return {
                "ok": True,
                "status": response.status,
                "message_id": data.get("id"),
                "channel_id": data.get("channel_id"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise WardError(f"Discord API error ({exc.code}): {body}") from exc
    except urllib.error.URLError as exc:
        raise WardError(f"Discord delivery connection error: {exc.reason}") from exc


def _http_get_json(url: str, headers: dict | None = None, timeout: int = 10) -> dict:
    request = urllib.request.Request(url, method="GET", headers=headers or {})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        return {"status": response.status, "body": json.loads(body) if body else {}}


def _chunk_text(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        chunk = remaining[:limit]
        split_at = chunk.rfind("\n")
        if split_at > 400:
            chunk = chunk[:split_at]
        chunks.append(chunk)
        remaining = remaining[len(chunk):].lstrip("\n")
    return chunks


def _format_artifact_section(label: str, path: Path, fence: str = "") -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    truncated = ""
    if len(content) > 12000:
        content = content[:12000]
        truncated = "\n\n[truncated: see artifact file for full content]"
    if fence:
        return f"## {label}\n\n```{fence}\n{content}{truncated}\n```"
    return f"## {label}\n\n{content}{truncated}"


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _read_optional_json(path: Path) -> list | dict:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": f"invalid json: {path.name}"}


def _transcript_context(job_dir: Path, fallback_transcript: str) -> dict:
    raw_transcript = _read_optional_text(job_dir / RAW_TRANSCRIPT_FILE) or fallback_transcript
    normalized_transcript = _read_optional_text(job_dir / NORMALIZED_TRANSCRIPT_FILE) or fallback_transcript
    confirmed_terms = _read_optional_json(job_dir / CONFIRMED_TERMS_FILE)
    uncertain_terms = _read_optional_json(job_dir / UNCERTAIN_TERMS_FILE)
    confirmed_stt_recovery = _read_optional_json(job_dir / CONFIRMED_STT_RECOVERY_FILE)
    diarization_render = _read_optional_text(job_dir / DIARIZATION_RENDER_FILE)
    if isinstance(confirmed_terms, list) and isinstance(uncertain_terms, list):
        confirmed_keys = {
            (str(item.get("original") or ""), str(item.get("corrected") or item.get("candidate") or ""))
            for item in confirmed_terms
        }
        uncertain_terms = [
            item
            for item in uncertain_terms
            if (
                str(item.get("original") or ""),
                str(item.get("candidate") or item.get("corrected") or ""),
            )
            not in confirmed_keys
        ]
    return {
        "raw_transcript": raw_transcript,
        "normalized_transcript": normalized_transcript,
        "correction_log": _read_optional_json(job_dir / CORRECTION_LOG_FILE),
        "confirmed_terms": confirmed_terms,
        "uncertain_terms": uncertain_terms,
        "confirmed_stt_recovery": confirmed_stt_recovery,
        "diarization_render": diarization_render,
    }


def _preferred_soap_artifact(job_dir: Path) -> tuple[Path, str]:
    soap_note = job_dir / SOAP_NOTE_FILE
    if soap_note.exists():
        return soap_note, SOAP_NOTE_FILE
    hermes_result = job_dir / HERMES_RESULT_FILE
    if hermes_result.exists():
        return hermes_result, HERMES_RESULT_FILE
    raise WardError(f"missing SOAP draft artifact: {soap_note}")


def _delivery_messages(job_dir: Path, job_id: str) -> list[str]:
    sections = [
        (
            f"SOAP draft for ward job `{job_id}`\n\n"
            "此為 AI 依逐字稿產生的草稿，請依實際病人狀況修改後再寫入正式病歷。"
        )
    ]

    result_path, source_name = _preferred_soap_artifact(job_dir)
    sections.append(_format_artifact_section(f"SOAP draft (`{source_name}`)", result_path))

    changes_path = job_dir / CLINICAL_CHANGES_FILE
    if changes_path.exists():
        sections.append(_format_artifact_section("Clinical changes JSON", changes_path, "json"))

    recovery_path = job_dir / STT_RECOVERY_CANDIDATES_FILE
    confirmed_recovery_path = job_dir / CONFIRMED_STT_RECOVERY_FILE
    if recovery_path.exists():
        recovery_payload = _read_optional_json(recovery_path)
        if isinstance(recovery_payload, dict) and recovery_payload.get("candidate_count"):
            confirmed_recovery = _read_optional_json(confirmed_recovery_path)
            candidates = recovery_payload.get("candidates") if isinstance(recovery_payload, dict) else []
            confirmed_ids = {
                str(item.get("candidate_id") or "").strip()
                for item in confirmed_recovery
                if isinstance(item, dict) and str(item.get("candidate_id") or "").strip()
            } if isinstance(confirmed_recovery, list) else set()
            pending_candidates = []
            confirmed_count = 0
            for item in candidates if isinstance(candidates, list) else []:
                if not isinstance(item, dict):
                    continue
                candidate_id = str(item.get("candidate_id") or "").strip()
                if candidate_id and candidate_id in confirmed_ids:
                    confirmed_count += 1
                    continue
                if item.get("requires_human_confirmation") is False:
                    confirmed_count += 1
                    continue
                pending_candidates.append(item)

            if pending_candidates:
                sections.append(_format_artifact_section("STT recovery candidates JSON", recovery_path, "json"))
            elif confirmed_count:
                sections.append(
                    "## STT recovery summary\n\n"
                    f"Confirmed STT recovery candidates: {confirmed_count}\n\n"
                    "Pending STT recovery candidates: 0\n\n"
                    f"Source artifact: `{recovery_path.name}`"
                )

    for name in ALERT_REPORT_FILES:
        alert_path = job_dir / name
        if alert_path.exists():
            fence = "json" if alert_path.suffix == ".json" else ""
            sections.append(_format_artifact_section("Alert report", alert_path, fence))
            break

    if len(sections) == 1:
        raise WardError("no deliverable SOAP, clinical changes, or alert report artifact found")

    messages = []
    for section in sections:
        messages.extend(_chunk_text(section))
    return messages


def _notify_local_delivery_failure(job_id: str) -> dict:
    title = "Ward SOAP delivery failed"
    message = f"SOAP 回傳失敗，請稍後重送：{job_id}"
    if is_windows():
        return {"ok": False, "method": "unsupported_platform", "error": "desktop notification is only implemented on macOS"}
    try:
        completed = subprocess.run(
            [
                "osascript",
                "-l",
                "JavaScript",
                "-e",
                (
                    "const app = Application.currentApplication(); "
                    "app.includeStandardAdditions = true; "
                    f"app.displayNotification({json.dumps(message)}, "
                    f"{{withTitle: {json.dumps(title)}}});"
                ),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {
            "ok": completed.returncode == 0,
            "method": "macos_notification",
            "error": (completed.stderr or "").strip() or None,
        }
    except Exception as exc:
        return {"ok": False, "method": "macos_notification", "error": str(exc)}


def _clinical_prompt(
    job_id: str,
    state: dict,
    transcript: str,
    target: str,
    deidentify: bool,
    literature_plan: dict | None = None,
    transcript_context: dict | None = None,
) -> str:
    context = transcript_context or {}
    raw_transcript = context.get("raw_transcript") or transcript
    normalized_transcript = context.get("normalized_transcript") or transcript
    correction_log = context.get("correction_log") if "correction_log" in context else []
    confirmed_terms = context.get("confirmed_terms") if "confirmed_terms" in context else []
    uncertain_terms = context.get("uncertain_terms") if "uncertain_terms" in context else []
    confirmed_stt_recovery = context.get("confirmed_stt_recovery") if "confirmed_stt_recovery" in context else []
    diarization_render = context.get("diarization_render") or ""
    transcript_section = normalized_transcript or "No transcript attached yet. Stop and report that transcription is required before clinical drafting."
    identity_rule = (
        "- De-identification was requested. Remove or mask patient identifiers before drafting."
        if deidentify
        else "- Preserve patient identifiers and bedside identity cues present in the transcript so the clinician can match this draft to the correct patient."
    )
    return f"""# Ward Review Package

Job ID: {job_id}
Target: {target}
De-identification requested: {str(deidentify).lower()}

## Output Goal

Generate a practical SOAP draft from the transcript for the clinician to edit on the phone.
This is not a final medical record. The clinician will verify and rewrite as needed before charting.
The transcript may be mixed Chinese and English, but the SOAP body must be written in natural medical English only.
Do not output Chinese inside the S/O/A/P body sections.
Use Chinese only in the final `需確認` section for uncertainty reminders and missing-information notes.
Translate Chinese clinical phrases into standard English medical phrasing, for example:
- 「咳嗽改善」 -> "cough improved"
- 「胸悶減輕」 -> "chest tightness improved"
- 「食慾尚可」 -> "appetite fair"
- 「未於逐字稿記載」 -> "Not documented in transcript."

Required output format:

S
- ...

O
- ...

A
- ...

P
- ...

需確認
- ...

## Routing

Append a machine-readable routing block after `需確認`.
Use exactly one fenced JSON object with these keys:

```json
{{
  "primary_specialty": "ophthalmology",
  "diagnosis_topics": ["subconjunctival_hemorrhage"],
  "confidence": "high",
  "routing_rationale": "short one-sentence reason"
}}
```

Routing rules:
- Base the specialty on the actual clinical problem in the note, not on the local taxonomy file.
- Use the most specific clinically responsible service when it is clear.
- If the case is not clearly owned by a subspecialty, use `general_internal_medicine`.
- `diagnosis_topics` should be stable snake_case problem labels.
- For a new disease or service, invent a sensible label instead of leaving it blank.

## Safety Rules

- Treat this as a clinical draft aid, not a final medical record.
- Do not invent missing facts.
- Mark uncertainty explicitly.
- Keep medications, allergies, abnormal labs, and plans in separate sections.
- Return a concise SOAP draft in English plus a short Chinese `需確認` list.
- Use both raw_transcript and normalized_transcript. Prefer normalized_transcript for medical spelling only when correction_log supports it.
- Apply confirmed_terms as clinician-confirmed corrections when interpreting the transcript.
- Treat confirmed_stt_recovery as accepted local retranscription evidence for previously missing STT coverage gaps. Use `confirmation_method` to distinguish clinician-confirmed entries from automated quality-gate entries.
- Use diarization_render only as auxiliary speaker/time structure. Do not treat speaker labels as patient identity or infer clinical roles from them.
- Do not invent medication names, doses, lab values, culture results, diagnoses, or procedures.
- Every remaining item in uncertain_terms must be marked 「需人工確認」 if it affects the SOAP draft.
- If objective data, assessment, or plan are not present in the transcript, write 「未於逐字稿記載」 instead of adding standard content.
- SOAP headings must remain `S`, `O`, `A`, `P`; section content should be clinically natural English.
- Do not translate word-for-word. Convert Chinese transcript content into standard medical English.
- Keep medication names, diagnoses, labs, vitals, and abbreviations in clinical English.
- Do not leave the SOAP body in Chinese when a standard clinical English phrase is available.
- Every sentence in S/O/A/P must be in English unless it is an unavoidable medication/proper noun abbreviation.
- If a Chinese phrase appears in the transcript, restate it in medical English rather than copying Chinese text.
- If you are unsure, use an English uncertainty phrase and place the detailed note in `需確認`.
{identity_rule}

## Current Job State

```json
{json.dumps(state, ensure_ascii=False, indent=2)}
```

## Input Handling

This job may contain clinical information. Preserve uncertainty and do not add facts that are not present in the transcript.

## Medical Normalization Inputs

The normalized transcript was produced by conservative post-processing. High-confidence corrections are listed in correction_log. Clinician-confirmed uncertain terms are listed in confirmed_terms. Remaining lower-confidence possible medical terms are listed in uncertain_terms and require clinician review.

### correction_log

```json
{json.dumps(correction_log, ensure_ascii=False, indent=2)}
```

### confirmed_terms

```json
{json.dumps(confirmed_terms, ensure_ascii=False, indent=2)}
```

### uncertain_terms

```json
{json.dumps(uncertain_terms, ensure_ascii=False, indent=2)}
```

### confirmed_stt_recovery

These are clinician-confirmed local retranscription snippets from coverage gaps. They may be used as SOAP evidence, but must remain traceable to their time window.

```json
{json.dumps(confirmed_stt_recovery, ensure_ascii=False, indent=2)}
```

## normalized_transcript

```text
{transcript_section}
```

## raw_transcript

```text
{raw_transcript or "No raw transcript artifact found."}
```

## diarization_render

This rendering mechanically groups transcript text by speaker label and time window. It is included to reduce source mixing in multi-speaker ward-round audio. It does not infer roles, authority, or patient identity.

```text
{diarization_render or "No diarization render artifact found."}
```
"""


def ingest(config: WardConfig, audio_path: Path) -> dict:
    source = audio_path.expanduser().resolve()
    if not source.exists():
        raise WardError(f"audio file does not exist: {source}")
    if not source.is_file():
        raise WardError(f"audio path is not a file: {source}")

    config.output_dir.mkdir(parents=True, exist_ok=True)
    job_id = _job_id(config)
    job_dir = _job_dir(config, job_id)
    job_dir.mkdir(parents=False, exist_ok=False)

    input_meta = {
        "original_path": str(source),
        "sha256": _sha256(source),
        "size_bytes": source.stat().st_size,
        "duration_seconds": None,
    }
    state = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "created_at": _iso_now(config),
        "updated_at": _iso_now(config),
        "status": "needs_review",
        "current_step": "ingest",
        "input": input_meta,
        "steps": {
            "ingest": "done",
            "normalize": "pending",
            "transcribe": "pending",
            "diarize": "pending",
            "clinical_extract": "pending",
            "soap_generate": "pending",
            "literature": "pending",
            "delivery": "blocked",
        },
        "needs_human_review": True,
        "review_reasons": default_review_reasons(),
        "policy": default_policy(),
        "artifacts": {
            "state": STATE_FILE,
            "input_meta": INPUT_META_FILE,
        },
        "last_error": None,
    }

    _json_write(job_dir / INPUT_META_FILE, input_meta)
    _json_write(job_dir / STATE_FILE, state)
    return {"ok": True, "action": "ingest", "job_id": job_id, "job_dir": str(job_dir), "state": state}


def status(config: WardConfig, job_id: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    state = read_state(config, job_id)
    return {
        "ok": True,
        "action": "status",
        "job_id": job_id,
        "status": state["status"],
        "current_step": state["current_step"],
        "needs_human_review": state["needs_human_review"],
        "review_reasons": state["review_reasons"],
        "policy": state["policy"],
        "updated_at": state["updated_at"],
    }


def list_jobs(config: WardConfig, today: bool = False) -> dict:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = _now(config).strftime("%Y%m%d") if today else None
    jobs = []

    for state_file in sorted(config.output_dir.glob(f"*/{STATE_FILE}"), reverse=True):
        job_id = state_file.parent.name
        if prefix and not job_id.startswith(prefix):
            continue
        state = _json_read(state_file)
        jobs.append(
            {
                "job_id": job_id,
                "status": state.get("status"),
                "current_step": state.get("current_step"),
                "created_at": state.get("created_at"),
                "updated_at": state.get("updated_at"),
                "needs_human_review": state.get("needs_human_review"),
            }
        )

    return {"ok": True, "action": "list", "today": today, "jobs": jobs}


def inspect(config: WardConfig, job_id: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    artifacts = sorted(path.name for path in job_dir.iterdir() if path.is_file())
    return {
        "ok": True,
        "action": "inspect",
        "job_id": job_id,
        "job_dir": str(job_dir),
        "state": state,
        "artifacts": artifacts,
    }


def _obsidian_filename(ward_date: str, bed_id: str, job_id: str) -> str:
    return f"{ward_date}_bed-{bed_id}_{job_id}.md"


def _yaml_frontmatter(payload: dict[str, object]) -> str:
    lines = ["---"]
    for key, value in payload.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {json.dumps(str(item), ensure_ascii=False)}")
        else:
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def _split_soap_sections(content: str) -> tuple[dict[str, list[str]], list[str]]:
    headers = {
        "S": "subjective",
        "O": "objective",
        "A": "assessment",
        "P": "plan",
        "## Subjective": "subjective",
        "## Objective": "objective",
        "## Assessment": "assessment",
        "## Plan": "plan",
        "需確認": "confirm",
        "## 需確認": "confirm",
        "## Routing": "routing",
        "## Hermes Routing": "routing",
    }
    sections: dict[str, list[str]] = {value: [] for value in headers.values()}
    raw_lines: list[str] = []
    current: str | None = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped in headers:
            current = headers[stripped]
            continue
        if current == "routing":
            continue
        raw_lines.append(line)
        if current is not None:
            sections[current].append(line)
    return sections, raw_lines


def _clean_section_lines(lines: list[str]) -> list[str]:
    return [line.rstrip() for line in lines if line.strip()]


def _merge_topic_labels(*values: object) -> list[str]:
    topics: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for item in value:
            text = str(item).strip()
            if text and text not in topics:
                topics.append(text)
    return topics


def _infer_obsidian_service(result_text: str) -> str:
    text = result_text.lower()
    for service, keywords in load_obsidian_service_keyword_sets():
        if any(keyword in text for keyword in keywords):
            return service
    return ""


def _collect_obsidian_labels(text: str, keyword_sets: tuple[tuple[str, tuple[str, ...]], ...]) -> list[str]:
    matched: list[str] = []
    for label, keywords in keyword_sets:
        if any(keyword in text for keyword in keywords):
            matched.append(label)
    return matched


def _infer_obsidian_taxonomy(result_text: str) -> dict[str, object]:
    text = result_text.lower()
    diagnosis_topics = _collect_obsidian_labels(text, load_obsidian_diagnosis_keyword_sets())
    problem_keywords = _collect_obsidian_labels(text, load_obsidian_problem_keyword_sets())
    teaching_topics: list[str] = []
    if "pcp" in diagnosis_topics:
        teaching_topics.append("opportunistic pneumonia workup")
        teaching_topics.append("empiric PCP treatment")
    if "hiv" in diagnosis_topics:
        teaching_topics.append("immunocompromised host evaluation")
    if "stroke" in diagnosis_topics:
        teaching_topics.append("time-sensitive neuro deficit review")
    if "sepsis" in diagnosis_topics:
        teaching_topics.append("early sepsis bundle")

    follow_up_type: list[str] = []
    if any(marker in text for marker in ("pending", "await", "follow up", "follow-up")):
        follow_up_type.append("test-result-follow-up")
    if any(marker in text for marker in ("cd4", "hiv combo test", "blood culture", "sputum", "bronchoscopy")):
        follow_up_type.append("diagnostic-follow-up")

    teaching_value = ""
    if "pcp" in diagnosis_topics and "hiv" in diagnosis_topics:
        teaching_value = "Immunocompromised pneumonia with hypoxemia should keep PCP high on the differential."
    elif "stroke" in diagnosis_topics:
        teaching_value = "Time-sensitive neurologic syndromes should keep bedside deficits and imaging aligned."
    elif "sepsis" in diagnosis_topics:
        teaching_value = "Sepsis workflows should keep cultures, antibiotics, and escalation aligned early."

    paper_topics: list[str] = []
    if "pcp" in diagnosis_topics:
        paper_topics.append("opportunistic-infection")
    if "stroke" in diagnosis_topics:
        paper_topics.append("acute-neurology")

    return {
        "diagnosis_topics": diagnosis_topics,
        "problem_keywords": problem_keywords,
        "teaching_topics": teaching_topics,
        "paper_topics": paper_topics,
        "paper_candidate": False,
        "teaching_value": teaching_value,
        "follow_up_needed": bool(follow_up_type),
        "follow_up_type": follow_up_type,
    }


def _build_obsidian_classification(job_dir: Path, result_text: str) -> dict[str, object]:
    try:
        for taxonomy_path in (CLINICAL_SPECIALTY_MAP_FILE, LITERATURE_TAXONOMY_APPROVED_FILE):
            taxonomy_payload = load_yaml_dict(taxonomy_path)
            if taxonomy_payload.get("_load_error"):
                raise RuntimeError(f"{taxonomy_path.name}: {taxonomy_payload['_load_error']}")
        classification = build_classification_json(result_text)
        _json_write(job_dir / CLASSIFICATION_FILE, classification)
        return {
            "ok": True,
            "classification": classification,
            "classification_path": str(job_dir / CLASSIFICATION_FILE),
            "source": "yaml_deterministic",
        }
    except Exception as exc:
        return {
            "ok": False,
            "classification": None,
            "error": str(exc),
            "source": "fallback_lightweight",
        }


def _render_obsidian_note(
    state: dict,
    result_text: str,
    *,
    source_type: str = HERMES_RESULT_FILE,
    classification: dict[str, object] | None = None,
    literature_summary: dict[str, object] | None = None,
    openevidence_narrative: str | None = None,
) -> str:
    created_at = str(state.get("created_at") or "")
    ward_date = created_at[:10] if len(created_at) >= 10 else "unknown-date"
    routing = state.get("routing") or {}
    bed_id = str(routing.get("bed_id") or "unknown")
    encounter_id = str(routing.get("encounter_id") or "")
    sections, raw_lines = _split_soap_sections(result_text)
    taxonomy = _infer_obsidian_taxonomy(result_text)
    classification = classification or {}
    classification_service = str(classification.get("primary_specialty") or "").strip()
    service = classification_service or _infer_obsidian_service(result_text)
    diagnosis_topics = _merge_topic_labels(classification.get("diagnosis_topics"), taxonomy["diagnosis_topics"])

    frontmatter = _yaml_frontmatter(
        {
            "note_type": "ward_soap_draft",
            "job_id": str(state.get("job_id") or ""),
            "ward_date": ward_date,
            "created_at": created_at or "",
            "bed_id": bed_id,
            "encounter_id": encounter_id,
            "service": service,
            "care_setting": "ward",
            "needs_human_review": bool(state.get("needs_human_review")),
            "draft_status": "draft",
            "diagnosis_topics": diagnosis_topics,
            "problem_keywords": taxonomy["problem_keywords"],
            "teaching_topics": taxonomy["teaching_topics"],
            "paper_topics": taxonomy["paper_topics"],
            "paper_candidate": taxonomy["paper_candidate"],
            "teaching_value": taxonomy["teaching_value"],
            "follow_up_needed": taxonomy["follow_up_needed"],
            "follow_up_type": taxonomy["follow_up_type"],
            "suggested_obsidian_folder": str(classification.get("suggested_obsidian_folder") or ""),
            "classification_confidence": classification.get("confidence", ""),
            "source_type": source_type,
            "tags": ["ward-round", "soap-draft"],
        }
    )

    note_lines = [
        frontmatter,
        "",
        f"# {ward_date} Ward SOAP Draft",
        "",
        "## SOAP",
        "",
    ]

    named_sections = [
        ("### Subjective", _clean_section_lines(sections["subjective"])),
        ("### Objective", _clean_section_lines(sections["objective"])),
        ("### Assessment", _clean_section_lines(sections["assessment"])),
        ("### Plan", _clean_section_lines(sections["plan"])),
    ]
    if any(lines for _, lines in named_sections):
        for title, lines in named_sections:
            note_lines.append(title)
            if lines:
                note_lines.extend(lines)
            else:
                note_lines.append("- Not documented.")
            note_lines.append("")
    else:
        note_lines.extend(_clean_section_lines(raw_lines))
        note_lines.append("")

    confirm_lines = _clean_section_lines(sections["confirm"])
    note_lines.extend(
        [
            "## 需確認",
            *(confirm_lines or ["- None documented."]),
            "",
            "## 後續追蹤",
            "- ",
            "",
            "## 教學重點",
            "- ",
            "",
            "## 論文/病例整理想法",
            "- ",
            "",
        ]
    )
    summary = literature_summary or {}
    key_points = summary.get("key_points") if isinstance(summary.get("key_points"), list) else []
    evidence_items = summary.get("evidence_items") if isinstance(summary.get("evidence_items"), list) else []
    source_count = int(_safe_float(summary.get("source_count")))
    note_lines.append("## Literature Summary")
    if key_points:
        note_lines.append(f"- Source count: {source_count}")
        for point in key_points[:5]:
            rendered = str(point).strip()
            if rendered:
                note_lines.append(f"- {rendered}")
        if evidence_items:
            note_lines.append("")
            note_lines.append("### Retrieved Sources")
            grouped_sources: dict[str, list[dict[str, object]]] = {"PubMed": [], "OpenEvidence": [], "Other": []}
            for raw_item in evidence_items[:10]:
                if not isinstance(raw_item, dict):
                    continue
                source_name = str(raw_item.get("source") or "").strip().lower()
                if source_name == "pubmed":
                    grouped_sources["PubMed"].append(raw_item)
                elif source_name == "openevidence":
                    grouped_sources["OpenEvidence"].append(raw_item)
                else:
                    grouped_sources["Other"].append(raw_item)

            def _render_group(header: str, items: list[dict[str, object]]) -> None:
                if not items:
                    return
                note_lines.append("")
                note_lines.append(f"### {header}")
                for item in items:
                    title = str(item.get("title") or "").strip() or "Untitled source"
                    source = str(item.get("source") or "").strip() or "Literature source"
                    relevance = str(item.get("relevance") or "").strip()
                    summary_text = str(item.get("summary") or "").strip()
                    evidence_level = str(item.get("evidence_level") or "").strip()
                    url = str(item.get("url") or "").strip()
                    if url:
                        note_lines.append(f"- {source}: [{title}]({url})")
                    else:
                        note_lines.append(f"- {source}: {title}")
                    if evidence_level:
                        note_lines.append(f"  - Evidence Level: {evidence_level}")
                    if relevance:
                        note_lines.append(f"  - Relevance: {relevance}")
                    if summary_text:
                        note_lines.append(f"  - Summary: {summary_text}")

            _render_group("PubMed Sources", grouped_sources["PubMed"])
            _render_group("OpenEvidence Sources", grouped_sources["OpenEvidence"])
            _render_group("Other Sources", grouped_sources["Other"])
    else:
        note_lines.append("- None yet.")
    narrative_text = str(openevidence_narrative or "").strip()
    if narrative_text:
        note_lines.append("")
        note_lines.append("### OpenEvidence Narrative")
        note_lines.append(narrative_text)
    note_lines.append("")
    return "\n".join(note_lines)


def _build_key_insights(
    result_text: str,
    *,
    classification_result: dict[str, object],
) -> dict[str, object]:
    taxonomy = _infer_obsidian_taxonomy(result_text)
    classification_payload = classification_result.get("classification") or {}
    primary_specialty = str(classification_payload.get("primary_specialty") or "").strip()
    diagnosis_topics = _merge_topic_labels(classification_payload.get("diagnosis_topics"), taxonomy["diagnosis_topics"])
    matched_terms = classification_payload.get("matched_terms")
    if not isinstance(matched_terms, list):
        matched_terms = []
    return {
        "source": str(classification_result.get("source") or ""),
        "primary_specialty": primary_specialty,
        "suggested_obsidian_folder": str(classification_payload.get("suggested_obsidian_folder") or ""),
        "classification_confidence": classification_payload.get("confidence", ""),
        "reason_summary": str(classification_payload.get("classification_reason_summary") or ""),
        "diagnosis_topics": diagnosis_topics,
        "problem_keywords": taxonomy["problem_keywords"],
        "follow_up_needed": taxonomy["follow_up_needed"],
        "follow_up_type": taxonomy["follow_up_type"],
        "matched_terms": matched_terms,
    }


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _obsidian_route_folder(classification_result: dict[str, object]) -> tuple[str, str]:
    if not classification_result.get("ok"):
        return "Clinical Drafts/Unsorted", "unsorted_classification_failed"
    classification_payload = classification_result.get("classification") or {}
    confidence = _safe_float(classification_payload.get("confidence"))
    suggested_folder = str(classification_payload.get("suggested_obsidian_folder") or "").strip()
    if (
        suggested_folder
        and confidence >= OBSIDIAN_AUTO_ROUTE_MIN_CONFIDENCE
        and not suggested_folder.startswith("/")
        and ".." not in suggested_folder
    ):
        if suggested_folder.startswith("Medicine/Unmapped_Clinical_Domain/"):
            return suggested_folder, "auto_routed_by_unmapped_clinical_domain"
        return suggested_folder, "auto_routed_by_taxonomy"
    if not suggested_folder:
        return "Clinical Drafts/Unsorted", "unsorted_no_suggested_folder"
    return "Clinical Drafts/Unsorted", "unsorted_low_confidence"


def _should_auto_literature_enrich(result_text: str, plan: dict[str, object] | None) -> tuple[bool, str]:
    if not isinstance(plan, dict):
        return False, "skip_missing_plan"
    search_targets = plan.get("search_targets")
    if not isinstance(search_targets, list) or not search_targets:
        return False, "skip_no_search_targets"
    clinical = plan.get("clinical_classification") if isinstance(plan.get("clinical_classification"), dict) else {}
    diagnosis_topics = clinical.get("diagnosis_topics")
    if not isinstance(diagnosis_topics, list) or not diagnosis_topics:
        return False, "skip_no_diagnosis_topics"
    try:
        confidence = _safe_float(build_classification_json(result_text).get("confidence"))
    except Exception:
        return False, "skip_classification_unavailable"
    if confidence < AUTO_LITERATURE_MIN_CONFIDENCE:
        return False, "skip_low_confidence"
    return True, "run_auto_literature"


def export_obsidian(config: WardConfig, job_id: str, vault_dir: Path | None = None) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    result_path, source_name = _preferred_soap_artifact(job_dir)

    created_at = str(state.get("created_at") or "")
    ward_date = created_at[:10] if len(created_at) >= 10 else "unknown-date"
    routing = state.get("routing") or {}
    bed_id = str(routing.get("bed_id") or "unknown")
    year, month = (ward_date.split("-") + ["00", "00"])[:2]

    target_vault = (vault_dir or config.obsidian_vault_dir).expanduser().resolve()
    result_text = result_path.read_text(encoding="utf-8", errors="replace")
    classification_result = _build_obsidian_classification(job_dir, result_text)
    classification = classification_result.get("classification") if classification_result.get("ok") else None
    key_insights = _build_key_insights(result_text, classification_result=classification_result)
    _json_write(job_dir / KEY_INSIGHTS_FILE, key_insights)
    literature_summary = None
    openevidence_narrative = None
    summary_path = job_dir / LITERATURE_SUMMARY_FILE
    if summary_path.exists():
        loaded_summary = _json_read(summary_path)
        if isinstance(loaded_summary, dict):
            literature_summary = loaded_summary
    narrative_path = job_dir / LITERATURE_NARRATIVE_FILE
    if narrative_path.exists():
        raw_narrative = narrative_path.read_text(encoding="utf-8", errors="replace")
        cleaned_lines: list[str] = []
        blank_run = 0
        for line in raw_narrative.splitlines():
            text = line.rstrip()
            stripped = text.strip()
            if stripped.startswith("# OpenEvidence Narrative"):
                continue
            if stripped.startswith("- Query:"):
                continue
            if stripped:
                blank_run = 0
                cleaned_lines.append(text)
            else:
                blank_run += 1
                if blank_run <= 1:
                    cleaned_lines.append("")
        openevidence_narrative = "\n".join(cleaned_lines).strip()
    routed_folder, route_status = _obsidian_route_folder(classification_result)
    note_dir = target_vault / Path(routed_folder) / year / month
    note_dir.mkdir(parents=True, exist_ok=True)
    note_path = note_dir / _obsidian_filename(ward_date, bed_id, job_id)
    note_text = _render_obsidian_note(
        state,
        result_text,
        source_type=source_name,
        classification=classification,
        literature_summary=literature_summary,
        openevidence_narrative=openevidence_narrative,
    )
    note_path.write_text(note_text, encoding="utf-8")

    response = {
        "ok": True,
        "action": "export-obsidian",
        "job_id": job_id,
        "vault_dir": str(target_vault),
        "note_dir": str(note_dir),
        "note_path": str(note_path),
        "obsidian_route": {
            "folder": routed_folder,
            "status": route_status,
            "min_confidence": OBSIDIAN_AUTO_ROUTE_MIN_CONFIDENCE,
        },
        "source_artifact": str(result_path),
        "source_type": source_name,
        "key_insights_path": str(job_dir / KEY_INSIGHTS_FILE),
        "classification": {
            "ok": bool(classification_result.get("ok")),
            "source": classification_result.get("source"),
        },
    }
    if classification_result.get("ok"):
        response["classification"]["path"] = classification_result.get("classification_path")
    else:
        response["classification"]["error"] = classification_result.get("error")
    return response


def _literature_source_text(job_dir: Path) -> tuple[str, str]:
    parts: list[str] = []
    source_names: list[str] = []
    try:
        soap_path, soap_source = _preferred_soap_artifact(job_dir)
    except WardError:
        soap_path = None
        soap_source = ""
    if soap_path and soap_path.exists():
        parts.append(soap_path.read_text(encoding="utf-8", errors="replace"))
        source_names.append(soap_source)

    transcript_path = job_dir / TRANSCRIPT_FILE
    if transcript_path.exists():
        parts.append(transcript_path.read_text(encoding="utf-8", errors="replace"))
        source_names.append(TRANSCRIPT_FILE)

    return "\n\n".join(part for part in parts if part.strip()), "+".join(source_names)


def _refresh_literature_taxonomy_queue(job_dir: Path, job_id: str) -> dict | None:
    source_text, source_type = _literature_source_text(job_dir)
    if not source_text.strip():
        return None
    return _refresh_literature_taxonomy_candidates(source_text, source_type=source_type or "unknown", source_id=job_id)


def _update_literature_plan_artifact(job_dir: Path, job_id: str, state: dict) -> dict | None:
    source_text, source_type = _literature_source_text(job_dir)
    if not source_text.strip():
        return None
    plan = plan_literature_queries(source_text, source_type=source_type or "unknown")
    plan["job_id"] = job_id
    _json_write(job_dir / LITERATURE_PLAN_FILE, plan)
    state.setdefault("artifacts", {})["literature_query_plan"] = LITERATURE_PLAN_FILE
    state.setdefault("steps", {})["literature"] = "planned" if plan["search_targets"] else "needs_clinical_question"
    _refresh_literature_taxonomy_queue(job_dir, job_id)
    return plan


def _retention_cutoff_timestamp(age_days: int) -> float:
    return time.time() - (age_days * 86400)


def _retention_file_entry(path: Path, *, category: str, group: str, reason: str) -> dict:
    stat = path.lstat()
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "category": category,
        "group": group,
        "reason": reason,
    }


def _retention_manifest_hash(candidates: list[dict]) -> str:
    manifest = [
        {
            "path": item.get("path"),
            "size_bytes": item.get("size_bytes"),
            "mtime": item.get("mtime"),
            "category": item.get("category"),
            "group": item.get("group"),
            "reason": item.get("reason"),
        }
        for item in candidates
    ]
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _retention_review_path(config: WardConfig, created_at: str) -> Path:
    stamp = created_at.replace("-", "").replace(":", "").replace("T", "_")
    return config.output_dir / RETENTION_REVIEW_DIR / f"retention_dry_run_{stamp}.json"


def _retention_job_groups(config: WardConfig) -> list[Path]:
    if not config.output_dir.exists():
        return []
    return sorted((path for path in config.output_dir.iterdir() if path.is_dir() and path.name != "_health"), key=lambda p: p.name)


def _retention_case_groups(config: WardConfig) -> list[Path]:
    if not config.case_view_dir.exists():
        return []
    return sorted((path for path in config.case_view_dir.iterdir() if path.is_dir()), key=lambda p: p.name)


def retention_dry_run(
    config: WardConfig,
    *,
    age_days: int = 14,
    limit: int = 200,
    write_artifact: bool = False,
    artifact_path: Path | None = None,
) -> dict:
    if age_days < 0:
        raise WardError("age_days must be >= 0")
    if limit < 1:
        raise WardError("limit must be >= 1")

    cutoff = _retention_cutoff_timestamp(age_days)
    candidates: list[dict] = []

    for job_dir in _retention_job_groups(config):
        group = job_dir.name
        for child in sorted(job_dir.iterdir(), key=lambda p: p.name):
            if _retention_path_protected(child):
                continue
            try:
                mtime = child.lstat().st_mtime
            except FileNotFoundError:
                continue
            if mtime > cutoff:
                continue
            if child.is_dir():
                if child.name in RETENTION_DELETE_JOB_DIRS:
                    candidates.append(
                        _retention_file_entry(
                            child,
                            category="job_dir",
                            group=group,
                            reason=f"output blacklist directory older than {age_days} days",
                        )
                    )
                continue
            if child.name in RETENTION_KEEP_JOB_FILES:
                continue
            if child.name in RETENTION_DELETE_JOB_FILES or any(fnmatch.fnmatch(child.name, pattern) for pattern in RETENTION_DELETE_JOB_GLOBS):
                candidates.append(
                    _retention_file_entry(
                        child,
                        category="job_file",
                        group=group,
                        reason=f"output blacklist file older than {age_days} days",
                    )
                )

    for case_root in _retention_case_groups(config):
        for artifacts_dir in sorted(case_root.glob("*/artifacts")):
            if _retention_path_protected(artifacts_dir):
                continue
            try:
                mtime = artifacts_dir.lstat().st_mtime
            except FileNotFoundError:
                continue
            if not artifacts_dir.is_dir() or mtime > cutoff:
                continue
            candidates.append(
                _retention_file_entry(
                    artifacts_dir,
                    category="case_artifacts_dir",
                    group=case_root.name,
                    reason=f"case presentation artifacts directory older than {age_days} days",
                )
            )

    candidates.sort(key=lambda item: (item["group"], item["path"]))
    total_bytes = sum(int(item["size_bytes"]) for item in candidates)
    manifest_sha256 = _retention_manifest_hash(candidates)
    groups: dict[str, dict] = {}
    for item in candidates:
        bucket = groups.setdefault(item["group"], {"count": 0, "size_bytes": 0, "categories": set()})
        bucket["count"] += 1
        bucket["size_bytes"] += int(item["size_bytes"])
        bucket["categories"].add(item["category"])

    grouped_candidates = [
        {
            "group": group,
            "count": payload["count"],
            "size_bytes": payload["size_bytes"],
            "categories": sorted(payload["categories"]),
        }
        for group, payload in sorted(groups.items())
    ]

    created_at = datetime.now().isoformat(timespec="seconds")
    full_payload = {
        "ok": True,
        "action": "retention-dry-run",
        "policy": "retention_cleanup_safe_review_v1",
        "created_at": created_at,
        "age_days": age_days,
        "cutoff_before": datetime.fromtimestamp(cutoff).isoformat(timespec="seconds"),
        "candidate_count": len(candidates),
        "total_reclaimable_bytes": total_bytes,
        "candidate_manifest_sha256": manifest_sha256,
        "groups": grouped_candidates,
        "candidates": candidates,
        "truncated": False,
        "limit": None,
        "approval_gate": {
            "status": "pending_review",
            "required_before_deletion": True,
            "requirements": [
                "Review this dry-run artifact before any deletion command is run.",
                "Confirm the candidate_count and total_reclaimable_bytes are expected.",
                "Confirm every path is inside daily job output or case presentation artifacts.",
                "Future deletion command must verify candidate_manifest_sha256 before deleting.",
            ],
        },
        "protected_roots": [str(path) for path in RETENTION_PROTECTED_ROOTS],
        "notes": [
            "Dry-run only. No files were deleted.",
            "Scope is limited to daily job output files, stt_compare directories, and ward_cases presentation artifacts older than the cutoff.",
            "Workflow source files, handoff documents, Hermes config/memory, and Codex memory roots are protected and never considered by retention.",
        ],
    }

    artifact_written = None
    if write_artifact:
        destination = artifact_path.expanduser().resolve() if artifact_path else _retention_review_path(config, created_at)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _retention_path_protected(destination):
            raise WardError(f"refusing to write retention artifact under protected path: {destination}")
        _json_write(destination, full_payload)
        artifact_written = str(destination)

    response = dict(full_payload)
    response["candidates"] = candidates[:limit]
    response["truncated"] = len(candidates) > limit
    response["limit"] = limit
    response["review_artifact"] = artifact_written
    response["approval_gate"] = {
        **full_payload["approval_gate"],
        "review_artifact": artifact_written,
        "candidate_manifest_sha256": manifest_sha256,
    }
    if response["truncated"]:
        response["notes"] = response["notes"] + [
            "CLI output is truncated; use --write-artifact to persist the full candidate list for review.",
        ]
    return response


def export_prompt(config: WardConfig, job_id: str, target: str, deidentify: bool) -> dict:
    if target != "chatgpt":
        raise WardError("only target=chatgpt is supported in the minimal CLI")

    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    transcript_path = job_dir / TRANSCRIPT_FILE
    transcript = transcript_path.read_text(encoding="utf-8") if transcript_path.exists() else ""
    literature_plan = plan_literature_queries(transcript) if transcript else None
    if literature_plan:
        _json_write(job_dir / LITERATURE_PLAN_FILE, literature_plan)
        state["artifacts"]["literature_query_plan"] = LITERATURE_PLAN_FILE
        state["steps"]["literature"] = "planned"
    transcript_context = _transcript_context(job_dir, transcript)
    prompt = _clinical_prompt(
        job_id,
        state,
        transcript,
        "ChatGPT manual workflow",
        deidentify,
        literature_plan,
        transcript_context,
    )
    prompt_path = job_dir / PROMPT_PACKAGE_FILE
    prompt_path.write_text(prompt, encoding="utf-8")

    state["artifacts"]["prompt_package"] = PROMPT_PACKAGE_FILE
    state["steps"]["clinical_extract"] = "blocked"
    state["steps"]["soap_generate"] = "blocked"
    state["last_error"] = None
    write_state(config, state)

    return {
        "ok": True,
        "action": "export-prompt",
        "job_id": job_id,
        "target": target,
        "deidentify": deidentify,
        "prompt_path": str(prompt_path),
    }


def attach_transcript(config: WardConfig, job_id: str, transcript_path: Path) -> dict:
    job_id = resolve_job_id(config, job_id)
    source = transcript_path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise WardError(f"transcript file does not exist: {source}")

    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    destination = job_dir / TRANSCRIPT_FILE
    transcript_text = source.read_text(encoding="utf-8")
    destination.write_text(transcript_text, encoding="utf-8")

    state["artifacts"]["transcript_manual"] = TRANSCRIPT_FILE
    state["steps"]["transcribe"] = "done"
    state["current_step"] = "transcribe"
    state["status"] = "needs_review"
    state["needs_human_review"] = True
    if "manual_transcript_requires_review" not in state["review_reasons"]:
        state["review_reasons"].append("manual_transcript_requires_review")
    write_state(config, state)

    return {
        "ok": True,
        "action": "attach-transcript",
        "job_id": job_id,
        "transcript_path": str(destination),
        "size_bytes": destination.stat().st_size,
    }


def _confirmed_term_entry(term: dict, confirmed_at: str, source: str) -> dict:
    corrected = str(term.get("corrected") or term.get("candidate") or "").strip()
    original = str(term.get("original") or "").strip()
    if not original or not corrected:
        raise WardError("confirmed terms require both original and corrected/candidate values")
    return {
        "original": original,
        "corrected": corrected,
        "reason": "clinician confirmed uncertain medical term",
        "confidence": "confirmed",
        "category": term.get("category"),
        "line": term.get("line"),
        "confirmed_at": confirmed_at,
        "source": source,
    }


def confirm_terms(
    config: WardConfig,
    job_id: str,
    *,
    all_uncertain: bool = False,
    original: str | None = None,
    corrected: str | None = None,
) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    confirmed_at = _iso_now(config)

    existing = _read_optional_json(job_dir / CONFIRMED_TERMS_FILE)
    if not isinstance(existing, list):
        existing = []

    additions: list[dict] = []
    if all_uncertain:
        uncertain = _read_optional_json(job_dir / UNCERTAIN_TERMS_FILE)
        if not isinstance(uncertain, list) or not uncertain:
            raise WardError("no uncertain terms found to confirm")
        additions = [_confirmed_term_entry(term, confirmed_at, "uncertain_terms") for term in uncertain]
    else:
        if not original or not corrected:
            raise WardError("pass --all-uncertain or provide both --original and --corrected")
        additions = [
            _confirmed_term_entry(
                {"original": original, "corrected": corrected},
                confirmed_at,
                "manual",
            )
        ]

    merged_by_key = {
        (str(item.get("original") or ""), str(item.get("corrected") or "")): item
        for item in existing
        if item.get("original") and item.get("corrected")
    }
    for item in additions:
        merged_by_key[(item["original"], item["corrected"])] = item
    confirmed_terms = list(merged_by_key.values())
    (job_dir / CONFIRMED_TERMS_FILE).write_text(json.dumps(confirmed_terms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    state.setdefault("artifacts", {})["confirmed_terms"] = CONFIRMED_TERMS_FILE
    state["confirmed_terms"] = {
        "count": len(confirmed_terms),
        "updated_at": confirmed_at,
    }
    state["last_error"] = None
    write_state(config, state)

    case_view = None
    try:
        case_view = build_case_view(config, job_id)
    except Exception as exc:
        case_view = {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "action": "confirm-terms",
        "job_id": job_id,
        "confirmed_terms_path": str(job_dir / CONFIRMED_TERMS_FILE),
        "confirmed_count": len(confirmed_terms),
        "added_count": len(additions),
        "case_view": case_view,
    }


def _previous_delivery_target(job_dir: Path) -> str | None:
    intent = _read_optional_json(job_dir / DELIVERY_INTENT_FILE)
    if isinstance(intent, dict):
        target = str(intent.get("target") or "").strip()
        if target:
            return target
    return None


def _stt_recovery_duration(candidate: dict) -> float | None:
    start = candidate.get("start")
    end = candidate.get("end")
    try:
        return max(float(end) - float(start), 0.0)
    except (TypeError, ValueError):
        return None


def _auto_stt_recovery_reason(candidate: dict) -> str | None:
    text = str(candidate.get("text") or "").strip()
    if len(text) < AUTO_STT_RECOVERY_MIN_TEXT_CHARS:
        return None
    if candidate.get("source") != "local_retranscribe_no_initial_prompt":
        return None

    duration = _stt_recovery_duration(candidate)
    if duration is None or duration > AUTO_STT_RECOVERY_MAX_SECONDS:
        return None

    segments = candidate.get("segments")
    if not isinstance(segments, list) or not segments:
        return None

    removed_segments = candidate.get("removed_segments")
    if isinstance(removed_segments, list) and removed_segments:
        return None

    return "local_retranscribe_quality_gate"


def _confirmed_stt_recovery_entry(
    candidate: dict,
    confirmed_at: str,
    *,
    confidence: str = "clinician_confirmed_stt_recovery",
    confirmation_method: str = "manual",
    auto_confirm_reason: str | None = None,
) -> dict:
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    text = str(candidate.get("text") or "").strip()
    if not candidate_id or not text:
        raise WardError("confirmed STT recovery requires both candidate_id and text")
    entry = {
        "candidate_id": candidate_id,
        "gap_id": candidate.get("gap_id"),
        "start": candidate.get("start"),
        "end": candidate.get("end"),
        "text": text,
        "source": candidate.get("source") or "stt_recovery_candidates",
        "confidence": confidence,
        "confirmation_method": confirmation_method,
        "confirmed_at": confirmed_at,
        "requires_human_confirmation": False,
    }
    if auto_confirm_reason:
        entry["auto_confirm_reason"] = auto_confirm_reason
    return entry


def confirm_stt_recovery(
    config: WardConfig,
    job_id: str,
    *,
    include_all: bool = False,
    candidate_ids: list[str] | None = None,
) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    confirmed_at = _iso_now(config)
    candidates_payload = _read_optional_json(job_dir / STT_RECOVERY_CANDIDATES_FILE)
    candidates = candidates_payload.get("candidates") if isinstance(candidates_payload, dict) else []
    if not isinstance(candidates, list) or not candidates:
        raise WardError("no STT recovery candidates found to confirm")

    requested_ids = {item.strip() for item in (candidate_ids or []) if item.strip()}
    if not include_all and not requested_ids:
        raise WardError("pass include_all=True or provide candidate_ids")

    selected = [
        candidate
        for candidate in candidates
        if include_all or str(candidate.get("candidate_id") or "") in requested_ids
    ]
    if not selected:
        raise WardError("no matching STT recovery candidates found")

    existing = _read_optional_json(job_dir / CONFIRMED_STT_RECOVERY_FILE)
    if not isinstance(existing, list):
        existing = []
    merged_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in existing
        if item.get("candidate_id")
    }
    additions = [_confirmed_stt_recovery_entry(candidate, confirmed_at) for candidate in selected]
    for item in additions:
        merged_by_id[item["candidate_id"]] = item
    confirmed = list(merged_by_id.values())
    (job_dir / CONFIRMED_STT_RECOVERY_FILE).write_text(
        json.dumps(confirmed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    state.setdefault("artifacts", {})["confirmed_stt_recovery"] = CONFIRMED_STT_RECOVERY_FILE
    state["confirmed_stt_recovery"] = {
        "count": len(confirmed),
        "updated_at": confirmed_at,
    }
    state["last_error"] = None
    write_state(config, state)

    return {
        "ok": True,
        "action": "confirm-stt-recovery",
        "job_id": job_id,
        "confirmed_stt_recovery_path": str(job_dir / CONFIRMED_STT_RECOVERY_FILE),
        "confirmed_count": len(confirmed),
        "added_count": len(additions),
        "confirmed_candidate_ids": [item["candidate_id"] for item in additions],
    }


def auto_confirm_stt_recovery_candidates(config: WardConfig, job_id: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    confirmed_at = _iso_now(config)
    candidates_payload = _read_optional_json(job_dir / STT_RECOVERY_CANDIDATES_FILE)
    candidates = candidates_payload.get("candidates") if isinstance(candidates_payload, dict) else []
    if not isinstance(candidates, list) or not candidates:
        return {
            "ok": True,
            "action": "auto-confirm-stt-recovery",
            "job_id": job_id,
            "status": "skipped",
            "message": "no STT recovery candidates found",
            "auto_confirmed_count": 0,
            "auto_confirmed_candidate_ids": [],
        }

    existing = _read_optional_json(job_dir / CONFIRMED_STT_RECOVERY_FILE)
    if not isinstance(existing, list):
        existing = []
    merged_by_id = {
        str(item.get("candidate_id") or ""): item
        for item in existing
        if isinstance(item, dict) and item.get("candidate_id")
    }

    additions = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        if not candidate_id or candidate_id in merged_by_id:
            continue
        reason = _auto_stt_recovery_reason(candidate)
        if not reason:
            continue
        additions.append(
            _confirmed_stt_recovery_entry(
                candidate,
                confirmed_at,
                confidence="auto_confirmed_stt_recovery",
                confirmation_method="auto_quality_gate",
                auto_confirm_reason=reason,
            )
        )

    if not additions:
        return {
            "ok": True,
            "action": "auto-confirm-stt-recovery",
            "job_id": job_id,
            "status": "skipped",
            "message": "no candidates passed the auto-confirm quality gate",
            "auto_confirmed_count": 0,
            "auto_confirmed_candidate_ids": [],
        }

    for item in additions:
        merged_by_id[item["candidate_id"]] = item
    confirmed = list(merged_by_id.values())
    (job_dir / CONFIRMED_STT_RECOVERY_FILE).write_text(
        json.dumps(confirmed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    state.setdefault("artifacts", {})["confirmed_stt_recovery"] = CONFIRMED_STT_RECOVERY_FILE
    state["confirmed_stt_recovery"] = {
        "count": len(confirmed),
        "updated_at": confirmed_at,
        "auto_confirmed_count": sum(
            1 for item in confirmed if isinstance(item, dict) and item.get("confirmation_method") == "auto_quality_gate"
        ),
    }
    state["last_error"] = None
    write_state(config, state)

    return {
        "ok": True,
        "action": "auto-confirm-stt-recovery",
        "job_id": job_id,
        "status": "ok",
        "confirmed_stt_recovery_path": str(job_dir / CONFIRMED_STT_RECOVERY_FILE),
        "confirmed_count": len(confirmed),
        "auto_confirmed_count": len(additions),
        "auto_confirmed_candidate_ids": [item["candidate_id"] for item in additions],
    }


def accept_latest_review(
    config: WardConfig,
    *,
    job_id: str = "latest",
    redraft: bool = True,
    allow_external_llm: bool = True,
    model: str | None = None,
    provider: str | None = None,
    deidentify: bool = False,
    deliver_target: str | None = None,
    reuse_delivery_target: bool = True,
    export_obsidian_note: bool = True,
    obsidian_vault_dir: Path | None = None,
    include_stt_recovery: bool = False,
    stt_recovery_candidate_ids: list[str] | None = None,
) -> dict:
    """Mobile-friendly shortcut for accepting all review terms on the latest job."""
    resolved_job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, resolved_job_id)
    actions: list[dict] = []

    try:
        confirm_result = confirm_terms(config, resolved_job_id, all_uncertain=True)
    except WardError as exc:
        if "no uncertain terms found to confirm" not in str(exc):
            raise
        confirm_result = {
            "ok": True,
            "action": "confirm-terms",
            "job_id": resolved_job_id,
            "status": "skipped",
            "message": "no uncertain terms found to confirm",
            "confirmed_count": 0,
            "added_count": 0,
        }
    actions.append(confirm_result)

    stt_recovery_result = None
    if include_stt_recovery or stt_recovery_candidate_ids:
        stt_recovery_result = confirm_stt_recovery(
            config,
            resolved_job_id,
            include_all=include_stt_recovery,
            candidate_ids=stt_recovery_candidate_ids,
        )
        actions.append(stt_recovery_result)

    chosen_target = deliver_target
    if not chosen_target and reuse_delivery_target:
        chosen_target = _previous_delivery_target(job_dir)

    redraft_result = None
    if redraft:
        redraft_result = run(
            config,
            resolved_job_id,
            allow_external_llm=allow_external_llm,
            model=model,
            provider=provider,
            deidentify=deidentify,
            deliver_target=chosen_target,
            export_obsidian_note=export_obsidian_note,
            obsidian_vault_dir=obsidian_vault_dir,
        )
        actions.append(redraft_result)

    return {
        "ok": all(bool(action.get("ok")) for action in actions),
        "action": "accept-latest",
        "job_id": resolved_job_id,
        "confirmed": confirm_result,
        "stt_recovery": stt_recovery_result,
        "redraft": redraft_result,
        "delivery_target": chosen_target,
        "export_obsidian": export_obsidian_note if redraft else False,
        "actions": actions,
    }


def normalize_llm(
    config: WardConfig,
    job_id: str,
    *,
    allow_external_llm: bool = False,
    model: str | None = None,
    provider: str | None = None,
) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)

    result = run_llm_normalization(
        config,
        job_id,
        job_dir=job_dir,
        state=state,
        allow_external_llm=allow_external_llm,
        model=model,
        provider=provider,
    )

    state = read_state(config, job_id)
    state.setdefault("steps", {})["llm_normalize"] = result["status"]
    state.setdefault("artifacts", {})["llm_normalization"] = LLM_NORMALIZATION_FILE
    state["artifacts"]["llm_normalization_audit"] = LLM_NORMALIZATION_AUDIT_FILE
    if result["status"] != "blocked":
        state["artifacts"]["llm_normalized_transcript"] = LLM_NORMALIZED_TRANSCRIPT_FILE
    state["llm_normalization"] = {
        "status": result["status"],
        "message": result.get("message"),
    }
    state.setdefault("policy", {})
    state["policy"]["external_llm_allowed"] = bool(allow_external_llm)
    state["policy"]["requires_local_only"] = not allow_external_llm
    state["current_step"] = "llm_normalize"
    if result["status"] == "blocked":
        state["status"] = "needs_review"
        state["last_error"] = result.get("message")
    else:
        state["status"] = "needs_review"
        state["last_error"] = None
        if "llm_normalization_requires_review" not in state["review_reasons"]:
            state["review_reasons"].append("llm_normalization_requires_review")
    write_state(config, state)

    case_view = None
    try:
        case_view = build_case_view(config, job_id)
    except Exception as exc:
        case_view = {"ok": False, "error": str(exc)}

    return {
        "ok": result["ok"],
        "action": "normalize-llm",
        "job_id": job_id,
        "status": result["status"],
        "message": result.get("message"),
        "artifacts": result.get("artifacts", {}),
        "case_view": case_view,
    }


def extract_facts(config: WardConfig, job_id: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)

    result = extract_clinical_facts(config, job_id, job_dir=job_dir, state=state)

    state = read_state(config, job_id)
    state.setdefault("steps", {})["clinical_extract"] = result["status"]
    state.setdefault("artifacts", {})["clinical_facts"] = CLINICAL_FACTS_FILE
    state["artifacts"]["clinical_facts_audit"] = CLINICAL_FACTS_AUDIT_FILE
    state["clinical_facts"] = {
        "status": result["status"],
        "message": result.get("message"),
    }
    state["current_step"] = "clinical_extract"
    state["status"] = "needs_review"
    state["last_error"] = result.get("message") if result["status"] == "blocked" else None
    write_state(config, state)

    return {
        "ok": result["ok"],
        "action": "extract-facts",
        "job_id": job_id,
        "status": result["status"],
        "message": result.get("message"),
        "artifacts": result.get("artifacts", {}),
    }


def draft_soap(config: WardConfig, job_id: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)

    result = draft_soap_note(config, job_id, job_dir=job_dir, state=state)

    state = read_state(config, job_id)
    state.setdefault("steps", {})["soap_generate"] = result["status"]
    state.setdefault("artifacts", {})["soap_note"] = SOAP_NOTE_FILE
    state["artifacts"]["soap_note_json"] = SOAP_NOTE_JSON_FILE
    state["artifacts"]["soap_audit"] = SOAP_AUDIT_FILE
    state["soap"] = {
        "status": result["status"],
        "message": result.get("message"),
    }
    state["current_step"] = "soap_generate"
    state["status"] = "needs_review"
    state["last_error"] = result.get("message") if result["status"] == "blocked" else None
    literature = _update_literature_plan_artifact(job_dir, job_id, state)
    write_state(config, state)

    response = {
        "ok": result["ok"],
        "action": "draft-soap",
        "job_id": job_id,
        "status": result["status"],
        "message": result.get("message"),
        "artifacts": result.get("artifacts", {}),
    }
    if literature is not None:
        response["literature_plan"] = {
            "path": str(job_dir / LITERATURE_PLAN_FILE),
            "source_type": literature.get("source_type"),
            "clinical_classification": literature.get("clinical_classification"),
            "search_targets": literature.get("search_targets", []),
        }
    return response


def validate_soap(config: WardConfig, job_id: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)

    result = validate_soap_note(config, job_id, job_dir=job_dir, state=state)

    state = read_state(config, job_id)
    state.setdefault("steps", {})["soap_validate"] = result["status"]
    state.setdefault("artifacts", {})["soap_validation"] = SOAP_VALIDATION_FILE
    state["soap_validation"] = {
        "status": result["status"],
        "message": result.get("message"),
    }
    state["current_step"] = "soap_validate"
    if result["status"] == "auto_finalized":
        state["status"] = "auto_finalized"
        state["needs_human_review"] = False
        state["last_error"] = None
    else:
        state["status"] = "needs_review"
        state["needs_human_review"] = True
        state["last_error"] = result.get("message")
    write_state(config, state)

    return {
        "ok": result["ok"],
        "action": "validate-soap",
        "job_id": job_id,
        "status": result["status"],
        "message": result.get("message"),
        "artifacts": result.get("artifacts", {}),
    }


def auto_soap(
    config: WardConfig,
    job_id: str,
    *,
    allow_external_llm: bool = False,
    model: str | None = None,
    provider: str | None = None,
    deliver_target: str | None = None,
    export_obsidian_note: bool = False,
    obsidian_vault_dir: Path | None = None,
    validate_after_output: bool = True,
) -> dict:
    job_id = resolve_job_id(config, job_id)
    actions = [
        normalize_llm(config, job_id, allow_external_llm=allow_external_llm, model=model, provider=provider),
        extract_facts(config, job_id),
        draft_soap(config, job_id),
    ]
    obsidian_result = None
    if export_obsidian_note:
        try:
            obsidian_result = export_obsidian(config, job_id, vault_dir=obsidian_vault_dir)
        except Exception as exc:
            obsidian_result = {
                "ok": False,
                "action": "export-obsidian",
                "job_id": job_id,
                "error": str(exc),
            }
        actions.append(obsidian_result)

    delivery_result = None
    if deliver_target:
        try:
            delivery_result = deliver(config, job_id, deliver_target)
        except Exception as exc:
            delivery_result = {
                "ok": False,
                "action": "deliver",
                "job_id": job_id,
                "target": deliver_target,
                "error": str(exc),
            }
        actions.append(delivery_result)

    validation_result = None
    if validate_after_output:
        validation_result = validate_soap(config, job_id)
        actions.append(validation_result)
    state = read_state(config, job_id)
    required_actions = actions[:3]
    if obsidian_result is not None:
        required_actions.append(obsidian_result)
    if delivery_result is not None:
        required_actions.append(delivery_result)
    foreground_ok = all(bool(action.get("ok")) for action in required_actions)
    report = {
        "ok": foreground_ok,
        "action": "auto-soap",
        "job_id": job_id,
        "status": "output_ready" if foreground_ok else "output_failed",
        "message": "SOAP draft output completed; validation recorded separately" if foreground_ok else "SOAP draft output failed",
        "actions": actions,
        "artifacts": {
            "llm_normalization": state.get("artifacts", {}).get("llm_normalization"),
            "clinical_facts": state.get("artifacts", {}).get("clinical_facts"),
            "soap_note": state.get("artifacts", {}).get("soap_note"),
            "soap_validation": state.get("artifacts", {}).get("soap_validation"),
        },
    }
    if validation_result is not None:
        report["validation"] = {
            "ok": bool(validation_result.get("ok")),
            "status": validation_result.get("status"),
            "message": validation_result.get("message"),
        }
    else:
        report["validation"] = {
            "ok": None,
            "status": "skipped",
            "message": "validation skipped for immediate output path",
        }
    if obsidian_result is not None:
        report["obsidian_export"] = obsidian_result
    if delivery_result is not None:
        report["delivery"] = delivery_result
    _json_write(_job_dir(config, job_id) / WORKFLOW_REPORT_FILE, report)
    return report


def transcribe(config: WardConfig, job_id: str, model: str | None = None) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    audio_path = Path(state["input"]["original_path"])

    try:
        stt_rule_sync = _sync_stt_rules(config, job_id)
        state.setdefault("artifacts", {})["stt_rule_sync"] = STT_RULE_SYNC_FILE
        write_state(config, state)
    except WardError as exc:
        state["status"] = "failed"
        state["current_step"] = "stt_rule_sync"
        state.setdefault("artifacts", {})["stt_rule_sync"] = STT_RULE_SYNC_FILE
        state["last_error"] = str(exc)
        write_state(config, state)
        raise

    if not WARD_STT_PYTHON.exists():
        raise WardError(f"Ward STT Python not found: {WARD_STT_PYTHON}")

    command = [
        str(WARD_STT_PYTHON),
        "-m",
        "ward_pipeline.stt_whisperx",
        str(audio_path),
        "--output-dir",
        str(job_dir),
    ]
    if model:
        command.extend(["--model", model])

    env = os.environ.copy()
    env["PYTHONPATH"] = str(WARD_RUNTIME_ROOT)
    env["PATH"] = os.pathsep.join([str(WARD_STT_PYTHON.parent), env.get("PATH", "")])
    # Use already-cached HF/Transformers models by default. Online metadata
    # checks can add minutes to every fresh STT subprocess on this machine.
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=1800,
        cwd=str(WARD_RUNTIME_ROOT),
        env=env,
    )
    raw_output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        state["status"] = "failed"
        state["current_step"] = "transcribe"
        state["last_error"] = raw_output or f"transcription exited with code {completed.returncode}"
        write_state(config, state)
        raise WardError(state["last_error"])

    result = None
    json_error: json.JSONDecodeError | None = None
    for line in reversed(raw_output.splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            result = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            json_error = exc
    if result is None:
        state["status"] = "failed"
        state["current_step"] = "transcribe"
        state["last_error"] = f"could not parse transcription output: {json_error}"
        write_state(config, state)
        raise WardError(state["last_error"])

    _json_write(job_dir / TRANSCRIPTION_META_FILE, result)
    if not result.get("success"):
        state["status"] = "failed"
        state["current_step"] = "transcribe"
        state["artifacts"]["transcription_meta"] = TRANSCRIPTION_META_FILE
        state["last_error"] = result.get("error") or "transcription failed"
        write_state(config, state)
        raise WardError(state["last_error"])

    transcript_text = result.get("transcript", "")
    raw_destination = job_dir / RAW_TRANSCRIPT_FILE
    raw_destination.write_text(transcript_text, encoding="utf-8")

    normalized = normalize_transcript(transcript_text)
    normalized_text = normalized["normalized_transcript"]
    normalized_destination = job_dir / NORMALIZED_TRANSCRIPT_FILE
    correction_log_path = job_dir / CORRECTION_LOG_FILE
    uncertain_terms_path = job_dir / UNCERTAIN_TERMS_FILE
    normalized_destination.write_text(normalized_text, encoding="utf-8")
    correction_log_path.write_text(dumps_json(normalized["correction_log"]), encoding="utf-8")
    uncertain_terms_path.write_text(dumps_json(normalized["uncertain_terms"]), encoding="utf-8")

    destination = job_dir / TRANSCRIPT_FILE
    destination.write_text(normalized_text, encoding="utf-8")

    state["artifacts"]["transcript_auto"] = TRANSCRIPT_FILE
    state["artifacts"]["raw_transcript"] = RAW_TRANSCRIPT_FILE
    state["artifacts"]["normalized_transcript"] = NORMALIZED_TRANSCRIPT_FILE
    state["artifacts"]["correction_log"] = CORRECTION_LOG_FILE
    state["artifacts"]["uncertain_terms"] = UNCERTAIN_TERMS_FILE
    state["normalization"] = normalized["summary"]
    state["artifacts"]["transcription_meta"] = TRANSCRIPTION_META_FILE
    for key, filename in (result.get("artifacts") or {}).items():
        state["artifacts"][key] = filename
    if (job_dir / DIARIZATION_RTTM_FILE).exists():
        state["artifacts"]["diarization_rttm"] = DIARIZATION_RTTM_FILE
    if (job_dir / DIARIZATION_SEGMENTS_FILE).exists():
        state["artifacts"]["diarization_segments"] = DIARIZATION_SEGMENTS_FILE
    if (job_dir / SEGMENTS_WHISPERX_FILE).exists():
        state["artifacts"]["segments_whisperx"] = SEGMENTS_WHISPERX_FILE
    if (job_dir / SPEAKER_TRANSCRIPT_FILE).exists():
        state["artifacts"]["transcript_speaker"] = SPEAKER_TRANSCRIPT_FILE
    if (job_dir / DIARIZATION_RENDER_FILE).exists():
        state["artifacts"]["diarization_render"] = DIARIZATION_RENDER_FILE
    review_result = None
    try:
        review_result = stt_review(config, job_id)
        state["artifacts"]["stt_review_candidates"] = STT_REVIEW_CANDIDATES_FILE
    except Exception as exc:
        review_result = {"ok": False, "error": str(exc)}
    state["steps"]["normalize"] = "done"
    state["steps"]["transcribe"] = "done"
    state["steps"]["diarize"] = "done"
    state["current_step"] = "transcribe"
    state["status"] = "needs_review"
    state["needs_human_review"] = True
    if "automatic_transcript_requires_review" not in state["review_reasons"]:
        state["review_reasons"].append("automatic_transcript_requires_review")
    state["last_error"] = None
    write_state(config, state)
    auto_stt_recovery_result = auto_confirm_stt_recovery_candidates(config, job_id)
    case_view = None
    try:
        case_view = build_case_view(config, job_id)
    except Exception as exc:
        case_view = {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "action": "transcribe",
        "job_id": job_id,
        "transcript_path": str(destination),
        "provider": result.get("provider"),
        "diarization": result.get("diarization"),
        "language": result.get("language"),
        "duration": result.get("duration"),
        "size_bytes": destination.stat().st_size,
        "case_view": case_view,
        "stt_review": review_result,
        "auto_stt_recovery": auto_stt_recovery_result,
        "stt_rule_sync": stt_rule_sync,
    }


def import_result(config: WardConfig, job_id: str, result_path: Path) -> dict:
    job_id = resolve_job_id(config, job_id)
    source = result_path.expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise WardError(f"result file does not exist: {source}")

    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    destination = job_dir / IMPORTED_RESULT_FILE
    destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    state["artifacts"]["imported_result"] = IMPORTED_RESULT_FILE
    state["status"] = "needs_review"
    state["current_step"] = "manual_review"
    state["needs_human_review"] = True
    if "imported_chatgpt_result_requires_review" not in state["review_reasons"]:
        state["review_reasons"].append("imported_chatgpt_result_requires_review")
    write_state(config, state)

    return {
        "ok": True,
        "action": "import-result",
        "job_id": job_id,
        "result_path": str(destination),
    }


def literature_plan(config: WardConfig, job_id: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    source_text, source_type = _literature_source_text(job_dir)
    if not source_text.strip():
        raise WardError("missing SOAP or transcript; run transcribe, attach-transcript, or draft SOAP before literature-plan")
    plan = plan_literature_queries(source_text, source_type=source_type or "unknown")
    plan["job_id"] = job_id
    plan_path = job_dir / LITERATURE_PLAN_FILE
    _json_write(plan_path, plan)
    taxonomy_refresh = _refresh_literature_taxonomy_queue(job_dir, job_id)

    state["artifacts"]["literature_query_plan"] = LITERATURE_PLAN_FILE
    state["steps"]["literature"] = "planned" if plan["search_targets"] else "needs_clinical_question"
    state["last_error"] = None
    write_state(config, state)

    response = {
        "ok": True,
        "action": "literature-plan",
        "job_id": job_id,
        "plan_path": str(plan_path),
        "plan": plan,
    }
    if taxonomy_refresh is not None:
        response["taxonomy_refresh"] = taxonomy_refresh
        response["taxonomy_candidates_path"] = str(LITERATURE_TAXONOMY_CANDIDATES_FILE)
    return response


def openevidence_login(config: WardConfig, *, timeout: int = 600) -> dict:
    _ = config
    return _openevidence_login(timeout=timeout)


def literature_enrich(
    config: WardConfig,
    job_id: str,
    *,
    max_queries: int = 4,
    results_per_query: int = 3,
    timeout: int = 20,
) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    plan_path = job_dir / LITERATURE_PLAN_FILE
    llm_plan_path = job_dir / LITERATURE_QUESTION_PLAN_FILE
    plan_source = "taxonomy"
    fallback_reason = None
    if llm_plan_path.exists():
        try:
            plan = validate_literature_question_plan(_json_read(llm_plan_path))
            plan_source = "llm"
            plan_path = llm_plan_path
        except Exception as exc:
            fallback_reason = f"llm_plan_invalid: {exc}"
            if plan_path.exists():
                plan = _json_read(plan_path)
            else:
                plan_result = literature_plan(config, job_id)
                plan = plan_result["plan"]
    elif plan_path.exists():
        plan = _json_read(plan_path)
        fallback_reason = "llm_plan_missing"
    else:
        plan_result = literature_plan(config, job_id)
        plan = plan_result["plan"]
        fallback_reason = "llm_plan_missing"

    if not isinstance(plan, dict) or not plan.get("search_targets"):
        state.setdefault("steps", {})["literature"] = "needs_clinical_question"
        state["last_error"] = "missing literature search targets"
        write_state(config, state)
        return {
            "ok": False,
            "action": "literature-enrich",
            "job_id": job_id,
            "status": "needs_clinical_question",
            "message": state["last_error"],
            "plan_path": str(plan_path),
        }

    sources_payload = retrieve_literature_sources(
        plan,
        max_queries=max_queries,
        results_per_query=results_per_query,
        timeout=timeout,
    )
    summary_payload = summarize_literature_sources(plan, sources_payload)
    sources_path = job_dir / LITERATURE_SOURCES_FILE
    summary_path = job_dir / LITERATURE_SUMMARY_FILE
    _json_write(sources_path, sources_payload)
    _json_write(summary_path, summary_payload)
    narrative_payload = sources_payload.get("openevidence_narrative") if isinstance(sources_payload, dict) else {}
    narrative_path: Path | None = None
    if isinstance(narrative_payload, dict) and narrative_payload.get("ok") and str(narrative_payload.get("text") or "").strip():
        narrative_path = job_dir / LITERATURE_NARRATIVE_FILE
        narrative_text = str(narrative_payload.get("text") or "").strip()
        narrative_query = str(narrative_payload.get("query") or "").strip()
        content = ["# OpenEvidence Narrative", ""]
        if narrative_query:
            content.extend([f"- Query: `{narrative_query}`", ""])
        content.append(narrative_text)
        narrative_path.write_text("\n".join(content) + "\n", encoding="utf-8")

    state.setdefault("artifacts", {})["literature_query_plan"] = LITERATURE_PLAN_FILE
    if plan_source == "llm":
        state["artifacts"]["literature_question_plan"] = LITERATURE_QUESTION_PLAN_FILE
    state["artifacts"]["literature_sources"] = LITERATURE_SOURCES_FILE
    state["artifacts"]["literature_summary"] = LITERATURE_SUMMARY_FILE
    if narrative_path is not None:
        state["artifacts"]["openevidence_narrative"] = LITERATURE_NARRATIVE_FILE
    state.setdefault("steps", {})["literature"] = "summarized" if summary_payload.get("source_count") else "retrieval_empty"
    state["last_error"] = None if sources_payload.get("ok") else "literature retrieval had no usable sources"
    write_state(config, state)

    return {
        "ok": bool(sources_payload.get("ok")),
        "action": "literature-enrich",
        "job_id": job_id,
        "status": state["steps"]["literature"],
        "plan_path": str(plan_path),
        "plan_source": plan_source,
        "fallback_reason": fallback_reason,
        "sources_path": str(sources_path),
        "summary_path": str(summary_path),
        "narrative_path": str(narrative_path) if narrative_path is not None else None,
        "source_count": sources_payload.get("source_count", 0),
        "errors": sources_payload.get("errors", []),
        "summary": summary_payload,
    }


def run(
    config: WardConfig,
    job_id: str,
    *,
    transcript_path: Path | None = None,
    allow_external_llm: bool = False,
    model: str | None = None,
    provider: str | None = None,
    deidentify: bool = False,
    deliver_target: str | None = None,
    export_obsidian_note: bool = False,
    obsidian_vault_dir: Path | None = None,
) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    actions: list[dict] = []

    if transcript_path is not None:
        actions.append(attach_transcript(config, job_id, transcript_path))
        state = read_state(config, job_id)

    transcript_file = job_dir / TRANSCRIPT_FILE
    if not transcript_file.exists():
        state["status"] = "needs_transcript"
        state["current_step"] = "transcribe"
        state["last_error"] = "missing transcript; attach one with ward run JOB --transcript PATH"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "blocked_at": "transcribe",
            "message": state["last_error"],
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report

    transcript = transcript_file.read_text(encoding="utf-8")
    if not transcript.strip():
        state["status"] = "needs_transcript"
        state["current_step"] = "transcribe"
        state["last_error"] = "transcript is empty; provide clearer audio or attach a transcript"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "blocked_at": "transcribe",
            "message": state["last_error"],
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report

    plan = plan_literature_queries(transcript)
    plan["job_id"] = job_id
    _json_write(job_dir / LITERATURE_PLAN_FILE, plan)

    transcript_context = _transcript_context(job_dir, transcript)
    prompt = _clinical_prompt(job_id, state, transcript, "Codex autopilot", deidentify, plan, transcript_context)
    prompt_path = job_dir / PROMPT_PACKAGE_FILE
    prompt_path.write_text(prompt, encoding="utf-8")
    actions.append({"ok": True, "action": "export-prompt", "prompt_path": str(prompt_path)})

    state["artifacts"]["prompt_package"] = PROMPT_PACKAGE_FILE
    state["artifacts"]["literature_query_plan"] = LITERATURE_PLAN_FILE
    state["steps"]["clinical_extract"] = "pending"
    state["steps"]["soap_generate"] = "pending"
    state["steps"]["literature"] = "planned" if plan.get("search_targets") else "needs_clinical_question"
    state["last_error"] = None

    if not allow_external_llm:
        state["status"] = "blocked_external_llm_policy"
        state["current_step"] = "clinical_extract"
        state["policy"]["external_llm_allowed"] = False
        state["policy"]["requires_local_only"] = True
        state["last_error"] = "external LLM execution not allowed; rerun with --allow-external-llm after confirming policy"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "blocked_at": "clinical_extract",
            "message": state["last_error"],
            "prompt_path": str(prompt_path),
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report

    if provider and provider != "openai-codex":
        state["status"] = "failed"
        state["current_step"] = "clinical_extract"
        state["last_error"] = f"Codex exec runner does not support provider override: {provider}"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "message": state["last_error"],
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report

    try:
        completed, result_text = run_codex_exec(
            prompt,
            config=config,
            cwd=job_dir,
            output_dir=job_dir,
            model=model,
            timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        state["status"] = "failed"
        state["current_step"] = "clinical_extract"
        state["last_error"] = f"Codex exec timed out after {exc.timeout} seconds"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "message": state["last_error"],
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report
    except Exception as exc:
        state["status"] = "failed"
        state["current_step"] = "clinical_extract"
        state["last_error"] = f"Codex exec failed: {exc}"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "message": state["last_error"],
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report

    diagnostic_text = (completed.stdout or completed.stderr).strip()
    lower_diagnostic = diagnostic_text.lower()
    codex_reported_error = (
        lower_diagnostic.startswith("api call failed")
        or "connection error" in lower_diagnostic
        or "authentication error" in lower_diagnostic
        or "rate limit" in lower_diagnostic
    )
    if completed.returncode != 0 or codex_reported_error:
        state["status"] = "failed"
        state["current_step"] = "clinical_extract"
        state["last_error"] = diagnostic_text or f"Codex exec exited with code {completed.returncode}"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "exit_code": completed.returncode,
            "message": state["last_error"],
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report

    if not result_text:
        state["status"] = "failed"
        state["current_step"] = "clinical_extract"
        state["last_error"] = "Codex exec exited with code 0 but produced an empty SOAP draft"
        write_state(config, state)
        report = {
            "ok": False,
            "action": "run",
            "job_id": job_id,
            "status": state["status"],
            "exit_code": completed.returncode,
            "message": state["last_error"],
            "llm_runner": "codex_exec",
            "stdout_bytes": len(completed.stdout or ""),
            "stderr_bytes": len(completed.stderr or ""),
            "actions": actions,
        }
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)
        return report

    result_path = job_dir / HERMES_RESULT_FILE
    result_path.write_text(result_text + "\n", encoding="utf-8")

    state["artifacts"]["hermes_result"] = HERMES_RESULT_FILE
    state["artifacts"]["llm_runner"] = "codex_exec"
    state["artifacts"]["soap_draft"] = HERMES_RESULT_FILE
    state["steps"]["clinical_extract"] = "done"
    state["steps"]["soap_generate"] = "done"
    state["status"] = "needs_review"
    state["current_step"] = "manual_review"
    state["needs_human_review"] = True
    state["policy"]["external_llm_allowed"] = True
    state["policy"]["requires_local_only"] = False
    if "hermes_generated_result_requires_clinician_review" not in state["review_reasons"]:
        state["review_reasons"].append("hermes_generated_result_requires_clinician_review")
    state["last_error"] = None
    refreshed_literature_plan = _update_literature_plan_artifact(job_dir, job_id, state)
    write_state(config, state)
    auto_literature = None
    should_enrich, enrich_reason = _should_auto_literature_enrich(result_text, refreshed_literature_plan)
    try:
        auto_literature = literature_enrich(config, job_id, max_queries=4, results_per_query=3, timeout=20)
    except Exception as exc:
        auto_literature = {
            "ok": False,
            "action": "literature-enrich",
            "job_id": job_id,
            "status": "failed",
            "message": str(exc),
        }
    if isinstance(auto_literature, dict) and not should_enrich:
        auto_literature["auto_trigger_warning"] = enrich_reason
        auto_literature["auto_trigger_threshold"] = AUTO_LITERATURE_MIN_CONFIDENCE

    report = {
        "ok": True,
        "action": "run",
        "job_id": job_id,
        "status": state["status"],
        "current_step": state["current_step"],
        "prompt_path": str(prompt_path),
        "result_path": str(result_path),
        "actions": actions,
    }
    if refreshed_literature_plan is not None:
        report["literature_plan"] = {
            "path": str(job_dir / LITERATURE_PLAN_FILE),
            "source_type": refreshed_literature_plan.get("source_type"),
            "clinical_classification": refreshed_literature_plan.get("clinical_classification"),
            "search_targets": refreshed_literature_plan.get("search_targets", []),
        }
    report["literature_auto_enrich"] = auto_literature
    _json_write(job_dir / WORKFLOW_REPORT_FILE, report)

    if export_obsidian_note:
        try:
            obsidian_result = export_obsidian(config, job_id, vault_dir=obsidian_vault_dir)
        except Exception as exc:
            obsidian_result = {
                "ok": False,
                "action": "export-obsidian",
                "job_id": job_id,
                "error": str(exc),
            }
        report["obsidian_export"] = obsidian_result
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)

    if deliver_target:
        delivery_result = deliver(config, job_id, deliver_target)
        report["delivery"] = delivery_result
        _json_write(job_dir / WORKFLOW_REPORT_FILE, report)

    return report


def process_audio(
    config: WardConfig,
    audio_path: Path,
    *,
    allow_external_llm: bool = False,
    stt_model: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    deidentify: bool = False,
    deliver_target: str | None = None,
    export_obsidian_note: bool = False,
    obsidian_vault_dir: Path | None = None,
) -> dict:
    created = ingest(config, audio_path)
    job_id = created["job_id"]
    actions = [created]

    try:
        actions.append(transcribe(config, job_id, model=stt_model))
    except WardError as exc:
        report = {
            "ok": False,
            "action": "process",
            "job_id": job_id,
            "status": "failed",
            "blocked_at": "transcribe",
            "message": str(exc),
            "actions": actions,
        }
        _json_write(_job_dir(config, job_id) / WORKFLOW_REPORT_FILE, report)
        return report

    result = run(
        config,
        job_id,
        allow_external_llm=allow_external_llm,
        model=model,
        provider=provider,
        deidentify=deidentify,
        deliver_target=deliver_target,
        export_obsidian_note=export_obsidian_note,
        obsidian_vault_dir=obsidian_vault_dir,
    )
    result["action"] = "process"
    result["job_dir"] = created["job_dir"]
    result["actions"] = actions + result.get("actions", [])
    _json_write(_job_dir(config, job_id) / WORKFLOW_REPORT_FILE, result)
    return result


def deliver(config: WardConfig, job_id: str, target: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    parsed_target = _parse_delivery_target(target)
    messages = _delivery_messages(job_dir, job_id)
    intent = {
        "target": target,
        "parsed_target": parsed_target,
        "artifact_files": [
            name for name in (
                SOAP_DRAFT_FILE,
                HERMES_RESULT_FILE,
                CLINICAL_CHANGES_FILE,
                STT_RECOVERY_CANDIDATES_FILE,
                *ALERT_REPORT_FILES,
            )
            if (job_dir / name).exists()
        ],
        "updated_at": _iso_now(config),
    }
    _json_write(job_dir / DELIVERY_INTENT_FILE, intent)

    attempts = []
    sent_messages = []
    for index, message in enumerate(messages, start=1):
        delivered = False
        last_error = None
        for attempt_number in range(1, DELIVERY_RETRY_COUNT + 1):
            attempt = {
                "message_index": index,
                "attempt": attempt_number,
                "started_at": _iso_now(config),
            }
            try:
                result = _discord_post_message(parsed_target, message)
                attempt.update({"ok": True, "result": result, "finished_at": _iso_now(config)})
                attempts.append(attempt)
                sent_messages.append(result)
                delivered = True
                break
            except WardError as exc:
                last_error = str(exc)
                attempt.update({"ok": False, "error": last_error, "finished_at": _iso_now(config)})
                attempts.append(attempt)
                if attempt_number < DELIVERY_RETRY_COUNT:
                    time.sleep(DELIVERY_RETRY_DELAY_SECONDS)

        if not delivered:
            notification = _notify_local_delivery_failure(job_id)
            report = {
                "ok": False,
                "action": "deliver",
                "job_id": job_id,
                "status": "report_failed",
                "target": target,
                "failed_message_index": index,
                "message": "SOAP 回傳失敗，請稍後重送",
                "error": last_error,
                "attempts": attempts,
                "notification": notification,
                "artifact_dir": str(job_dir),
            }
            _json_write(job_dir / DELIVERY_REPORT_FILE, report)
            state["artifacts"]["delivery_intent"] = DELIVERY_INTENT_FILE
            state["artifacts"]["delivery_report"] = DELIVERY_REPORT_FILE
            _record_delivery_state(
                config,
                state,
                status="report_failed",
                delivery_step="failed",
                last_error=last_error,
            )
            return report

    report = {
        "ok": True,
        "action": "deliver",
        "job_id": job_id,
        "status": "delivered",
        "target": target,
        "messages_sent": len(sent_messages),
        "sent_messages": sent_messages,
        "attempts": attempts,
        "artifact_dir": str(job_dir),
    }
    _json_write(job_dir / DELIVERY_REPORT_FILE, report)
    state["status"] = "delivered"
    state["current_step"] = "delivery"
    state["steps"]["delivery"] = "done"
    state["artifacts"]["delivery_intent"] = DELIVERY_INTENT_FILE
    state["artifacts"]["delivery_report"] = DELIVERY_REPORT_FILE
    state["needs_human_review"] = True
    if "delivered_soap_draft_requires_clinician_edit" not in state["review_reasons"]:
        state["review_reasons"].append("delivered_soap_draft_requires_clinician_edit")
    state["last_error"] = None
    write_state(config, state)
    return report


def resend(config: WardConfig, job_id: str, target: str | None = None) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    if target is None:
        intent_path = job_dir / DELIVERY_INTENT_FILE
        if not intent_path.exists():
            raise WardError("no previous delivery target found; pass --target discord:CHAT_ID[:THREAD_ID]")
        target = _json_read(intent_path)["target"]
    return deliver(config, job_id, target)


def resolve_report(config: WardConfig, job_id: str, reason: str) -> dict:
    job_id = resolve_job_id(config, job_id)
    job_dir = _job_dir(config, job_id)
    state = read_state(config, job_id)
    if state.get("status") != "report_failed" and state.get("steps", {}).get("delivery") != "failed":
        raise WardError("job is not in a failed delivery state")

    resolution = {
        "job_id": job_id,
        "resolved_at": _iso_now(config),
        "reason": reason,
        "previous_status": state.get("status"),
        "previous_step": state.get("current_step"),
        "previous_last_error": state.get("last_error"),
        "delivery_intent": state.get("artifacts", {}).get("delivery_intent"),
        "delivery_report": state.get("artifacts", {}).get("delivery_report"),
    }
    _json_write(job_dir / DELIVERY_RESOLUTION_FILE, resolution)

    state["status"] = "report_resolved"
    state["current_step"] = "delivery"
    state["steps"]["delivery"] = "resolved"
    state["needs_human_review"] = False
    state["last_error"] = None
    state["artifacts"]["delivery_resolution"] = DELIVERY_RESOLUTION_FILE
    write_state(config, state)

    return {
        "ok": True,
        "action": "resolve-report",
        "job_id": job_id,
        "status": state["status"],
        "current_step": state["current_step"],
        "resolution_path": str(job_dir / DELIVERY_RESOLUTION_FILE),
    }


def _health_result(ok: bool, message: str, **extra: object) -> dict:
    payload = {"ok": ok, "message": message}
    payload.update(extra)
    return payload


def _run_command_check(command: list[str], *, timeout: int = 30, cwd: str | None = None, env: dict[str, str] | None = None) -> dict:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        output = (completed.stdout or completed.stderr).strip()
        return _health_result(
            completed.returncode == 0,
            output[:1000] or f"exit_code={completed.returncode}",
            exit_code=completed.returncode,
        )
    except subprocess.TimeoutExpired as exc:
        return _health_result(False, f"timeout after {exc.timeout}s")
    except Exception as exc:
        return _health_result(False, str(exc))


def _check_discord_bot() -> dict:
    token = _discord_token()
    if not token:
        return _health_result(False, "DISCORD_BOT_TOKEN is not configured")
    try:
        result = _http_get_json(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bot {token}", "User-Agent": "ward-health/1.0"},
        )
        username = result["body"].get("username", "unknown")
        return _health_result(True, f"connected as {username}")
    except Exception as exc:
        return _health_result(False, str(exc))


def _check_network() -> dict:
    try:
        result = _http_get_json(
            "https://discord.com/api/v10/gateway",
            headers={"User-Agent": "ward-health/1.0"},
        )
        return _health_result(result["status"] == 200, f"discord gateway status {result['status']}")
    except Exception as exc:
        return _health_result(False, str(exc))


def _check_stt_environment() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WARD_RUNTIME_ROOT)
    env["PATH"] = os.pathsep.join([str(WARD_STT_PYTHON.parent), env.get("PATH", "")])
    code = (
        "import importlib.util, shutil; "
        "from ward_pipeline.stt_whisperx import transcribe_with_whisperx; "
        "print('ward_whisperx_stt_ok ffmpeg=' + str(bool(shutil.which('ffmpeg'))))"
    )
    return _run_command_check([str(WARD_STT_PYTHON), "-c", code], timeout=60, cwd=str(WARD_RUNTIME_ROOT), env=env)


def _check_whisper_modules() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WARD_RUNTIME_ROOT)
    env["PATH"] = os.pathsep.join([str(WARD_STT_PYTHON.parent), env.get("PATH", "")])
    code = (
        "import importlib.util, json; "
        "result = {"
        "'faster_whisper': bool(importlib.util.find_spec('faster_whisper')), "
        "'whisperx': bool(importlib.util.find_spec('whisperx')), "
        "'pyannote.audio': bool(importlib.util.find_spec('pyannote.audio')), "
        "'torch': bool(importlib.util.find_spec('torch'))"
        "}; "
        "print(json.dumps(result))"
    )
    result = _run_command_check([str(WARD_STT_PYTHON), "-c", code], timeout=60, cwd=str(WARD_RUNTIME_ROOT), env=env)
    try:
        modules = json.loads(result["message"])
    except Exception:
        modules = {}
    ok = bool(modules.get("faster_whisper") and modules.get("whisperx") and modules.get("pyannote.audio"))
    message = (
        f"faster-whisper={'ok' if modules.get('faster_whisper') else 'missing'}, "
        f"whisperx={'ok' if modules.get('whisperx') else 'missing'}, "
        f"pyannote.audio={'ok' if modules.get('pyannote.audio') else 'missing'}"
    )
    return _health_result(ok, message, modules=modules)


def _check_diarization() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WARD_RUNTIME_ROOT)
    env["PATH"] = os.pathsep.join([str(WARD_STT_PYTHON.parent), env.get("PATH", "")])
    code = """
import importlib.util
import json
import os

mods = {
    "pyannote.audio": bool(importlib.util.find_spec("pyannote.audio")),
    "whisperx": bool(importlib.util.find_spec("whisperx")),
}
token_value = os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
access = False
access_error = ""
if token_value and mods["pyannote.audio"] and mods["whisperx"]:
    try:
        from huggingface_hub import hf_hub_download

        hf_hub_download("pyannote/speaker-diarization-community-1", "config.yaml", token=token_value)
        access = True
    except Exception as exc:
        access_error = type(exc).__name__ + ": " + str(exc)
print(json.dumps({"modules": mods, "hf_token": bool(token_value), "model_access": access, "access_error": access_error}))
"""
    result = _run_command_check([str(WARD_STT_PYTHON), "-c", code], timeout=60, cwd=str(WARD_RUNTIME_ROOT), env=env)
    try:
        payload = json.loads(result["message"])
    except Exception:
        payload = {}
    modules = payload.get("modules", {})
    has_token = bool(payload.get("hf_token"))
    has_model_access = bool(payload.get("model_access"))
    ok = bool(modules.get("pyannote.audio") and modules.get("whisperx") and has_token and has_model_access)
    if not has_token:
        message = "HF_TOKEN missing; add a Hugging Face read token after accepting pyannote model terms"
    elif not has_model_access:
        message = (
            "HF_TOKEN present but pyannote/speaker-diarization-community-1 is not accessible; "
            "accept/request access on Hugging Face"
        )
    else:
        message = "WhisperX diarization configured"
    return _health_result(
        ok,
        message,
        modules=modules,
        hf_token_configured=has_token,
        model_access=has_model_access,
        access_error=payload.get("access_error") or None,
    )


def _check_identity_policy() -> dict:
    sample = "王小明 0912345678 A123456789 cough"
    sanitized = deidentify_text(sample)
    opt_in_available = (
        "王小明" not in sanitized
        and "0912345678" not in sanitized
        and "A123456789" not in sanitized
    )
    return _health_result(
        opt_in_available,
        "patient identifiers are preserved by default; de-identification remains opt-in",
        default_deidentify=False,
        opt_in_deidentify_available=opt_in_available,
    )


def _check_codex() -> dict:
    try:
        completed, message = run_codex_exec(
            "Reply exactly WARD_HEALTH_CODEX_OK",
            config=load_config(WARD_RUNTIME_ROOT),
            cwd=WARD_RUNTIME_ROOT,
            output_dir=WARD_RUNTIME_ROOT / "data" / "output" / "_health",
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        return _health_result(False, f"timeout after {exc.timeout}s")
    except Exception as exc:
        return _health_result(False, str(exc))
    diagnostic = (completed.stdout or completed.stderr).strip()
    ok = completed.returncode == 0 and message.strip() == "WARD_HEALTH_CODEX_OK"
    return _health_result(
        ok,
        message or diagnostic[:1000] or f"exit_code={completed.returncode}",
        exit_code=completed.returncode,
        llm_runner="codex_exec",
    )


def _check_pending_reports(config: WardConfig) -> dict:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    pending = []
    for state_file in sorted(config.output_dir.glob(f"*/{STATE_FILE}")):
        try:
            state = _json_read(state_file)
        except WardError:
            continue
        if state.get("status") == "report_failed" or state.get("steps", {}).get("delivery") == "failed":
            pending.append(state_file.parent.name)
    return _health_result(True, f"{len(pending)} pending report(s)", count=len(pending), jobs=pending)


def _check_disk_space(config: WardConfig) -> dict:
    usage = shutil.disk_usage(config.output_dir.parent if config.output_dir.exists() else Path.home())
    free_gb = usage.free / (1024 ** 3)
    ok = free_gb >= 20
    return _health_result(ok, f"{free_gb:.1f} GB free", free_gb=round(free_gb, 1))


def _check_ward_job(config: WardConfig) -> dict:
    test_dir = config.output_dir / "_health"
    test_dir.mkdir(parents=True, exist_ok=True)
    audio_path = test_dir / "health_audio.wav"
    audio_path.write_bytes(b"ward-health-audio-placeholder")
    try:
        created = ingest(config, audio_path)
        return _health_result(True, created["job_id"], job_id=created["job_id"], job_dir=created["job_dir"])
    except Exception as exc:
        return _health_result(False, str(exc))


def _check_artifact_rw(config: WardConfig) -> dict:
    test_dir = config.output_dir / "_health"
    try:
        test_dir.mkdir(parents=True, exist_ok=True)
        test_path = test_dir / "artifact_rw.txt"
        test_path.write_text("ward-health-ok\n", encoding="utf-8")
        ok = test_path.read_text(encoding="utf-8") == "ward-health-ok\n"
        return _health_result(ok, str(test_path))
    except Exception as exc:
        return _health_result(False, str(exc))


def _format_health_summary(report: dict, *, delivery_status_pending: bool = False) -> str:
    checks = report["checks"]

    def status(name: str) -> str:
        if name == "discord_delivery" and delivery_status_pending and name not in checks:
            return "測試中"
        return "正常" if checks.get(name, {}).get("ok") else "異常"

    pending_count = checks.get("pending_reports", {}).get("count", 0)
    disk_text = checks.get("disk_space", {}).get("message", "unknown")
    failed = [name for name, payload in checks.items() if not payload.get("ok")]
    lines = [
        f"今日 05:00 查房工作流自測結果：",
        f"Discord bot：{status('discord_bot')}",
        f"Hermes agent：{status('hermes_agent')}",
        f"ward CLI：{status('ward_cli')}",
        f"artifact 目錄：{status('artifact_rw')}",
        f"STT：{status('stt_environment')}",
        f"WhisperX / faster-whisper：{status('whisper_modules')}",
        f"diarization：{status('diarization')}",
        f"身份資訊保留策略：{status('identity_policy')}",
        f"Codex / OpenAI：{status('codex')}",
        f"Discord 回傳：{status('discord_delivery')}",
        f"pending report：{pending_count} 件",
        f"磁碟空間：{disk_text}",
    ]
    if failed:
        lines.append("總結：今日查房工作流有異常，請先處理以下項目")
        for name in failed:
            payload = checks[name]
            lines.append(f"- {name}: {payload.get('message')}")
    else:
        lines.append("總結：今日可正常查房使用")
    return "\n".join(lines)


def health(config: WardConfig, target: str | None = None) -> dict:
    health_dir = config.output_dir / "_health"
    health_dir.mkdir(parents=True, exist_ok=True)
    target = target or DEFAULT_HEALTH_DISCORD_TARGET

    checks = {
        "network": _check_network(),
        "discord_bot": _check_discord_bot(),
        "hermes_agent": _run_command_check([HERMES_BIN, "status"], timeout=30),
        "ward_cli": _run_command_check([WARD_BIN, "config"], timeout=30),
        "artifact_rw": _check_artifact_rw(config),
        "ward_test_job": _check_ward_job(config),
        "stt_environment": _check_stt_environment(),
        "whisper_modules": _check_whisper_modules(),
        "diarization": _check_diarization(),
        "identity_policy": _check_identity_policy(),
        "codex": _check_codex(),
        "pending_reports": _check_pending_reports(config),
        "disk_space": _check_disk_space(config),
    }

    report = {
        "ok": all(payload.get("ok") for payload in checks.values()),
        "action": "health",
        "created_at": _iso_now(config),
        "target": target,
        "checks": checks,
    }
    summary = _format_health_summary(report, delivery_status_pending=True)

    summary_path = health_dir / HEALTH_SUMMARY_FILE
    report_path = health_dir / HEALTH_REPORT_FILE
    summary_path.write_text(summary + "\n", encoding="utf-8")
    _json_write(report_path, report)

    delivery = None
    parsed_target = None
    delivered_message_id = None
    try:
        if not target:
            raise WardError("WARD_HEALTH_DISCORD_TARGET is not configured")
        parsed_target = _parse_delivery_target(target)
        attempts = []
        last_error = None
        delivered = False
        for attempt_number in range(1, DELIVERY_RETRY_COUNT + 1):
            attempt = {"attempt": attempt_number, "started_at": _iso_now(config)}
            try:
                send_result = _discord_post_message(parsed_target, summary)
                delivered_message_id = send_result.get("message_id")
                attempt.update({"ok": True, "result": send_result, "finished_at": _iso_now(config)})
                attempts.append(attempt)
                delivered = True
                break
            except WardError as exc:
                last_error = str(exc)
                attempt.update({"ok": False, "error": last_error, "finished_at": _iso_now(config)})
                attempts.append(attempt)
                if attempt_number < DELIVERY_RETRY_COUNT:
                    time.sleep(DELIVERY_RETRY_DELAY_SECONDS)
        delivery = {
            "ok": delivered,
            "target": target,
            "attempts": attempts,
            "error": last_error,
        }
    except WardError as exc:
        delivery = {"ok": False, "target": target, "error": str(exc), "attempts": []}

    report["checks"]["discord_delivery"] = _health_result(
        bool(delivery and delivery.get("ok")),
        "health report delivered" if delivery and delivery.get("ok") else (delivery or {}).get("error", "delivery failed"),
        delivery=delivery,
    )
    report["ok"] = all(payload.get("ok") for payload in report["checks"].values())
    summary = _format_health_summary(report)

    if delivery and delivery.get("ok") and parsed_target and delivered_message_id:
        try:
            edit_result = _discord_edit_message(parsed_target, delivered_message_id, summary)
            delivery["edit"] = {"ok": True, "result": edit_result}
        except WardError as exc:
            delivery["edit"] = {"ok": False, "error": str(exc)}

    summary_path.write_text(summary + "\n", encoding="utf-8")
    _json_write(report_path, report)

    return {
        "ok": report["ok"],
        "action": "health",
        "summary": summary,
        "report_path": str(report_path),
        "summary_path": str(summary_path),
        "delivery": delivery,
    }


def config_summary(config: WardConfig) -> dict:
    return {
        "ok": True,
        "action": "config",
        "config": {
            field.name: str(getattr(config, field.name))
            for field in dataclasses.fields(config)
        },
    }
