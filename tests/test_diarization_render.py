from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.stt_whisperx import _diarization_render


def test_diarization_render_splits_mixed_speaker_words_inside_one_asr_segment() -> None:
    transcript = _diarization_render(
        [
            {
                "start": 0.0,
                "end": 12.0,
                "speaker": "SPEAKER_00",
                "text": "病人說頭暈老師說先量血壓護理師說站起來心跳一百一",
                "words": [
                    {"word": "病人說頭暈", "start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"},
                    {"word": "老師說先量血壓", "start": 3.2, "end": 7.0, "speaker": "SPEAKER_01"},
                    {"word": "站起來心跳一百一", "start": 7.2, "end": 12.0, "speaker": "SPEAKER_00"},
                ],
            }
        ]
    )

    assert "## block-001 SPEAKER_00 00:00-00:03" in transcript
    assert "## block-002 SPEAKER_01 00:03-00:07" in transcript
    assert "## block-003 SPEAKER_00 00:07-00:12" in transcript
    assert "病人說頭暈" in transcript
    assert "老師說先量血壓" in transcript
    assert "站起來心跳一百一" in transcript
    assert "does not infer roles" in transcript


def test_diarization_render_falls_back_to_segment_text_without_words() -> None:
    transcript = _diarization_render(
        [
            {
                "start": 15.0,
                "end": 20.0,
                "speaker": "SPEAKER_02",
                "text": "明天再追蹤抽血。",
                "words": [],
            }
        ]
    )

    assert "## block-001 SPEAKER_02 00:15-00:20" in transcript
    assert "- source: segment_text" in transcript
    assert "明天再追蹤抽血。" in transcript


if __name__ == "__main__":
    test_diarization_render_splits_mixed_speaker_words_inside_one_asr_segment()
    test_diarization_render_falls_back_to_segment_text_without_words()
    print(json.dumps({"ok": True, "message": "diarization render tests passed"}))
