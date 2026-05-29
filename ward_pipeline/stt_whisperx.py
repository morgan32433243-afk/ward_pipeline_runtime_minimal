from __future__ import annotations

import argparse
import inspect
import importlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import yaml


SAMPLE_RATE = 16000
DEFAULT_MODEL = "large-v3"
DEFAULT_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
DEFAULT_LANGUAGE = "zh"
DEFAULT_TASK = "transcribe"
DEFAULT_INITIAL_PROMPT = (
    "台灣病房查房錄音，繁體中文為主，可混用英文醫學縮寫、診斷、檢驗值、"
    "藥名與劑量。常見詞彙：fever, cough, dyspnea, pneumonia, diabetes, "
    "hypertension, creatinine, CRP, WBC, SpO2, CD4, HIV combo test, PCP, "
    "ceftriaxone, azithromycin, insulin, metformin, Bactrim, prednisolone, "
    "nasal oxygen, bronchoscopy, blood culture, sputum culture, syphilis screen。"
)
PROMPT_CONTAMINATION_PHRASES = (
    "請忠實轉錄不要翻譯不要自行補上未聽到的藥名劑量或檢驗數值",
    "忠實轉錄不要翻譯不要自行補上未聽到的藥名劑量或檢驗數值",
    "不要翻譯不要自行補上未聽到的藥名劑量或檢驗數值",
    "詞彙fevercoughdyspneapneumoniahypertensioncrpsputumculture",
    "常見詞彙fevercoughdyspneapneumonia",
)
NONCLINICAL_HALLUCINATION_PHRASES = (
    "請不吝點讚",
    "不吝點讚",
    "點讚訂閱分享",
    "按讚訂閱分享",
    "訂閱分享打賞",
    "打賞支持明鏡",
    "打賞支持明镜",
    "支持明鏡",
    "支持明镜",
    "明鏡及點點欄目",
    "明镜及點點栏目",
    "感謝收看",
    "下集再見",
    "歡迎訂閱",
    "買美式咖啡",
    "幫你買美式咖啡",
)
NONCLINICAL_HALLUCINATION_TERMS = (
    "點讚",
    "按讚",
    "訂閱",
    "打賞",
    "明鏡",
    "明镜",
    "點點欄目",
    "點點栏目",
)
TAIL_NOISE_PHRASES_FILE = Path(__file__).with_name("stt_tail_noise_phrases.yml")
TIMING_FILE = "transcription.timing.json"
DEBUG_FILE = "transcription.debug.json"
STT_COVERAGE_AUDIT_FILE = "stt_coverage_audit.json"
STT_RECOVERY_CANDIDATES_FILE = "stt_recovery_candidates.json"
DIARIZATION_RENDER_FILE = "diarization_render.md"
COVERAGE_GAP_THRESHOLD_SECONDS = 5.0
COVERAGE_GAP_MERGE_SECONDS = 1.0
COVERAGE_GAP_BUFFER_SECONDS = 3.0
DIARIZATION_RENDER_MERGE_GAP_SECONDS = 1.0
MAX_RECOVERY_GAPS = 3
_ACTIVE_TIMINGS: StageTimings | None = None


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _load_runtime_env() -> None:
    _load_env_file(Path.home() / ".hermes" / ".env")
    _load_env_file(Path(__file__).resolve().parents[1] / ".env")


