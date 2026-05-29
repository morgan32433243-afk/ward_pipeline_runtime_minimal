from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .case_view import build_case_view
from .config import WardConfig
from .encounter import append_ward_job, is_audio_file, route_audio_file
from .jobs import process_audio, read_state, write_state


WATCHER_STATE_FILE = "watcher_state.json"
DEFAULT_STABILITY_SECONDS = 8
DEFAULT_POLL_SECONDS = 5


def _state_path(config: WardConfig) -> Path:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    return config.log_dir / WATCHER_STATE_FILE


def _read_state(config: WardConfig) -> dict:
    path = _state_path(config)
    if not path.exists():
        return {"schema_version": "1.0", "processed": {}, "events": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_state(config: WardConfig, state: dict) -> None:
    _state_path(config).write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}::{stat.st_size}::{int(stat.st_mtime)}"


def _is_stable(path: Path, stable_seconds: int) -> bool:
    try:
        first = path.stat()
        time.sleep(stable_seconds)
        second = path.stat()
    except FileNotFoundError:
        return False
    return first.st_size == second.st_size and int(first.st_mtime) == int(second.st_mtime)


def _event(config: WardConfig, state: dict, payload: dict) -> None:
    payload.setdefault("created_at", datetime.now(ZoneInfo(config.timezone)).isoformat(timespec="seconds"))
    events = state.setdefault("events", [])
    events.append(payload)
    del events[:-200]


def process_incoming_audio(
    config: WardConfig,
    audio_path: Path,
    *,
    bed_id: str | None = None,
    allow_external_llm: bool = False,
    deidentify: bool = False,
    model: str | None = None,
    provider: str | None = None,
    deliver_target: str | None = None,
    export_obsidian_note: bool = False,
    obsidian_vault_dir: Path | None = None,
) -> dict:
    routed = route_audio_file(config, audio_path, bed_id=bed_id, move=True)
    result = process_audio(
        config,
        routed.archived_path,
        allow_external_llm=allow_external_llm,
        deidentify=deidentify,
        model=model,
        provider=provider,
        deliver_target=deliver_target,
        export_obsidian_note=export_obsidian_note,
        obsidian_vault_dir=obsidian_vault_dir,
    )
    job_id = result.get("job_id")
    if job_id:
        job_dir = result.get("job_dir", "")
        if not job_dir:
            for action in result.get("actions", []):
                if isinstance(action, dict) and action.get("job_dir"):
                    job_dir = action["job_dir"]
                    break
        append_ward_job(routed.encounter_dir, job_id, job_dir, routed.archived_path)
        state = read_state(config, job_id)
        state["routing"] = {
            "archive_path": str(routed.archived_path),
            "encounter_dir": str(routed.encounter_dir),
            "encounter_id": routed.encounter_id,
            "bed_id": routed.bed_id,
            "bed_id_role": "location_only_not_patient_identity",
            "same_encounter_confidence": routed.confidence,
            "requires_identity_review": routed.requires_identity_review,
            "grouping_basis": routed.grouping_basis,
            "safety_note": "Routing metadata is for organization only and is not clinical evidence.",
        }
        write_state(config, state)
        try:
            result["case_view"] = build_case_view(config, job_id)
        except Exception as exc:
            result["case_view"] = {"ok": False, "error": str(exc)}

    return {
        "ok": bool(result.get("ok")),
        "action": "watch-process",
        "archived_path": str(routed.archived_path),
        "encounter_dir": str(routed.encounter_dir),
        "encounter_id": routed.encounter_id,
        "bed_id": routed.bed_id,
        "same_encounter_confidence": routed.confidence,
        "requires_identity_review": routed.requires_identity_review,
        "grouping_basis": routed.grouping_basis,
        "route_status": routed.route_status,
        "ward_result": result,
    }


def scan_incoming(
    config: WardConfig,
    *,
    incoming_dir: Path | None = None,
    once: bool = True,
    stable_seconds: int = DEFAULT_STABILITY_SECONDS,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    allow_external_llm: bool = False,
    deidentify: bool = False,
    model: str | None = None,
    provider: str | None = None,
    deliver_target: str | None = None,
    export_obsidian_note: bool = False,
    obsidian_vault_dir: Path | None = None,
) -> dict:
    incoming = (incoming_dir or config.incoming_dir).expanduser().resolve()
    incoming.mkdir(parents=True, exist_ok=True)
    state = _read_state(config)
    processed = state.setdefault("processed", {})
    results = []

    while True:
        for path in sorted(incoming.iterdir()):
            if not is_audio_file(path):
                continue
            try:
                fingerprint = _fingerprint(path)
            except FileNotFoundError:
                continue
            if fingerprint in processed:
                continue
            if not _is_stable(path, stable_seconds):
                continue

            try:
                result = process_incoming_audio(
                    config,
                    path,
                    allow_external_llm=allow_external_llm,
                    deidentify=deidentify,
                    model=model,
                    provider=provider,
                    deliver_target=deliver_target,
                    export_obsidian_note=export_obsidian_note,
                    obsidian_vault_dir=obsidian_vault_dir,
                )
                processed[fingerprint] = result
                _event(config, state, {"ok": True, "source_path": str(path), "result": result})
                results.append(result)
            except Exception as exc:
                failure = {"ok": False, "source_path": str(path), "error": str(exc)}
                processed[fingerprint] = failure
                _event(config, state, failure)
                results.append(failure)
            _write_state(config, state)

        if once:
            return {"ok": True, "action": "watch-once", "incoming_dir": str(incoming), "processed_count": len(results), "results": results}
        time.sleep(poll_seconds)
