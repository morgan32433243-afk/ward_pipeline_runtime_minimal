from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .codex_runtime import CODEX_TIMEOUT_SECONDS, run_codex_exec
from .config import WardConfig


RAW_TRANSCRIPT_FILE = "raw_transcript.txt"
NORMALIZED_TRANSCRIPT_FILE = "normalized_transcript.md"
CORRECTION_LOG_FILE = "correction_log.json"
UNCERTAIN_TERMS_FILE = "uncertain_terms.json"
CONFIRMED_TERMS_FILE = "confirmed_terms.json"
LLM_NORMALIZED_TRANSCRIPT_FILE = "llm_normalized_transcript.md"
LLM_NORMALIZATION_FILE = "llm_normalization.json"
LLM_NORMALIZATION_AUDIT_FILE = "llm_normalization_audit.json"
PROMPT_VERSION = "llm_normalization_v1"
PROMPT_PATH = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"
SCHEMA_VERSION = "1.0"
LLM_CODEX_TIMEOUT_SECONDS = CODEX_TIMEOUT_SECONDS

HIGH_RISK_CATEGORIES = {
    "medication",
    "medications",
    "dose",
    "diagnosis",
    "diagnoses",
    "procedure",
    "procedures",
    "lab",
    "labs",
    "lab_value",
    "imaging",
}


class LLMNormalizationError(Exception):
    pass


LLMClient = Callable[[str, int], str]


def _iso_now(config: WardConfig) -> str:
    return datetime.now(ZoneInfo(config.timezone)).isoformat(timespec="seconds")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _read_json(path: Path) -> list | dict:
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"error": f"invalid json: {path.name}"}


def _read_prompt_contract() -> str:
    return _read_text(PROMPT_PATH)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _numbered_lines(text: str) -> str:
    lines = text.splitlines() or [""]
    return "\n".join(f"{index}: {line}" for index, line in enumerate(lines, start=1))


def _risk_level(term: dict) -> str:
    category = str(term.get("category") or "").strip().lower()
    if category in HIGH_RISK_CATEGORIES:
        return "high"
    if term.get("requires_human_confirmation"):
        return "medium"
    return "low"


def _uncertain_items(uncertain_terms: list | dict) -> list[dict]:
    if not isinstance(uncertain_terms, list):
        return []
    items = []
    for term in uncertain_terms:
        if not isinstance(term, dict):
            continue
        line = term.get("line")
        source_refs = []
        if isinstance(line, int):
            source_refs.append(
                {
                    "artifact": RAW_TRANSCRIPT_FILE,
                    "line_start": line,
                    "line_end": line,
                }
            )
        items.append(
            {
                "text": str(term.get("original") or ""),
                "candidate_normalization": str(term.get("candidate") or term.get("corrected") or ""),
                "reason_uncertain": str(term.get("reason") or "uncertain normalization candidate"),
                "risk_level": _risk_level(term),
                "source_refs": source_refs,
            }
        )
    return items


def _validate_payload(payload: dict) -> None:
    status = payload.get("status")
    if status not in {"ok", "blocked", "partial", "failed"}:
        raise LLMNormalizationError(f"invalid llm normalization status: {status}")
    if not isinstance(payload.get("summary"), dict):
        raise LLMNormalizationError("llm normalization summary must be an object")
    if not isinstance(payload.get("blocks"), list):
        raise LLMNormalizationError("llm normalization blocks must be a list")
    if status in {"ok", "partial"} and not payload.get("blocks"):
        raise LLMNormalizationError("ok/partial llm normalization requires at least one block")
    if not isinstance(payload.get("uncertain_items"), list):
        raise LLMNormalizationError("llm normalization uncertain_items must be a list")
    if not isinstance(payload.get("suppressed_items"), list):
        raise LLMNormalizationError("llm normalization suppressed_items must be a list")
    for index, block in enumerate(payload.get("blocks") or [], start=1):
        if not isinstance(block, dict):
            raise LLMNormalizationError(f"llm normalization block {index} must be an object")
        for key in ("block_id", "source_text", "normalized_text", "confidence", "rationale", "source_refs"):
            if key not in block:
                raise LLMNormalizationError(f"llm normalization block {index} missing {key}")
        if block.get("confidence") not in {"high", "medium", "low"}:
            raise LLMNormalizationError(f"llm normalization block {index} has invalid confidence")
        if not isinstance(block.get("source_refs"), list) or not block["source_refs"]:
            raise LLMNormalizationError(f"llm normalization block {index} must have source_refs")


