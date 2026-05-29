from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from .config import WardConfig


STT_REVIEW_CANDIDATES_FILE = "stt_review_candidates.json"
MEDICAL_QUEUE_FILE = "stt_medical_term_candidates.yml"
BEDSIDE_QUEUE_FILE = "stt_bedside_phrase_candidates.yml"
TAIL_QUEUE_FILE = "stt_tail_noise_candidates.yml"
README_FILE = "README.md"
MEDICAL_LEXICON_FILE = Path(__file__).with_name("medical_lexicon.yml")
BEDSIDE_PHRASE_RULES_FILE = Path(__file__).with_name("stt_bedside_phrase_rules.yml")
TAIL_NOISE_PHRASES_FILE = Path(__file__).with_name("stt_tail_noise_phrases.yml")
BEDSIDE_PHRASE_PATTERNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("個管室", "care_coordination", ("個管室",)),
    ("支開家屬", "privacy_context", ("支開家屬", "家屬支開")),
    ("報告出來", "result_follow_up", ("報告出來", "結果出來", "下午會出來")),
    ("有狀況再call", "escalation", ("有狀況再call", "有狀況再 call", "再call我", "再 call 我")),
    ("先不要", "management_decision", ("先不要",)),
    ("先處理", "management_decision", ("先處理",)),
    ("馬上開上去", "treatment_action", ("馬上開上去", "開上去")),
)


def _load_promoted_bedside_patterns(path: Path | None = None) -> list[tuple[str, str, tuple[str, ...]]]:
    path = path or BEDSIDE_PHRASE_RULES_FILE
    if not path.exists():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    patterns: list[tuple[str, str, tuple[str, ...]]] = []
    for item in payload.get("items") or []:
        phrase = str(item.get("phrase") or "").strip()
        category = str(item.get("category") or "approved").strip()
        variants = tuple(str(value).strip() for value in (item.get("variants") or [phrase]) if str(value).strip())
        if phrase and variants:
            patterns.append((phrase, category, variants))
    return patterns


def _bedside_patterns() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    return BEDSIDE_PHRASE_PATTERNS + tuple(_load_promoted_bedside_patterns())


def _job_dir(config: WardConfig, job_id: str) -> Path:
    return config.output_dir / job_id


def _resolve_job_id(config: WardConfig, job_id: str) -> str:
    if job_id != "latest":
        return job_id
    jobs = sorted(
        (
            path.name
            for path in config.output_dir.iterdir()
            if path.is_dir() and not path.name.startswith("_") and (path / "state.json").exists()
        ),
        reverse=True,
    )
    if not jobs:
        raise FileNotFoundError("no ward jobs found")
    return jobs[0]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _queue_dir(config: WardConfig) -> Path:
    return getattr(config, "stt_review_queue_dir", Path(__file__).resolve().parents[1] / "data" / "stt_review_queue")


def _now(config: WardConfig) -> str:
    return datetime.now(ZoneInfo(getattr(config, "timezone", "Asia/Taipei"))).isoformat(timespec="seconds")


def _candidate_key(*parts: object) -> str:
    payload = "\0".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _context_from_item(item: dict[str, Any]) -> str:
    return str(item.get("context") or item.get("reason") or "").strip()


def _line_context(lines: list[str], index: int) -> str:
    start = max(0, index - 1)
    end = min(len(lines), index + 2)
    return "\n".join(line.strip() for line in lines[start:end] if line.strip())


