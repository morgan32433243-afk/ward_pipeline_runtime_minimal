from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.stt_whisperx import _coverage_gaps


def test_coverage_gap_detects_speech_without_reliable_asr_text() -> None:
    diarization = [
        {"start": 30.0, "end": 40.0, "speaker": "SPEAKER_00"},
        {"start": 40.5, "end": 50.0, "speaker": "SPEAKER_00"},
    ]
    reliable_segments = [
        {"start": 0.0, "end": 28.0, "text": "clinical text"},
        {"start": 51.0, "end": 70.0, "text": "clinical text"},
    ]

    gaps = _coverage_gaps(diarization=diarization, reliable_segments=reliable_segments, removed_segments=[])

    assert len(gaps) == 1
    assert gaps[0]["start"] == 30.0
    assert gaps[0]["end"] == 50.0
    assert gaps[0]["severity"] == "medium"


def test_coverage_gap_marks_prompt_contamination_nearby_as_high_severity() -> None:
    gaps = _coverage_gaps(
        diarization=[{"start": 29.0, "end": 50.0, "speaker": "SPEAKER_01"}],
        reliable_segments=[{"start": 51.0, "end": 70.0, "text": "clinical text"}],
        removed_segments=[
            {
                "start": 29.2,
                "end": 37.8,
                "text": "請忠實轉錄，不要翻譯，不要自行補上未聽到的藥名、劑量或檢驗數值。",
                "reason": "prompt_contamination",
            }
        ],
    )

    assert len(gaps) == 1
    assert gaps[0]["severity"] == "high"
    assert gaps[0]["nearby_removed_segments"][0]["reason"] == "prompt_contamination"


def test_coverage_gap_ignores_short_uncovered_interval() -> None:
    gaps = _coverage_gaps(
        diarization=[{"start": 10.0, "end": 13.0, "speaker": "SPEAKER_00"}],
        reliable_segments=[],
        removed_segments=[],
    )

    assert gaps == []


if __name__ == "__main__":
    test_coverage_gap_detects_speech_without_reliable_asr_text()
    test_coverage_gap_marks_prompt_contamination_nearby_as_high_severity()
    test_coverage_gap_ignores_short_uncovered_interval()
    print(json.dumps({"ok": True, "message": "stt coverage audit tests passed"}))