def _validate_source_refs(payload: dict, job_dir: Path, max_line_by_artifact: dict[str, int]) -> None:
    for block in payload.get("blocks") or []:
        for ref in block.get("source_refs") or []:
            if not isinstance(ref, dict):
                raise LLMNormalizationError("source_ref must be an object")
            artifact = str(ref.get("artifact") or "")
            if artifact not in max_line_by_artifact:
                raise LLMNormalizationError(f"source_ref uses unsupported artifact: {artifact}")
            if not (job_dir / artifact).exists():
                raise LLMNormalizationError(f"source_ref artifact does not exist: {artifact}")
            line_start = int(ref.get("line_start") or 0)
            line_end = int(ref.get("line_end") or 0)
            if line_start < 1 or line_end < line_start or line_end > max_line_by_artifact[artifact]:
                raise LLMNormalizationError(f"source_ref line range is invalid for {artifact}")


def _guard_unsupported_expansion(payload: dict) -> None:
    high_risk_flags = {
        "contains_medication",
        "contains_dose",
        "contains_diagnosis",
        "contains_procedure",
        "contains_lab_value",
        "contains_imaging",
    }
    for block in payload.get("blocks") or []:
        source_text = str(block.get("source_text") or "")
        normalized_text = str(block.get("normalized_text") or "")
        flags = set(block.get("flags") or [])
        if len(normalized_text) > max(len(source_text) * 2, len(source_text) + 240):
            raise LLMNormalizationError("normalized_text expands too far beyond source_text")
        if flags & high_risk_flags and block.get("confidence") != "high":
            block.setdefault("flags", [])
            if "needs_review" not in block["flags"]:
                block["flags"].append("needs_review")


def _extract_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)

    candidates = [stripped]
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        candidates.append(stripped[start : end + 1])

    last_error = None
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            break
        except json.JSONDecodeError as exc:
            last_error = exc
    else:
        raise LLMNormalizationError(f"LLM output is not valid JSON: {last_error}") from last_error
    if not isinstance(payload, dict):
        raise LLMNormalizationError("LLM output JSON must be an object")
    return payload


def _normalized_transcript_from_payload(payload: dict) -> str:
    transcript = str(payload.get("normalized_transcript") or "").strip()
    if transcript:
        return transcript + "\n"
    blocks = []
    for block in payload.get("blocks") or []:
        text = str(block.get("normalized_text") or "").strip()
        if text:
            blocks.append(text)
    return "\n\n".join(blocks).strip() + "\n"


def _skeleton_payload(
    *,
    job_id: str,
    normalized_transcript: str,
    uncertain_items: list[dict],
    input_artifacts: list[dict],
    line_end: int,
    correction_log: list | dict,
    confirmed_terms: list | dict,
) -> dict:
    high_risk_count = sum(1 for item in uncertain_items if item.get("risk_level") == "high")
    return {
        "version": SCHEMA_VERSION,
        "job_id": job_id,
        "status": "partial",
        "summary": {
            "normalized_changes": 0,
            "uncertain_items": len(uncertain_items),
            "high_risk_items": high_risk_count,
        },
        "blocks": [
            {
                "block_id": "b1",
                "source_text": normalized_transcript,
                "normalized_text": normalized_transcript,
                "change_type": "cleanup",
                "confidence": "high",
                "rationale": "Skeleton mode preserved conservative normalized transcript.",
                "source_refs": [
                    {
                        "artifact": NORMALIZED_TRANSCRIPT_FILE,
                        "line_start": 1,
                        "line_end": line_end,
                    }
                ],
                "flags": ["skeleton_noop"] + (["needs_review"] if uncertain_items else []),
            }
        ],
        "uncertain_items": uncertain_items,
        "suppressed_items": [],
        "input_artifacts": input_artifacts,
        "confirmed_terms_count": len(confirmed_terms) if isinstance(confirmed_terms, list) else 0,
        "correction_log_count": len(correction_log) if isinstance(correction_log, list) else 0,
    }