def _medical_candidates(job_id: str, job_dir: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for item in _read_json(job_dir / "correction_log.json", []):
        original = str(item.get("original") or "").strip()
        candidate = str(item.get("corrected") or item.get("candidate") or "").strip()
        if not original or not candidate or original == candidate:
            continue
        candidates.append(
            {
                "type": "medical_term",
                "key": _candidate_key("medical", original, candidate, item.get("category")),
                "original": original,
                "candidate": candidate,
                "category": str(item.get("category") or "unknown"),
                "confidence": str(item.get("confidence") or "unknown"),
                "source": "correction_log.json",
                "line": item.get("line"),
                "context": _context_from_item(item),
                "job_id": job_id,
            }
        )

    for item in _read_json(job_dir / "uncertain_terms.json", []):
        original = str(item.get("original") or "").strip()
        candidate = str(item.get("candidate") or "").strip()
        if not original or not candidate:
            continue
        candidates.append(
            {
                "type": "medical_term",
                "key": _candidate_key("medical", original, candidate, item.get("category")),
                "original": original,
                "candidate": candidate,
                "category": str(item.get("category") or "unknown"),
                "confidence": str(item.get("confidence") or "unknown"),
                "source": "uncertain_terms.json",
                "line": item.get("line"),
                "context": _context_from_item(item),
                "job_id": job_id,
            }
        )
    return candidates


def _tail_noise_candidates(job_id: str, job_dir: Path) -> list[dict[str, Any]]:
    debug = _read_json(job_dir / "transcription.debug.json", {})
    removed = ((debug.get("transcript_filter") or {}).get("removed_segments") or [])
    candidates: list[dict[str, Any]] = []
    for item in removed:
        if item.get("reason") != "nonclinical_hallucination":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        candidates.append(
            {
                "type": "tail_noise",
                "key": _candidate_key("tail", text),
                "text": text,
                "reason": "nonclinical_hallucination",
                "source": "transcription.debug.json",
                "start": item.get("start"),
                "end": item.get("end"),
                "speaker": item.get("speaker"),
                "job_id": job_id,
            }
        )
    return candidates


def _read_transcript_for_phrase_scan(job_dir: Path) -> tuple[str, str]:
    for name in ("normalized_transcript.md", "raw_transcript.txt", "transcript.speaker.txt", "transcript.manual.txt"):
        path = job_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace"), name
    return "", "transcript"


def _bedside_phrase_candidates(job_id: str, job_dir: Path) -> list[dict[str, Any]]:
    text, source = _read_transcript_for_phrase_scan(job_dir)
    if not text.strip():
        return []
    lines = text.splitlines()
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for line_index, line in enumerate(lines):
        for phrase, category, variants in _bedside_patterns():
            if not any(variant in line for variant in variants):
                continue
            identity = (phrase, line_index + 1)
            if identity in seen:
                continue
            seen.add(identity)
            candidates.append(
                {
                    "type": "bedside_phrase",
                    "key": _candidate_key("bedside", phrase, category),
                    "phrase": phrase,
                    "category": category,
                    "source": source,
                    "line": line_index + 1,
                    "context": _line_context(lines, line_index),
                    "job_id": job_id,
                }
            )
    return candidates


def _source_artifacts(job_dir: Path) -> list[str]:
    names = [
        "raw_transcript.txt",
        "normalized_transcript.md",
        "correction_log.json",
        "uncertain_terms.json",
        "stt_recovery_candidates.json",
        "stt_coverage_audit.json",
        "transcription.debug.json",
    ]
    return [name for name in names if (job_dir / name).exists()]


def _read_queue(path: Path, queue_type: str) -> dict[str, Any]:
    if path.exists():
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(payload, dict):
            payload.setdefault("version", "1.0")
            payload.setdefault("queue_type", queue_type)
            payload.setdefault("items", [])
            return payload
    return {"version": "1.0", "queue_type": queue_type, "items": []}


def _example_from_candidate(candidate: dict[str, Any], seen_at: str) -> dict[str, Any]:
    example = {
        "job_id": candidate.get("job_id"),
        "seen_at": seen_at,
        "source": candidate.get("source"),
    }
    for key in ("line", "context", "start", "end", "speaker"):
        if candidate.get(key) not in (None, ""):
            example[key] = candidate.get(key)
    return example


def _example_identity(example: dict[str, Any]) -> tuple[object, ...]:
    return (
        example.get("job_id"),
        example.get("source"),
        example.get("line"),
        example.get("context"),
        example.get("start"),
        example.get("end"),
        example.get("speaker"),
    )


def _merge_queue_item(existing: dict[str, Any], candidate: dict[str, Any], seen_at: str) -> dict[str, Any]:
    examples = list(existing.get("examples") or [])
    example = _example_from_candidate(candidate, seen_at)
    known_examples = {_example_identity(item) for item in examples}
    if _example_identity(example) in known_examples:
        return existing

    existing["occurrences"] = int(existing.get("occurrences") or 0) + 1
    existing["last_seen_job_id"] = candidate.get("job_id")
    existing["last_seen_at"] = seen_at
    examples.append(example)
    existing["examples"] = examples[-5:]
    return existing


def _new_queue_item(candidate: dict[str, Any], seen_at: str) -> dict[str, Any]:
    item = {
        "key": candidate["key"],
        "status": "watch",
        "occurrences": 1,
        "first_seen_job_id": candidate.get("job_id"),
        "last_seen_job_id": candidate.get("job_id"),
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
        "examples": [_example_from_candidate(candidate, seen_at)],
    }
    if candidate["type"] == "medical_term":
        item.update(
            {
                "original": candidate.get("original"),
                "candidate": candidate.get("candidate"),
                "category": candidate.get("category"),
                "confidence": candidate.get("confidence"),
            }
        )
    elif candidate["type"] == "bedside_phrase":
        item.update({"phrase": candidate.get("phrase"), "category": candidate.get("category")})
    elif candidate["type"] == "tail_noise":
        item.update({"text": candidate.get("text"), "reason": candidate.get("reason")})
    return item


def _update_queue(path: Path, queue_type: str, candidates: list[dict[str, Any]], seen_at: str) -> dict[str, Any]:
    payload = _read_queue(path, queue_type)
    items = list(payload.get("items") or [])
    by_key = {str(item.get("key")): item for item in items if item.get("key")}
    for candidate in candidates:
        key = str(candidate.get("key") or "")
        if not key:
            continue
        if key in by_key:
            by_key[key] = _merge_queue_item(by_key[key], candidate, seen_at)
        else:
            by_key[key] = _new_queue_item(candidate, seen_at)
    payload["items"] = sorted(by_key.values(), key=lambda item: (-int(item.get("occurrences") or 0), str(item.get("key") or "")))
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"path": str(path), "item_count": len(payload["items"])}


