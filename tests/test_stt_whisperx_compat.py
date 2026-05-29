from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.stt_whisperx import _transcribe_compat


class _LegacyTranscriber:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, audio, *, batch_size=None, language=None, task=None):  # noqa: ANN001
        self.calls.append(
            {
                "audio": audio,
                "batch_size": batch_size,
                "language": language,
                "task": task,
            }
        )
        return {"ok": True}


class _ModernTranscriber:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):  # noqa: ANN001
        self.calls.append({"audio": audio, "kwargs": dict(kwargs)})
        return {"ok": True}


class _FlakyTranscriber:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):  # noqa: ANN001
        self.calls.append({"audio": audio, "kwargs": dict(kwargs)})
        if "condition_on_previous_text" in kwargs:
            raise TypeError("got an unexpected keyword argument 'condition_on_previous_text'")
        return {"ok": True}


def test_transcribe_compat_filters_unsupported_kwargs() -> None:
    transcriber = _LegacyTranscriber()

    result = _transcribe_compat(
        transcriber,
        "audio-buffer",
        batch_size=4,
        language="zh",
        task="transcribe",
        condition_on_previous_text=False,
    )

    assert result == {"ok": True}
    assert transcriber.calls == [
        {"audio": "audio-buffer", "batch_size": 4, "language": "zh", "task": "transcribe"}
    ]


def test_transcribe_compat_preserves_kwargs_when_supported() -> None:
    transcriber = _ModernTranscriber()

    result = _transcribe_compat(
        transcriber,
        "audio-buffer",
        batch_size=8,
        language="zh",
        task="transcribe",
        condition_on_previous_text=True,
    )

    assert result == {"ok": True}
    assert transcriber.calls == [
        {
            "audio": "audio-buffer",
            "kwargs": {
                "batch_size": 8,
                "language": "zh",
                "task": "transcribe",
                "condition_on_previous_text": True,
            },
        }
    ]


def test_transcribe_compat_retries_without_unsupported_keyword() -> None:
    transcriber = _FlakyTranscriber()

    result = _transcribe_compat(
        transcriber,
        "audio-buffer",
        batch_size=8,
        language="zh",
        task="transcribe",
        condition_on_previous_text=False,
    )

    assert result == {"ok": True}
    assert transcriber.calls == [
        {
            "audio": "audio-buffer",
            "kwargs": {
                "batch_size": 8,
                "language": "zh",
                "task": "transcribe",
                "condition_on_previous_text": False,
            },
        },
        {
            "audio": "audio-buffer",
            "kwargs": {
                "batch_size": 8,
                "language": "zh",
                "task": "transcribe",
            },
        },
    ]


if __name__ == "__main__":
    test_transcribe_compat_filters_unsupported_kwargs()
    test_transcribe_compat_preserves_kwargs_when_supported()
    test_transcribe_compat_retries_without_unsupported_keyword()
    print("stt whisperx compat tests passed")