def _build_prompt(
    *,
    job_id: str,
    raw_transcript: str,
    normalized_transcript: str,
    correction_log: list | dict,
    confirmed_terms: list | dict,
    uncertain_terms: list | dict,
    input_artifacts: list[dict],
) -> str:
    schema = {
        "version": SCHEMA_VERSION,
        "job_id": job_id,
        "status": "ok | partial | failed",
        "summary": {
            "normalized_changes": 0,
            "uncertain_items": 0,
            "high_risk_items": 0,
        },
        "normalized_transcript": "...",
        "blocks": [
            {
                "block_id": "b1",
                "source_text": "...",
                "normalized_text": "...",
                "change_type": "punctuation | abbreviation | terminology | word_order | cleanup",
                "confidence": "high | medium | low",
                "rationale": "...",
                "source_refs": [{"artifact": NORMALIZED_TRANSCRIPT_FILE, "line_start": 1, "line_end": 1}],
                "flags": ["needs_review"],
            }
        ],
        "uncertain_items": [],
        "suppressed_items": [],
    }
    return f"""{_read_prompt_contract()}

Return only one JSON object. Do not wrap the JSON in prose.

## Job

Job ID: {job_id}
Prompt version: {PROMPT_VERSION}

## Required JSON Shape

```json
{json.dumps(schema, ensure_ascii=False, indent=2)}
```

## Input Artifact Hashes

```json
{json.dumps(input_artifacts, ensure_ascii=False, indent=2)}
```

## correction_log.json

```json
{json.dumps(correction_log, ensure_ascii=False, indent=2)}
```

## confirmed_terms.json

```json
{json.dumps(confirmed_terms, ensure_ascii=False, indent=2)}
```

## uncertain_terms.json

```json
{json.dumps(uncertain_terms, ensure_ascii=False, indent=2)}
```

## normalized_transcript.md With Line Numbers

```text
{_numbered_lines(normalized_transcript)}
```

## raw_transcript.txt With Line Numbers

```text
{_numbered_lines(raw_transcript)}
```
"""


def _repair_prompt(original_prompt: str, invalid_output: str, error: str) -> str:
    return f"""{original_prompt}

The previous response was rejected.

Validation error:
{error}

Previous response:
```text
{invalid_output[:12000]}
```

Return exactly one corrected JSON object that follows the required schema and source-ref rules.
"""