def _ensure_review_queue(config: WardConfig) -> Path:
    queue_dir = _queue_dir(config)
    queue_dir.mkdir(parents=True, exist_ok=True)
    readme_path = queue_dir / README_FILE
    readme_text = (
        "# Ward STT Review Queue\n\n"
        "Edit these YAML files when you have time. Keep `status: watch` for unreviewed items, "
        "change to `approved` when the item should be promoted later, and use `rejected` for noise.\n\n"
        "- `stt_medical_term_candidates.yml`: medical term corrections and uncertain terms.\n"
        "- `stt_bedside_phrase_candidates.yml`: bedside phrase and ward-context candidates.\n"
        "- `stt_tail_noise_candidates.yml`: nonclinical tail-noise candidates.\n\n"
        "Collection records candidates and statistics. Approved items are automatically promoted at the start of each future "
        "`ward transcribe` / `ward process` run, before STT starts.\n"
        "Use `ward stt-review-queue` to see a summary of current queue status.\n"
        "Use `ward stt-promote-approved --dry-run` to preview approved items manually, or "
        "`ward stt-promote-approved` to apply them immediately without waiting for the next transcription.\n"
    )
    readme_current = readme_path.read_text(encoding="utf-8", errors="replace") if readme_path.exists() else ""
    if not readme_path.exists() or "ward stt-promote-approved" not in readme_current:
        readme_path.write_text(
            readme_text,
            encoding="utf-8",
        )
    for name, queue_type in (
        (MEDICAL_QUEUE_FILE, "stt_medical_term_candidates"),
        (BEDSIDE_QUEUE_FILE, "stt_bedside_phrase_candidates"),
        (TAIL_QUEUE_FILE, "stt_tail_noise_candidates"),
    ):
        path = queue_dir / name
        if not path.exists():
            path.write_text(
                yaml.safe_dump({"version": "1.0", "queue_type": queue_type, "items": []}, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
    return queue_dir


def stt_review(config: WardConfig, job_id: str) -> dict[str, Any]:
    try:
        job_id = _resolve_job_id(config, job_id)
    except FileNotFoundError as exc:
        return {"ok": False, "action": "stt-review", "error": str(exc)}
    job_dir = _job_dir(config, job_id)
    if not job_dir.exists():
        return {"ok": False, "action": "stt-review", "job_id": job_id, "error": f"job not found: {job_id}"}

    queue_dir = _ensure_review_queue(config)
    seen_at = _now(config)
    medical = _medical_candidates(job_id, job_dir)
    bedside = _bedside_phrase_candidates(job_id, job_dir)
    tail = _tail_noise_candidates(job_id, job_dir)
    review = {
        "version": "1.0",
        "job_id": job_id,
        "generated_at": seen_at,
        "review_queue_dir": str(queue_dir),
        "medical_term_candidates": medical,
        "bedside_phrase_candidates": bedside,
        "tail_noise_candidates": tail,
        "source_artifacts": _source_artifacts(job_dir),
        "policy": "Candidates are collected for later human review. Phase 1 does not modify production STT rules.",
    }
    _write_json(job_dir / STT_REVIEW_CANDIDATES_FILE, review)

    queue_updates = [
        _update_queue(queue_dir / MEDICAL_QUEUE_FILE, "stt_medical_term_candidates", medical, seen_at),
        _update_queue(queue_dir / BEDSIDE_QUEUE_FILE, "stt_bedside_phrase_candidates", bedside, seen_at),
        _update_queue(queue_dir / TAIL_QUEUE_FILE, "stt_tail_noise_candidates", tail, seen_at),
    ]

    return {
        "ok": True,
        "action": "stt-review",
        "job_id": job_id,
        "review_artifact": str(job_dir / STT_REVIEW_CANDIDATES_FILE),
        "review_queue_dir": str(queue_dir),
        "candidate_counts": {
            "medical_terms": len(medical),
            "bedside_phrases": len(bedside),
            "tail_noise": len(tail),
        },
        "queue_updates": queue_updates,
    }


def _queue_summary(path: Path, queue_type: str, limit: int) -> dict[str, Any]:
    payload = _read_queue(path, queue_type)
    items = list(payload.get("items") or [])
    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "watch")
        status_counts[status] = status_counts.get(status, 0) + 1
    top_items = sorted(items, key=lambda item: (-int(item.get("occurrences") or 0), str(item.get("key") or "")))[:limit]
    return {
        "path": str(path),
        "queue_type": queue_type,
        "item_count": len(items),
        "status_counts": status_counts,
        "top_items": top_items,
    }


def stt_review_queue(config: WardConfig, limit: int = 10) -> dict[str, Any]:
    queue_dir = _ensure_review_queue(config)
    queues = [
        _queue_summary(queue_dir / MEDICAL_QUEUE_FILE, "stt_medical_term_candidates", limit),
        _queue_summary(queue_dir / BEDSIDE_QUEUE_FILE, "stt_bedside_phrase_candidates", limit),
        _queue_summary(queue_dir / TAIL_QUEUE_FILE, "stt_tail_noise_candidates", limit),
    ]
    return {
        "ok": True,
        "action": "stt-review-queue",
        "review_queue_dir": str(queue_dir),
        "queues": queues,
        "policy": "Edit YAML statuses manually. Approved items auto-promote before future transcriptions; dry-run remains available for preview.",
    }


def _approved_items(queue_dir: Path, filename: str, queue_type: str) -> list[dict[str, Any]]:
    payload = _read_queue(queue_dir / filename, queue_type)
    return [item for item in payload.get("items") or [] if str(item.get("status") or "").strip().lower() == "approved"]


def _normalized_medical_category(category: str) -> str:
    aliases = {
        "lab": "labs",
        "labs": "labs",
        "medication": "medications",
        "medications": "medications",
        "procedure": "procedures",
        "procedures": "procedures",
        "symptom": "symptoms",
        "symptoms": "symptoms",
        "diagnosis": "diagnosis",
    }
    return aliases.get(category.strip().lower(), "taiwan_clinical_phrases")


def _promoted_confidence(confidence: str) -> str:
    return "high" if confidence.strip().lower() == "high" else "medium"


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload if isinstance(payload, dict) else {}


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _promote_medical_terms(items: list[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
    lexicon = _load_yaml_dict(MEDICAL_LEXICON_FILE)
    promoted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        original = str(item.get("original") or "").strip()
        canonical = str(item.get("candidate") or "").strip()
        if not original or not canonical or original == canonical:
            skipped.append({"key": item.get("key"), "reason": "missing_or_noop_mapping", "original": original, "candidate": canonical})
            continue
        category = _normalized_medical_category(str(item.get("category") or ""))
        confidence = _promoted_confidence(str(item.get("confidence") or ""))
        entries = list(lexicon.get(category) or [])
        target = None
        for entry in entries:
            if str(entry.get("canonical") or "").strip().lower() == canonical.lower() and str(entry.get("confidence") or "").strip().lower() == confidence:
                target = entry
                break
        if target is None:
            target = {"canonical": canonical, "confidence": confidence, "variants": []}
            entries.append(target)
        variants = [str(value).strip() for value in (target.get("variants") or []) if str(value).strip()]
        if original not in variants:
            variants.append(original)
            target["variants"] = variants
            promoted.append({"key": item.get("key"), "category": category, "canonical": canonical, "variant": original, "confidence": confidence})
        else:
            skipped.append({"key": item.get("key"), "reason": "already_present", "category": category, "canonical": canonical, "variant": original})
        lexicon[category] = entries
    if promoted and not dry_run:
        _write_yaml(MEDICAL_LEXICON_FILE, lexicon)
    return {"path": str(MEDICAL_LEXICON_FILE), "promoted": promoted, "skipped": skipped}


def _load_rule_items(path: Path, queue_type: str) -> dict[str, Any]:
    payload = _load_yaml_dict(path)
    payload.setdefault("version", "1.0")
    payload.setdefault("queue_type", queue_type)
    payload.setdefault("items", [])
    return payload


def _promote_bedside_phrases(items: list[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
    payload = _load_rule_items(BEDSIDE_PHRASE_RULES_FILE, "stt_bedside_phrase_rules")
    existing = {str(item.get("phrase") or "").strip() for item in payload.get("items") or []}
    promoted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        phrase = str(item.get("phrase") or "").strip()
        if not phrase:
            skipped.append({"key": item.get("key"), "reason": "missing_phrase"})
            continue
        if phrase in existing:
            skipped.append({"key": item.get("key"), "reason": "already_present", "phrase": phrase})
            continue
        entry = {
            "phrase": phrase,
            "category": str(item.get("category") or "approved"),
            "variants": [phrase],
            "source_queue_key": item.get("key"),
        }
        payload["items"].append(entry)
        existing.add(phrase)
        promoted.append(entry)
    if promoted and not dry_run:
        _write_yaml(BEDSIDE_PHRASE_RULES_FILE, payload)
    return {"path": str(BEDSIDE_PHRASE_RULES_FILE), "promoted": promoted, "skipped": skipped}


def _promote_tail_noise(items: list[dict[str, Any]], *, dry_run: bool = False) -> dict[str, Any]:
    payload = _load_rule_items(TAIL_NOISE_PHRASES_FILE, "stt_tail_noise_phrases")
    existing = {str(item.get("text") or "").strip() for item in payload.get("items") or []}
    promoted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in items:
        text = str(item.get("text") or "").strip()
        if not text:
            skipped.append({"key": item.get("key"), "reason": "missing_text"})
            continue
        if text in existing:
            skipped.append({"key": item.get("key"), "reason": "already_present", "text": text})
            continue
        entry = {"text": text, "source_queue_key": item.get("key")}
        payload["items"].append(entry)
        existing.add(text)
        promoted.append(entry)
    if promoted and not dry_run:
        _write_yaml(TAIL_NOISE_PHRASES_FILE, payload)
    return {"path": str(TAIL_NOISE_PHRASES_FILE), "promoted": promoted, "skipped": skipped}


def stt_promote_approved(config: WardConfig, *, dry_run: bool = False) -> dict[str, Any]:
    queue_dir = _ensure_review_queue(config)
    medical = _approved_items(queue_dir, MEDICAL_QUEUE_FILE, "stt_medical_term_candidates")
    bedside = _approved_items(queue_dir, BEDSIDE_QUEUE_FILE, "stt_bedside_phrase_candidates")
    tail = _approved_items(queue_dir, TAIL_QUEUE_FILE, "stt_tail_noise_candidates")
    results = {
        "medical_terms": _promote_medical_terms(medical, dry_run=dry_run),
        "bedside_phrases": _promote_bedside_phrases(bedside, dry_run=dry_run),
        "tail_noise": _promote_tail_noise(tail, dry_run=dry_run),
    }
    return {
        "ok": True,
        "action": "stt-promote-approved",
        "dry_run": dry_run,
        "review_queue_dir": str(queue_dir),
        "approved_counts": {
            "medical_terms": len(medical),
            "bedside_phrases": len(bedside),
            "tail_noise": len(tail),
        },
        "results": results,
        "policy": "Only approved queue items are promoted. Rejected and watch items are ignored.",
    }
