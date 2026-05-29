from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.jobs import _delivery_messages


def _write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_delivery_messages_summarize_confirmed_stt_recovery_without_raw_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        job_dir = Path(tmp)
        (job_dir / "soap_note.md").write_text("S\n- line\n", encoding="utf-8")
        _write_json(
            job_dir / "stt_recovery_candidates.json",
            {
                "version": "1.0",
                "candidate_count": 2,
                "candidates": [
                    {
                        "candidate_id": "rec-001",
                        "text": "confirmed one",
                        "requires_human_confirmation": True,
                    },
                    {
                        "candidate_id": "rec-002",
                        "text": "confirmed two",
                        "requires_human_confirmation": True,
                    },
                ],
            },
        )
        _write_json(
            job_dir / "confirmed_stt_recovery.json",
            [
                {
                    "candidate_id": "rec-001",
                    "text": "confirmed one",
                    "confirmation_method": "auto_quality_gate",
                },
                {
                    "candidate_id": "rec-002",
                    "text": "confirmed two",
                    "confirmation_method": "auto_quality_gate",
                },
            ],
        )

        messages = _delivery_messages(job_dir, "20260513_test")
        joined = "\n".join(messages)

        assert "STT recovery summary" in joined
        assert "Confirmed STT recovery candidates: 2" in joined
        assert "STT recovery candidates JSON" not in joined


if __name__ == "__main__":
    test_delivery_messages_summarize_confirmed_stt_recovery_without_raw_json()
    print(json.dumps({"ok": True, "message": "delivery message tests passed"}))
