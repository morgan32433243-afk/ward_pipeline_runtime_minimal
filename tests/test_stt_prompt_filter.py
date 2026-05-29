from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.stt_whisperx import (
    DEFAULT_INITIAL_PROMPT,
    _filter_prompt_contamination_segments,
    _is_prompt_contamination_text,
)


def test_default_initial_prompt_avoids_verbatim_instruction_sentence() -> None:
    assert "請忠實轉錄" not in DEFAULT_INITIAL_PROMPT
    assert "不要翻譯" not in DEFAULT_INITIAL_PROMPT
    assert "不要自行補上未聽到的藥名" not in DEFAULT_INITIAL_PROMPT


def test_prompt_contamination_filter_removes_verbatim_instruction_segment() -> None:
    segments = [
        {
            "start": 0.0,
            "end": 10.0,
            "speaker": "SPEAKER_00",
            "text": "病人最近覺得頭暈，稍微走快一點就喘不過氣。",
        },
        {
            "start": 29.29,
            "end": 37.79,
            "speaker": "SPEAKER_01",
            "text": "請忠實轉錄，不要翻譯，不要自行補上未聽到的藥名、劑量或檢驗數值。",
        },
    ]

    filtered, removed = _filter_prompt_contamination_segments(segments)

    assert len(filtered) == 1
    assert filtered[0]["text"].startswith("病人最近")
    assert len(removed) == 1
    assert removed[0]["reason"] == "prompt_contamination"
    assert removed[0]["start"] == 29.29


def test_prompt_contamination_filter_removes_lexicon_prompt_leakage() -> None:
    segments = [
        {
            "start": 29.0,
            "end": 50.0,
            "speaker": "SPEAKER_01",
            "text": "詞彙：fever, cough, dyspnea, pneumonia, hypertension, CRP, sputum culture。",
        }
    ]

    filtered, removed = _filter_prompt_contamination_segments(segments)

    assert filtered == []
    assert len(removed) == 1
    assert removed[0]["reason"] == "prompt_contamination"


def test_prompt_contamination_detector_ignores_clinical_text() -> None:
    assert not _is_prompt_contamination_text("請問病人有沒有翻身時頭暈或心悸？")
    assert not _is_prompt_contamination_text("藥名和劑量需要等病歷確認後再補。")


if __name__ == "__main__":
    test_default_initial_prompt_avoids_verbatim_instruction_sentence()
    test_prompt_contamination_filter_removes_verbatim_instruction_segment()
    test_prompt_contamination_filter_removes_lexicon_prompt_leakage()
    test_prompt_contamination_detector_ignores_clinical_text()
    print(json.dumps({"ok": True, "message": "stt prompt filter tests passed"}))