def _hf_token() -> str | None:
    _load_runtime_env()
    for name in ("HF_TOKEN", "HUGGINGFACE_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    return None


def _seconds(value: Any) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _timestamp(seconds: float | None) -> str:
    if seconds is None:
        return "??:??"
    total = max(int(seconds), 0)
    return f"{total // 60:02d}:{total % 60:02d}"


def _speaker_text(segments: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        speaker = str(segment.get("speaker") or "SPEAKER_UNKNOWN")
        start = _timestamp(_seconds(segment.get("start")))
        end = _timestamp(_seconds(segment.get("end")))
        lines.append(f"[{speaker} {start}-{end}] {text}")
    return "\n".join(lines).strip()


def _join_render_words(words: list[str]) -> str:
    text = ""
    for raw_word in words:
        word = str(raw_word or "").strip()
        if not word:
            continue
        if text and text[-1].isascii() and text[-1].isalnum() and word[0].isascii() and word[0].isalnum():
            text += " "
        text += word
    return text.strip()


def _word_render_blocks(segment: dict[str, Any]) -> list[dict[str, Any]]:
    words = [word for word in segment.get("words") or [] if str(word.get("word") or "").strip()]
    if not words:
        text = str(segment.get("text") or "").strip()
        if not text:
            return []
        return [
            {
                "speaker": str(segment.get("speaker") or "SPEAKER_UNKNOWN"),
                "start": _seconds(segment.get("start")),
                "end": _seconds(segment.get("end")),
                "text": text,
                "source_start": _seconds(segment.get("start")),
                "source_end": _seconds(segment.get("end")),
                "source": "segment_text",
            }
        ]

    blocks: list[dict[str, Any]] = []
    current_speaker: str | None = None
    current_words: list[str] = []
    current_start: float | None = None
    current_end: float | None = None

    def flush() -> None:
        nonlocal current_speaker, current_words, current_start, current_end
        text = _join_render_words(current_words)
        if text:
            blocks.append(
                {
                    "speaker": current_speaker or "SPEAKER_UNKNOWN",
                    "start": current_start,
                    "end": current_end,
                    "text": text,
                    "source_start": _seconds(segment.get("start")),
                    "source_end": _seconds(segment.get("end")),
                    "source": "word_speakers",
                }
            )
        current_speaker = None
        current_words = []
        current_start = None
        current_end = None

    for word in words:
        speaker = str(word.get("speaker") or segment.get("speaker") or "SPEAKER_UNKNOWN")
        start = _seconds(word.get("start"))
        end = _seconds(word.get("end"))
        gap = None if current_end is None or start is None else start - current_end
        if current_words and (
            speaker != current_speaker
            or (gap is not None and gap > DIARIZATION_RENDER_MERGE_GAP_SECONDS)
        ):
            flush()
        if not current_words:
            current_speaker = speaker
            current_start = start
        current_words.append(str(word.get("word") or ""))
        current_end = end if end is not None else current_end
    flush()
    return blocks


def _merge_render_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for block in blocks:
        if not block.get("text"):
            continue
        if not merged:
            merged.append(dict(block))
            continue
        previous = merged[-1]
        previous_end = _seconds(previous.get("end"))
        block_start = _seconds(block.get("start"))
        same_speaker = previous.get("speaker") == block.get("speaker")
        same_source = previous.get("source") == block.get("source")
        close_gap = (
            previous_end is not None
            and block_start is not None
            and block_start - previous_end <= DIARIZATION_RENDER_MERGE_GAP_SECONDS
        )
        if same_speaker and same_source and close_gap:
            previous["end"] = block.get("end") or previous.get("end")
            previous["source_end"] = block.get("source_end") or previous.get("source_end")
            previous["text"] = _join_render_words([str(previous.get("text") or ""), str(block.get("text") or "")])
            continue
        merged.append(dict(block))
    return merged


def _diarization_render(segments: list[dict[str, Any]]) -> str:
    blocks: list[dict[str, Any]] = []
    for segment in segments:
        blocks.extend(_word_render_blocks(segment))
    blocks = _merge_render_blocks(blocks)

    lines = [
        "# Diarization Render",
        "",
        "Speaker labels are auxiliary timing metadata only. They are not patient identity.",
        "This file mechanically groups ASR text by diarization speaker and time; it does not infer roles or rewrite clinical content.",
        "",
    ]
    if not blocks:
        lines.append("No diarization-renderable transcript blocks were produced.")
        return "\n".join(lines).strip() + "\n"

    for index, block in enumerate(blocks, start=1):
        speaker = str(block.get("speaker") or "SPEAKER_UNKNOWN")
        start = _timestamp(_seconds(block.get("start")))
        end = _timestamp(_seconds(block.get("end")))
        source_start = _timestamp(_seconds(block.get("source_start")))
        source_end = _timestamp(_seconds(block.get("source_end")))
        lines.extend(
            [
                f"## block-{index:03d} {speaker} {start}-{end}",
                "",
                f"- source_window: {source_start}-{source_end}",
                f"- source: {block.get('source') or 'unknown'}",
                "",
                str(block.get("text") or "").strip(),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _compact_text(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff").lower()


def _promoted_tail_noise_phrases(path: Path | None = None) -> tuple[str, ...]:
    path = path or TAIL_NOISE_PHRASES_FILE
    if not path.exists():
        return ()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return ()
    phrases: list[str] = []
    for item in payload.get("items") or []:
        text = str(item.get("text") or "").strip()
        if text:
            phrases.append(text)
    return tuple(phrases)


def _is_prompt_contamination_text(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    if any(phrase in compact for phrase in PROMPT_CONTAMINATION_PHRASES):
        return True
    prompt_lexicon_hits = sum(
        1
        for term in (
            "fever",
            "cough",
            "dyspnea",
            "pneumonia",
            "hypertension",
            "creatinine",
            "sputumculture",
            "bloodculture",
        )
        if term in compact
    )
    return "詞彙" in compact and prompt_lexicon_hits >= 3


def _filter_prompt_contamination_segments(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    filtered: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "")
        if _is_prompt_contamination_text(text):
            removed.append(
                {
                    "start": _seconds(segment.get("start")),
                    "end": _seconds(segment.get("end")),
                    "speaker": segment.get("speaker"),
                    "text": text.strip(),
                    "reason": "prompt_contamination",
                }
            )
            continue
        filtered.append(segment)
    return filtered, removed


def _is_nonclinical_hallucination_text(text: str) -> bool:
    compact = _compact_text(text)
    if not compact:
        return False
    if len(compact) >= 6 and len(set(compact)) <= 2:
        return True
    if any(phrase in compact for phrase in NONCLINICAL_HALLUCINATION_PHRASES):
        return True
    promoted_phrases = tuple(_compact_text(phrase) for phrase in _promoted_tail_noise_phrases())
    if any(phrase and phrase in compact for phrase in promoted_phrases):
        return True
    term_hits = sum(1 for term in NONCLINICAL_HALLUCINATION_TERMS if term in compact)
    return term_hits >= 2


def _filter_nonclinical_hallucination_segments(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    filtered: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for segment in segments:
        text = str(segment.get("text") or "")
        if _is_nonclinical_hallucination_text(text):
            removed.append(
                {
                    "start": _seconds(segment.get("start")),
                    "end": _seconds(segment.get("end")),
                    "speaker": segment.get("speaker"),
                    "text": text.strip(),
                    "reason": "nonclinical_hallucination",
                }
            )
            continue
        filtered.append(segment)
    return filtered, removed


def _intervals_from_segments(segments: list[dict[str, Any]]) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for segment in segments:
        start = _seconds(segment.get("start"))
        end = _seconds(segment.get("end"))
        if start is None or end is None or end <= start:
            continue
        intervals.append((start, end))
    return intervals


def _merge_intervals(intervals: list[tuple[float, float]], *, max_gap: float = COVERAGE_GAP_MERGE_SECONDS) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if not merged or start - merged[-1][1] > max_gap:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _subtract_intervals(
    intervals: list[tuple[float, float]],
    covered: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    gaps: list[tuple[float, float]] = []
    covered = _merge_intervals(covered, max_gap=0.0)
    for start, end in intervals:
        cursor = start
        for cover_start, cover_end in covered:
            if cover_end <= cursor:
                continue
            if cover_start >= end:
                break
            if cover_start > cursor:
                gaps.append((cursor, min(cover_start, end)))
            cursor = max(cursor, cover_end)
            if cursor >= end:
                break
        if cursor < end:
            gaps.append((cursor, end))
    return gaps


def _overlap_seconds(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _coverage_gaps(
    *,
    diarization: list[dict[str, Any]],
    reliable_segments: list[dict[str, Any]],
    removed_segments: list[dict[str, Any]],
    duration: float | None = None,
    threshold_seconds: float = COVERAGE_GAP_THRESHOLD_SECONDS,
) -> list[dict[str, Any]]:
    speech_intervals = _merge_intervals(_intervals_from_segments(diarization))
    reliable_intervals = _merge_intervals(_intervals_from_segments(reliable_segments), max_gap=0.0)
    raw_gaps = _subtract_intervals(speech_intervals, reliable_intervals)
    gaps: list[dict[str, Any]] = []

    for start, end in raw_gaps:
        gap_duration = round(end - start, 3)
        if gap_duration < threshold_seconds:
            continue

        nearby_removed: list[dict[str, Any]] = []
        for removed in removed_segments:
            removed_start = _seconds(removed.get("start"))
            removed_end = _seconds(removed.get("end"))
            if removed_start is None or removed_end is None:
                continue
            if _overlap_seconds(start, end, removed_start, removed_end) <= 0 and min(
                abs(start - removed_end),
                abs(end - removed_start),
            ) > COVERAGE_GAP_MERGE_SECONDS:
                continue
            nearby_removed.append(
                {
                    "start": removed_start,
                    "end": removed_end,
                    "reason": removed.get("reason"),
                    "text": str(removed.get("text") or "").strip(),
                }
            )

        removed_reasons = {str(item.get("reason") or "") for item in nearby_removed}
        near_tail = bool(duration is not None and end >= max(duration - 5.0, 0.0))
        severity = "medium"
        if "prompt_contamination" in removed_reasons:
            severity = "high"
        elif "nonclinical_hallucination" in removed_reasons and near_tail:
            severity = "low"

        gaps.append(
            {
                "gap_id": f"gap-{len(gaps) + 1:03d}",
                "start": round(start, 3),
                "end": round(end, 3),
                "duration_seconds": gap_duration,
                "reason": "diarization_has_speech_but_no_reliable_asr_text",
                "severity": severity,
                "nearby_removed_segments": nearby_removed,
                "requires_human_confirmation": True,
            }
        )

    return gaps


def _offset_segments(segments: list[dict[str, Any]], offset_seconds: float) -> list[dict[str, Any]]:
    shifted: list[dict[str, Any]] = []
    for segment in _segments_for_json(segments):
        item = dict(segment)
        if item.get("start") is not None:
            item["start"] = round(float(item["start"]) + offset_seconds, 3)
        if item.get("end") is not None:
            item["end"] = round(float(item["end"]) + offset_seconds, 3)
        words = []
        for word in item.get("words") or []:
            shifted_word = dict(word)
            if shifted_word.get("start") is not None:
                shifted_word["start"] = round(float(shifted_word["start"]) + offset_seconds, 3)
            if shifted_word.get("end") is not None:
                shifted_word["end"] = round(float(shifted_word["end"]) + offset_seconds, 3)
            words.append(shifted_word)
        item["words"] = words
        shifted.append(item)
    return shifted


def _plain_text(segments: list[dict[str, Any]]) -> str:
    return " ".join(str(segment.get("text") or "").strip() for segment in segments).strip()


def _segments_for_json(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for segment in segments:
        words = []
        for word in segment.get("words") or []:
            words.append(
                {
                    "word": word.get("word"),
                    "start": _seconds(word.get("start")),
                    "end": _seconds(word.get("end")),
                    "speaker": word.get("speaker"),
                }
            )
        cleaned.append(
            {
                "start": _seconds(segment.get("start")),
                "end": _seconds(segment.get("end")),
                "speaker": segment.get("speaker"),
                "text": str(segment.get("text") or "").strip(),
                "words": words,
            }
        )
    return cleaned


def _diarization_rows(diarize_segments: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in diarize_segments.to_dict(orient="records"):
        rows.append(
            {
                "start": _seconds(row.get("start")),
                "end": _seconds(row.get("end")),
                "speaker": row.get("speaker"),
            }
        )
    return rows


def _rttm_lines(audio_path: Path, diarization: list[dict[str, Any]]) -> list[str]:
    file_id = audio_path.stem.replace(" ", "_")
    lines: list[str] = []
    for row in diarization:
        start = _seconds(row.get("start"))
        end = _seconds(row.get("end"))
        speaker = row.get("speaker") or "SPEAKER_UNKNOWN"
        if start is None or end is None or end <= start:
            continue
        duration = round(end - start, 3)
        lines.append(f"SPEAKER {file_id} 1 {start:.3f} {duration:.3f} <NA> <NA> {speaker} <NA> <NA>")
    return lines


class StageTimings:
    def __init__(self) -> None:
        self.started_at = time.perf_counter()
        self.stages: list[dict[str, Any]] = []

    def run(self, name: str, func: Any) -> Any:
        start = time.perf_counter()
        try:
            return func()
        finally:
            finished = time.perf_counter()
            self.stages.append(
                {
                    "name": name,
                    "seconds": round(finished - start, 3),
                    "started_offset_seconds": round(start - self.started_at, 3),
                    "finished_offset_seconds": round(finished - self.started_at, 3),
                }
            )

    def as_dict(self) -> dict[str, Any]:
        total = time.perf_counter() - self.started_at
        return {
            "total_seconds": round(total, 3),
            "stages": self.stages,
        }


def _dependency_imports() -> tuple[Any, Any, Any]:
    torch = importlib.import_module("torch")
    whisperx = importlib.import_module("whisperx")
    diarize_module = importlib.import_module("whisperx.diarize")
    return torch, whisperx, diarize_module.DiarizationPipeline


def _path_or_none(value: str | None) -> str | None:
    if not value:
        return None
    return str(Path(value).expanduser())


def _default_hf_home() -> str:
    return _path_or_none(os.environ.get("HF_HOME")) or str(Path.home() / ".cache" / "huggingface")


def _default_hf_hub_cache() -> str:
    return _path_or_none(os.environ.get("HUGGINGFACE_HUB_CACHE")) or str(Path(_default_hf_home()) / "hub")


def _default_torch_home() -> str:
    return _path_or_none(os.environ.get("TORCH_HOME")) or str(Path.home() / ".cache" / "torch")


def _initial_prompt() -> str:
    prompt_file = os.environ.get("WARD_STT_INITIAL_PROMPT_FILE")
    if prompt_file:
        path = Path(prompt_file).expanduser()
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    return os.environ.get("WARD_STT_INITIAL_PROMPT", DEFAULT_INITIAL_PROMPT).strip()


def _condition_on_previous_text() -> bool:
    value = os.environ.get("WARD_WHISPERX_CONDITION_ON_PREVIOUS_TEXT", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _transcribe_compat(model: Any, audio: Any, **kwargs: Any) -> Any:
    transcribe = getattr(model, "transcribe")
    remaining = dict(kwargs)
    try:
        signature = inspect.signature(transcribe)
    except (TypeError, ValueError):
        signature = None

    if signature is not None and not any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        remaining = {name: value for name, value in remaining.items() if name in signature.parameters}

    while True:
        try:
            return transcribe(audio, **remaining)
        except TypeError as exc:
            message = str(exc)
            match = re.search(r"unexpected keyword argument ['\"]([^'\"]+)['\"]", message)
            if not match:
                raise
            bad_key = match.group(1)
            if bad_key not in remaining:
                raise
            remaining = {name: value for name, value in remaining.items() if name != bad_key}


def _repo_cache_name(repo_id: str) -> str:
    return "models--" + repo_id.replace("/", "--")


def _repo_cache_root(repo_id: str, cache_dir: str | None) -> Path:
    root = Path(cache_dir).expanduser() if cache_dir else Path(_default_hf_hub_cache())
    return root / _repo_cache_name(repo_id)


def _dir_summary(path: Path) -> dict[str, Any]:
    exists = path.exists()
    summary: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
    }
    if not exists:
        return summary

    file_count = 0
    symlink_count = 0
    total_bytes = 0
    for child in path.rglob("*"):
        try:
            if child.is_symlink():
                symlink_count += 1
            if child.is_file():
                file_count += 1
                total_bytes += child.stat().st_size
        except OSError:
            continue
    summary.update(
        {
            "file_count": file_count,
            "symlink_count": symlink_count,
            "total_bytes": total_bytes,
            "refs": sorted(p.name for p in (path / "refs").iterdir()) if (path / "refs").is_dir() else [],
            "snapshots": sorted(p.name for p in (path / "snapshots").iterdir()) if (path / "snapshots").is_dir() else [],
            "locks_exist": (path.parent / ".locks" / path.name).exists(),
        }
    )
    return summary


def _alignment_plan(language_code: str | None) -> dict[str, Any]:
    if not language_code:
        return {"language": language_code, "model": None, "source": "unknown_language"}
    alignment_module = importlib.import_module("whisperx.alignment")
    torch_models = getattr(alignment_module, "DEFAULT_ALIGN_MODELS_TORCH", {})
    hf_models = getattr(alignment_module, "DEFAULT_ALIGN_MODELS_HF", {})
    if language_code in torch_models:
        return {"language": language_code, "model": torch_models[language_code], "source": "torchaudio"}
    if language_code in hf_models:
        return {"language": language_code, "model": hf_models[language_code], "source": "huggingface"}
    return {"language": language_code, "model": None, "source": "unavailable"}


def _cache_debug_info(
    *,
    model_name: str,
    diarization_model_name: str,
    cache_dir: str | None,
    detected_language: str | None,
    stt_language: str | None,
    stt_task: str,
    initial_prompt: str,
    condition_on_previous_text: bool,
    align_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    alignment = _alignment_plan(detected_language)
    alignment_model = alignment.get("model")
    hf_repos = {
        "whisper": f"Systran/faster-whisper-{model_name}",
        "diarization": diarization_model_name,
    }
    if alignment.get("source") == "huggingface" and alignment_model:
        hf_repos["alignment"] = str(alignment_model)

    return {
        "env": {
            "WARD_WHISPERX_CACHE_DIR": _path_or_none(os.environ.get("WARD_WHISPERX_CACHE_DIR")),
            "HF_HOME": _path_or_none(os.environ.get("HF_HOME")),
            "HUGGINGFACE_HUB_CACHE": _path_or_none(os.environ.get("HUGGINGFACE_HUB_CACHE")),
            "TRANSFORMERS_CACHE": _path_or_none(os.environ.get("TRANSFORMERS_CACHE")),
            "TORCH_HOME": _path_or_none(os.environ.get("TORCH_HOME")),
            "XDG_CACHE_HOME": _path_or_none(os.environ.get("XDG_CACHE_HOME")),
            "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
            "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
            "HF_TOKEN_CONFIGURED": bool(_hf_token()),
            "WARD_STT_TASK": os.environ.get("WARD_STT_TASK"),
            "WARD_STT_INITIAL_PROMPT_FILE": _path_or_none(os.environ.get("WARD_STT_INITIAL_PROMPT_FILE")),
        },
        "resolved_cache": {
            "whisperx_cache_dir_argument": _path_or_none(cache_dir),
            "hf_home_effective": _default_hf_home(),
            "hf_hub_cache_effective": _default_hf_hub_cache(),
            "torch_home_effective": _default_torch_home(),
        },
        "models": {
            "whisper": {
                "requested": model_name,
                "huggingface_repo_guess": hf_repos["whisper"],
            },
            "alignment": {
                **alignment,
                "metadata_type": (align_metadata or {}).get("type"),
                "metadata_language": (align_metadata or {}).get("language"),
            },
            "diarization": {
                "model": diarization_model_name,
            },
        },
        "stt_options": {
            "requested_language": stt_language,
            "task": stt_task,
            "condition_on_previous_text": condition_on_previous_text,
            "initial_prompt_configured": bool(initial_prompt),
            "initial_prompt_source": "file" if os.environ.get("WARD_STT_INITIAL_PROMPT_FILE") else "env_or_default",
        },
        "huggingface_cache_repos": {
            name: _dir_summary(_repo_cache_root(repo_id, cache_dir))
            for name, repo_id in hf_repos.items()
        },
    }


def _device() -> str:
    forced = os.environ.get("WARD_WHISPERX_DEVICE")
    if forced:
        return forced.strip().lower()

    import torch

    if torch.cuda.is_available():
        return "cuda"
    # WhisperX uses faster-whisper/CTranslate2 for transcription. On macOS this
    # path does not accept torch's "mps" device, so use CPU unless CUDA exists.
    return "cpu"


def transcribe_with_whisperx(
    audio_path: Path,
    output_dir: Path,
    model: str | None = None,
    language: str | None = None,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    diarization_model: str | None = None,
) -> dict[str, Any]:
    global _ACTIVE_TIMINGS
    timings = StageTimings()
    _ACTIVE_TIMINGS = timings
    token = timings.run("env_load_and_token", _hf_token)
    if not token:
        return {
            "success": False,
            "provider": "whisperx",
            "timing": timings.as_dict(),
            "error": (
                "HF_TOKEN is required for WhisperX speaker diarization. "
                "Create a Hugging Face read token, accept the pyannote diarization model terms, "
                "then add HF_TOKEN to ~/.hermes/.env or this repository's .env file."
            ),
        }

    output_dir.mkdir(parents=True, exist_ok=True)

    torch, whisperx, DiarizationPipeline = timings.run("dependency_imports", _dependency_imports)

    device = _device()
    compute_type = os.environ.get("WARD_WHISPERX_COMPUTE_TYPE") or ("float16" if device == "cuda" else "int8")
    batch_size = int(os.environ.get("WARD_WHISPERX_BATCH_SIZE", "4" if device == "cpu" else "16"))
    model_name = model or os.environ.get("WARD_WHISPERX_MODEL") or DEFAULT_MODEL
    diarize_model_name = diarization_model or os.environ.get("WARD_DIARIZATION_MODEL") or DEFAULT_DIARIZATION_MODEL
    cache_dir = os.environ.get("WARD_WHISPERX_CACHE_DIR")
    stt_language = language or os.environ.get("WARD_STT_LANGUAGE") or os.environ.get("HERMES_LOCAL_STT_LANGUAGE") or DEFAULT_LANGUAGE
    stt_task = os.environ.get("WARD_STT_TASK") or DEFAULT_TASK
    initial_prompt = _initial_prompt()
    condition_on_previous_text = _condition_on_previous_text()
    asr_options = {"initial_prompt": initial_prompt} if initial_prompt else None

    audio = timings.run("audio_load", lambda: whisperx.load_audio(str(audio_path)))
    whisper_model = timings.run(
        "whisper_model_load",
        lambda: whisperx.load_model(
            model_name,
            device,
            compute_type=compute_type,
            language=stt_language,
            task=stt_task,
            asr_options=asr_options,
            download_root=cache_dir,
        ),
    )
    result = timings.run(
        "whisper_transcribe",
        lambda: _transcribe_compat(
            whisper_model,
            audio,
            batch_size=batch_size,
            language=stt_language,
            task=stt_task,
            condition_on_previous_text=condition_on_previous_text,
        ),
    )
    detected_language = result.get("language")

    def _unload_whisper_model() -> None:
        nonlocal whisper_model
        del whisper_model
        if device == "cuda":
            torch.cuda.empty_cache()

    timings.run("whisper_model_unload", _unload_whisper_model)

    align_model, metadata = timings.run(
        "alignment_model_load",
        lambda: whisperx.load_align_model(language_code=detected_language, device=device, model_dir=cache_dir),
    )
    aligned = timings.run(
        "alignment",
        lambda: whisperx.align(
            result["segments"],
            align_model,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        ),
    )

    def _unload_alignment_model() -> None:
        nonlocal align_model
        del align_model
        if device == "cuda":
            torch.cuda.empty_cache()

    timings.run("alignment_model_unload", _unload_alignment_model)

    diarizer = timings.run(
        "diarization_model_load",
        lambda: DiarizationPipeline(model_name=diarize_model_name, token=token, device=device, cache_dir=cache_dir),
    )
    diarize_segments = timings.run(
        "diarization",
        lambda: diarizer(str(audio_path), min_speakers=min_speakers, max_speakers=max_speakers),
    )
    assigned = timings.run("speaker_assignment", lambda: whisperx.assign_word_speakers(diarize_segments, aligned))

    segments = timings.run("segments_json_prepare", lambda: _segments_for_json(assigned.get("segments") or []))
    segments, prompt_contamination_removed = timings.run(
        "prompt_contamination_filter",
        lambda: _filter_prompt_contamination_segments(segments),
    )
    segments, nonclinical_hallucination_removed = timings.run(
        "nonclinical_hallucination_filter",
        lambda: _filter_nonclinical_hallucination_segments(segments),
    )
    diarization = timings.run("diarization_json_prepare", lambda: _diarization_rows(diarize_segments))
    duration = max((row.get("end") or 0 for row in diarization), default=None)
    removed_segments = prompt_contamination_removed + nonclinical_hallucination_removed
    coverage_gaps = timings.run(
        "coverage_gap_audit",
        lambda: _coverage_gaps(
            diarization=diarization,
            reliable_segments=segments,
            removed_segments=removed_segments,
            duration=duration,
        ),
    )
    recovery_candidates: list[dict[str, Any]] = []
    recovery_errors: list[dict[str, Any]] = []
    recovery_gaps = [gap for gap in coverage_gaps if gap.get("severity") != "low"]
    if recovery_gaps:
        try:
            recovery_model = timings.run(
                "recovery_whisper_model_load",
                lambda: whisperx.load_model(
                    model_name,
                    device,
                    compute_type=compute_type,
                    language=stt_language,
                    task=stt_task,
                    asr_options=None,
                    download_root=cache_dir,
                ),
            )
            for gap in recovery_gaps[:MAX_RECOVERY_GAPS]:
                start = max(float(gap["start"]) - COVERAGE_GAP_BUFFER_SECONDS, 0.0)
                end = min(float(gap["end"]) + COVERAGE_GAP_BUFFER_SECONDS, len(audio) / SAMPLE_RATE)
                start_index = int(start * SAMPLE_RATE)
                end_index = int(end * SAMPLE_RATE)
                if end_index <= start_index:
                    continue

                def _recover_gap() -> dict[str, Any]:
                    recovered = _transcribe_compat(
                        recovery_model,
                        audio[start_index:end_index],
                        batch_size=batch_size,
                        language=stt_language,
                        task=stt_task,
                        condition_on_previous_text=condition_on_previous_text,
                    )
                    recovered_segments = _offset_segments(recovered.get("segments") or [], start)
                    recovered_segments, recovered_prompt_removed = _filter_prompt_contamination_segments(recovered_segments)
                    recovered_segments, recovered_hallucination_removed = _filter_nonclinical_hallucination_segments(recovered_segments)
                    return {
                        "segments": recovered_segments,
                        "removed": recovered_prompt_removed + recovered_hallucination_removed,
                    }

                try:
                    recovered_result = timings.run(f"recovery_transcribe_{gap['gap_id']}", _recover_gap)
                except Exception as exc:
                    recovery_errors.append({"gap_id": gap["gap_id"], "error": f"{type(exc).__name__}: {exc}"})
                    continue

                recovered_segments = recovered_result["segments"]
                recovered_text = _plain_text(recovered_segments)
                if not recovered_text:
                    recovery_errors.append({"gap_id": gap["gap_id"], "error": "empty recovery transcript"})
                    continue
                recovery_candidates.append(
                    {
                        "candidate_id": f"rec-{len(recovery_candidates) + 1:03d}",
                        "gap_id": gap["gap_id"],
                        "start": gap["start"],
                        "end": gap["end"],
                        "buffered_start": round(start, 3),
                        "buffered_end": round(end, 3),
                        "text": recovered_text,
                        "segments": recovered_segments,
                        "removed_segments": recovered_result["removed"],
                        "source": "local_retranscribe_no_initial_prompt",
                        "status": "candidate_requires_confirmation",
                        "requires_human_confirmation": True,
                    }
                )

            def _unload_recovery_model() -> None:
                nonlocal recovery_model
                del recovery_model
                if device == "cuda":
                    torch.cuda.empty_cache()

            timings.run("recovery_whisper_model_unload", _unload_recovery_model)
        except Exception as exc:
            recovery_errors.append({"gap_id": "all", "error": f"{type(exc).__name__}: {exc}"})

    speaker_transcript = timings.run("speaker_transcript_prepare", lambda: _speaker_text(segments))
    diarization_render = timings.run("diarization_render_prepare", lambda: _diarization_render(segments))
    transcript = speaker_transcript or _plain_text(segments)

    segments_path = output_dir / "segments.whisperx.json"
    diarization_path = output_dir / "diarization.segments.json"
    rttm_path = output_dir / "diarization.rttm"
    speaker_transcript_path = output_dir / "transcript.speaker.txt"
    diarization_render_path = output_dir / DIARIZATION_RENDER_FILE
    coverage_audit_path = output_dir / STT_COVERAGE_AUDIT_FILE
    recovery_candidates_path = output_dir / STT_RECOVERY_CANDIDATES_FILE
    timing_path = output_dir / TIMING_FILE
    debug_path = output_dir / DEBUG_FILE

    def _write_artifacts() -> None:
        segments_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        diarization_path.write_text(json.dumps(diarization, ensure_ascii=False, indent=2), encoding="utf-8")
        rttm_path.write_text("\n".join(_rttm_lines(audio_path, diarization)) + "\n", encoding="utf-8")
        speaker_transcript_path.write_text(transcript, encoding="utf-8")
        diarization_render_path.write_text(diarization_render, encoding="utf-8")
        coverage_audit_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "coverage_gaps": coverage_gaps,
                    "gap_count": len(coverage_gaps),
                    "threshold_seconds": COVERAGE_GAP_THRESHOLD_SECONDS,
                    "merge_seconds": COVERAGE_GAP_MERGE_SECONDS,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        recovery_candidates_path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "candidates": recovery_candidates,
                    "candidate_count": len(recovery_candidates),
                    "errors": recovery_errors,
                    "policy": "Candidates are not primary transcript text until accepted by manual confirmation or the job-level recovery quality gate.",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    timings.run("artifact_writes", _write_artifacts)
    timing = timings.as_dict()
    debug = _cache_debug_info(
        model_name=model_name,
        diarization_model_name=diarize_model_name,
        cache_dir=cache_dir,
        detected_language=detected_language,
        stt_language=stt_language,
        stt_task=stt_task,
        initial_prompt=initial_prompt,
        condition_on_previous_text=condition_on_previous_text,
        align_metadata=metadata,
    )
    debug["transcript_filter"] = {
        "prompt_contamination_removed_count": len(prompt_contamination_removed),
        "nonclinical_hallucination_removed_count": len(nonclinical_hallucination_removed),
        "removed_segments": removed_segments,
    }
    debug["coverage_audit"] = {
        "gap_count": len(coverage_gaps),
        "coverage_gaps": coverage_gaps,
    }
    debug["recovery_candidates"] = {
        "candidate_count": len(recovery_candidates),
        "errors": recovery_errors,
    }
    timing_path.write_text(json.dumps(timing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    debug_path.write_text(json.dumps(debug, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "success": True,
        "provider": "whisperx",
        "model": model_name,
        "language": detected_language,
        "requested_language": stt_language,
        "task": stt_task,
        "condition_on_previous_text": condition_on_previous_text,
        "initial_prompt_configured": bool(initial_prompt),
        "device": device,
        "compute_type": compute_type,
        "batch_size": batch_size,
        "duration": duration,
        "transcript": transcript,
        "timing": timing,
        "debug": debug,
        "transcript_filter": debug["transcript_filter"],
        "artifacts": {
            "segments_whisperx": segments_path.name,
            "diarization_segments": diarization_path.name,
            "diarization_rttm": rttm_path.name,
            "transcript_speaker": speaker_transcript_path.name,
            "diarization_render": diarization_render_path.name,
            "stt_coverage_audit": coverage_audit_path.name,
            "stt_recovery_candidates": recovery_candidates_path.name,
            "transcription_timing": timing_path.name,
            "transcription_debug": debug_path.name,
        },
        "diarization": {
            "enabled": True,
            "model": diarize_model_name,
            "segments_count": len(diarization),
            "min_speakers": min_speakers,
            "max_speakers": max_speakers,
            "warning": "Speaker labels are auxiliary only and must not be treated as patient identity.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ward WhisperX STT + diarization runner")
    parser.add_argument("audio_path")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model")
    parser.add_argument("--language")
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--diarization-model")
    args = parser.parse_args()

    try:
        result = transcribe_with_whisperx(
            Path(args.audio_path).expanduser().resolve(),
            Path(args.output_dir).expanduser().resolve(),
            model=args.model,
            language=args.language,
            min_speakers=args.min_speakers,
            max_speakers=args.max_speakers,
            diarization_model=args.diarization_model,
        )
    except Exception as exc:
        timing = _ACTIVE_TIMINGS.as_dict() if _ACTIVE_TIMINGS else None
        result = {"success": False, "provider": "whisperx", "error": f"{type(exc).__name__}: {exc}"}
        if timing:
            result["timing"] = timing
            try:
                output_dir = Path(args.output_dir).expanduser().resolve()
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / TIMING_FILE).write_text(
                    json.dumps(timing, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            except Exception:
                pass
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
