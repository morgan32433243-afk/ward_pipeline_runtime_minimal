from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ward_pipeline.stt_whisperx as stt_whisperx_module
from ward_pipeline.stt_whisperx import (
    _filter_nonclinical_hallucination_segments,
    _filter_prompt_contamination_segments,
    _is_nonclinical_hallucination_text,
)


def test_nonclinical_hallucination_filter_removes_video_platform_tail() -> None:
    segments = [
        {
            "start": 120.0,
            "end": 130.0,
            "speaker": "SPEAKER_00",
            "text": "老師那我先排 EKG 跟抽血，等結果出來再回報。",
        },
        {
            "start": 151.0,
            "end": 154.0,
            "speaker": "SPEAKER_01",
            "text": "請不吝點讚、訂閱、分享、打賞支持明镜及點點栏目",
        },
    ]

    filtered, removed = _filter_nonclinical_hallucination_segments(segments)

    assert len(filtered) == 1
    assert filtered[0]["text"].startswith("老師")
    assert len(removed) == 1
    assert removed[0]["reason"] == "nonclinical_hallucination"
    assert removed[0]["start"] == 151.0


def test_nonclinical_hallucination_detector_ignores_clinical_share_context() -> None:
    assert not _is_nonclinical_hallucination_text("家屬分享病人最近食慾變差，水喝得比較少。")
    assert not _is_nonclinical_hallucination_text("請病人多喝水，明天追蹤抽血結果。")
    assert not _is_nonclinical_hallucination_text("老師提醒要跟家屬分享檢查計畫。")


def test_nonclinical_hallucination_filter_removes_repeated_detail_tail() -> None:
    assert _is_nonclinical_hallucination_text("詳情詳情詳情詳情詳情詳情")


def test_nonclinical_hallucination_filter_can_follow_prompt_filter() -> None:
    segments = [
        {
            "start": 29.29,
            "end": 37.79,
            "speaker": "SPEAKER_01",
            "text": "請忠實轉錄，不要翻譯，不要自行補上未聽到的藥名、劑量或檢驗數值。",
        },
        {
            "start": 51.0,
            "end": 70.0,
            "speaker": "SPEAKER_00",
            "text": "她最近看起來比較蒼白，先抽血看有沒有貧血。",
        },
        {
            "start": 151.0,
            "end": 154.0,
            "speaker": "SPEAKER_01",
            "text": "請不吝點讚、訂閱、分享、打賞支持明镜及點點栏目",
        },
    ]

    filtered, prompt_removed = _filter_prompt_contamination_segments(segments)
    filtered, hallucination_removed = _filter_nonclinical_hallucination_segments(filtered)

    assert len(filtered) == 1
    assert filtered[0]["text"].startswith("她最近")
    assert len(prompt_removed) == 1
    assert len(hallucination_removed) == 1


def test_nonclinical_hallucination_filter_removes_cafe_tail() -> None:
    segments = [
        {
            "start": 172.0,
            "end": 181.0,
            "speaker": "SPEAKER_00",
            "text": "老師我等一下去幫你買美式咖啡",
        }
    ]

    filtered, removed = _filter_nonclinical_hallucination_segments(segments)

    assert filtered == []
    assert len(removed) == 1
    assert removed[0]["reason"] == "nonclinical_hallucination"


def test_nonclinical_hallucination_filter_reads_promoted_tail_noise() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        promoted_path = Path(tmp_dir) / "stt_tail_noise_phrases.yml"
        promoted_path.write_text(
            yaml.safe_dump(
                {
                    "version": "1.0",
                    "queue_type": "stt_tail_noise_phrases",
                    "items": [{"text": "這一段是測試尾噪"}],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        original_path = stt_whisperx_module.TAIL_NOISE_PHRASES_FILE
        try:
            stt_whisperx_module.TAIL_NOISE_PHRASES_FILE = promoted_path
            filtered, removed = _filter_nonclinical_hallucination_segments(
                [
                    {
                        "start": 180.0,
                        "end": 184.0,
                        "speaker": "SPEAKER_00",
                        "text": "老師這一段是測試尾噪",
                    }
                ]
            )
        finally:
            stt_whisperx_module.TAIL_NOISE_PHRASES_FILE = original_path

    assert filtered == []
    assert len(removed) == 1
    assert removed[0]["reason"] == "nonclinical_hallucination"


if __name__ == "__main__":
    test_nonclinical_hallucination_filter_removes_video_platform_tail()
    test_nonclinical_hallucination_detector_ignores_clinical_share_context()
    test_nonclinical_hallucination_filter_removes_repeated_detail_tail()
    test_nonclinical_hallucination_filter_can_follow_prompt_filter()
    test_nonclinical_hallucination_filter_removes_cafe_tail()
    test_nonclinical_hallucination_filter_reads_promoted_tail_noise()
    print(json.dumps({"ok": True, "message": "stt hallucination filter tests passed"}))