def _call_codex(
    prompt: str,
    attempt: int,
    *,
    config: WardConfig,
    model: str | None = None,
    provider: str | None = None,
) -> str:
    if provider and provider != "openai-codex":
        raise LLMNormalizationError(f"Codex exec runner does not support provider override: {provider}")
    try:
        completed, output = run_codex_exec(
            prompt,
            config=config,
            cwd=config.output_dir,
            output_dir=config.output_dir / "_llm_normalizer",
            model=model,
            timeout=LLM_CODEX_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise LLMNormalizationError(f"Codex exec timed out after {exc.timeout} seconds") from exc
    except Exception as exc:
        raise LLMNormalizationError(f"Codex exec failed: {exc}") from exc
    diagnostic = (completed.stdout or completed.stderr).strip()
    lower_diagnostic = diagnostic.lower()
    provider_error = (
        lower_diagnostic.startswith("api call failed")
        or "connection error" in lower_diagnostic
        or "authentication error" in lower_diagnostic
        or "rate limit" in lower_diagnostic
    )
    if completed.returncode != 0 or provider_error:
        raise LLMNormalizationError(diagnostic or f"Codex exec exited with code {completed.returncode}")
    if not output:
        raise LLMNormalizationError("Codex exec exited with code 0 but produced empty normalization output")
    return output


def run_llm_normalization(
    config: WardConfig,
    job_id: str,
    *,
    job_dir: Path,
    state: dict,
    allow_external_llm: bool = False,
    model: str | None = None,
    provider: str | None = None,
    llm_client: LLMClient | None = None,
) -> dict:
    """Create LLM-normalization artifacts without overwriting earlier transcript evidence.

    The default external provider is Hermes. Passing model/provider as
    "skeleton" or injecting llm_client supports deterministic contract tests.
    """
    started_at = _iso_now(config)
    input_artifacts = [
        _artifact_meta(job_dir, RAW_TRANSCRIPT_FILE),
        _artifact_meta(job_dir, NORMALIZED_TRANSCRIPT_FILE),
        _artifact_meta(job_dir, CORRECTION_LOG_FILE),
        _artifact_meta(job_dir, CONFIRMED_TERMS_FILE),
        _artifact_meta(job_dir, UNCERTAIN_TERMS_FILE),
    ]
    raw_transcript = _read_text(job_dir / RAW_TRANSCRIPT_FILE)
    normalized_transcript = _read_text(job_dir / NORMALIZED_TRANSCRIPT_FILE) or raw_transcript
    correction_log = _read_json(job_dir / CORRECTION_LOG_FILE)
    confirmed_terms = _read_json(job_dir / CONFIRMED_TERMS_FILE)
    uncertain_terms = _read_json(job_dir / UNCERTAIN_TERMS_FILE)
    line_end = max(_line_count(normalized_transcript), 1)
    max_line_by_artifact = {
        RAW_TRANSCRIPT_FILE: max(_line_count(raw_transcript), 1),
        NORMALIZED_TRANSCRIPT_FILE: line_end,
    }

    audit = {
        "version": SCHEMA_VERSION,
        "job_id": job_id,
        "stage": "llm_normalization",
        "prompt_version": PROMPT_VERSION,
        "model": model or "not_configured",
        "provider": provider or "not_configured",
        "temperature": None,
        "top_p": None,
        "token_usage": None,
        "retry_count": 0,
        "started_at": started_at,
        "completed_at": None,
        "input_artifacts": input_artifacts,
        "output_artifacts": {},
        "status": None,
        "blocked_reason": None,
    }

    if not allow_external_llm:
        payload = {
            "version": SCHEMA_VERSION,
            "job_id": job_id,
            "status": "blocked",
            "summary": {
                "normalized_changes": 0,
                "uncertain_items": len(_uncertain_items(uncertain_terms)),
                "high_risk_items": 0,
            },
            "blocks": [],
            "uncertain_items": _uncertain_items(uncertain_terms),
            "suppressed_items": [],
            "input_artifacts": input_artifacts,
            "blocked_reason": "blocked_by_policy",
        }
        _validate_payload(payload)
        _write_json(job_dir / LLM_NORMALIZATION_FILE, payload)
        audit["status"] = "blocked"
        audit["blocked_reason"] = "external_llm_not_allowed"
        audit["completed_at"] = _iso_now(config)
        audit["output_artifacts"] = {LLM_NORMALIZATION_FILE: _artifact_meta(job_dir, LLM_NORMALIZATION_FILE)}
        _write_json(job_dir / LLM_NORMALIZATION_AUDIT_FILE, audit)
        return {
            "ok": False,
            "status": "blocked",
            "message": "external LLM normalization not allowed by policy",
            "artifacts": {
                "llm_normalization": LLM_NORMALIZATION_FILE,
                "llm_normalization_audit": LLM_NORMALIZATION_AUDIT_FILE,
            },
        }

    uncertain_items = _uncertain_items(uncertain_terms)
    if model == "skeleton" or provider in {"skeleton", "contract-test", "local-contract"}:
        payload = _skeleton_payload(
            job_id=job_id,
            normalized_transcript=normalized_transcript,
            uncertain_items=uncertain_items,
            input_artifacts=input_artifacts,
            line_end=line_end,
            correction_log=correction_log,
            confirmed_terms=confirmed_terms,
        )
        _validate_payload(payload)
        _validate_source_refs(payload, job_dir, max_line_by_artifact)
        (job_dir / LLM_NORMALIZED_TRANSCRIPT_FILE).write_text(normalized_transcript, encoding="utf-8")
        _write_json(job_dir / LLM_NORMALIZATION_FILE, payload)

        audit["status"] = "partial"
        audit["completed_at"] = _iso_now(config)
        audit["output_artifacts"] = {
            LLM_NORMALIZED_TRANSCRIPT_FILE: _artifact_meta(job_dir, LLM_NORMALIZED_TRANSCRIPT_FILE),
            LLM_NORMALIZATION_FILE: _artifact_meta(job_dir, LLM_NORMALIZATION_FILE),
        }
        _write_json(job_dir / LLM_NORMALIZATION_AUDIT_FILE, audit)
        return {
            "ok": True,
            "status": "partial",
            "message": "llm normalization skeleton completed with no-op transcript preservation",
            "artifacts": {
                "llm_normalized_transcript": LLM_NORMALIZED_TRANSCRIPT_FILE,
                "llm_normalization": LLM_NORMALIZATION_FILE,
                "llm_normalization_audit": LLM_NORMALIZATION_AUDIT_FILE,
            },
        }

    base_prompt = _build_prompt(
        job_id=job_id,
        raw_transcript=raw_transcript,
        normalized_transcript=normalized_transcript,
        correction_log=correction_log,
        confirmed_terms=confirmed_terms,
        uncertain_terms=uncertain_terms,
        input_artifacts=input_artifacts,
    )
    client = llm_client or (
        lambda prompt, attempt: _call_codex(
            prompt,
            attempt,
            config=config,
            model=model,
            provider=provider,
        )
    )
    last_error = ""
    raw_output = ""
    payload = None
    prompt = base_prompt
    for attempt in range(2):
        try:
            raw_output = client(prompt, attempt)
            payload = _extract_json_object(raw_output)
            payload.setdefault("version", SCHEMA_VERSION)
            payload.setdefault("job_id", job_id)
            payload.setdefault("suppressed_items", [])
            payload.setdefault("uncertain_items", uncertain_items)
            payload.setdefault("input_artifacts", input_artifacts)
            _validate_payload(payload)
            _validate_source_refs(payload, job_dir, max_line_by_artifact)
            _guard_unsupported_expansion(payload)
            break
        except Exception as exc:
            last_error = str(exc)
            audit["retry_count"] = attempt + 1
            if attempt == 0:
                prompt = _repair_prompt(base_prompt, raw_output, last_error)
                continue
            payload = None

    if payload is None:
        failed_payload = {
            "version": SCHEMA_VERSION,
            "job_id": job_id,
            "status": "failed",
            "summary": {
                "normalized_changes": 0,
                "uncertain_items": len(uncertain_items),
                "high_risk_items": sum(1 for item in uncertain_items if item.get("risk_level") == "high"),
            },
            "blocks": [],
            "uncertain_items": uncertain_items,
            "suppressed_items": [],
            "input_artifacts": input_artifacts,
            "error": last_error,
            "raw_output_excerpt": raw_output[:2000],
        }
        _validate_payload(failed_payload)
        _write_json(job_dir / LLM_NORMALIZATION_FILE, failed_payload)
        audit["status"] = "failed"
        audit["blocked_reason"] = last_error
        audit["raw_output_excerpt"] = raw_output[:2000]
        audit["completed_at"] = _iso_now(config)
        audit["output_artifacts"] = {LLM_NORMALIZATION_FILE: _artifact_meta(job_dir, LLM_NORMALIZATION_FILE)}
        _write_json(job_dir / LLM_NORMALIZATION_AUDIT_FILE, audit)
        return {
            "ok": False,
            "status": "failed",
            "message": last_error,
            "artifacts": {
                "llm_normalization": LLM_NORMALIZATION_FILE,
                "llm_normalization_audit": LLM_NORMALIZATION_AUDIT_FILE,
            },
        }

    normalized_output = _normalized_transcript_from_payload(payload)
    (job_dir / LLM_NORMALIZED_TRANSCRIPT_FILE).write_text(normalized_output, encoding="utf-8")
    _write_json(job_dir / LLM_NORMALIZATION_FILE, payload)

    audit["status"] = payload["status"]
    audit["completed_at"] = _iso_now(config)
    audit["output_artifacts"] = {
        LLM_NORMALIZED_TRANSCRIPT_FILE: _artifact_meta(job_dir, LLM_NORMALIZED_TRANSCRIPT_FILE),
        LLM_NORMALIZATION_FILE: _artifact_meta(job_dir, LLM_NORMALIZATION_FILE),
    }
    _write_json(job_dir / LLM_NORMALIZATION_AUDIT_FILE, audit)
    return {
        "ok": payload["status"] in {"ok", "partial"},
        "status": payload["status"],
        "message": "llm normalization completed",
        "artifacts": {
            "llm_normalized_transcript": LLM_NORMALIZED_TRANSCRIPT_FILE,
            "llm_normalization": LLM_NORMALIZATION_FILE,
            "llm_normalization_audit": LLM_NORMALIZATION_AUDIT_FILE,
        },
    }
