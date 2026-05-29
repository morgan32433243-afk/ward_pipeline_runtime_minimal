from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.jobs import accept_latest_review, auto_confirm_stt_recovery_candidates


@dataclass(frozen=True)
class TestConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    timezone: str = "Asia/Taipei"
    obsidian_vault_dir: Path | None = None


def _write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_accept_latest_confirms_all_uncertain_terms_without_redraft() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        job_id = "20260507_230000_accept"
        job_dir = tmp / "output" / job_id
        job_dir.mkdir(parents=True)
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=tmp / "output",
            case_view_dir=tmp / "case_view",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
        )
        _write_json(
            job_dir / "state.json",
            {
                "schema_version": "1.0",
                "job_id": job_id,
                "created_at": "2026-05-07T23:00:00+08:00",
                "updated_at": "2026-05-07T23:00:00+08:00",
                "status": "needs_review",
                "current_step": "manual_review",
                "steps": {},
                "needs_human_review": True,
                "review_reasons": [],
                "policy": {},
                "artifacts": {"state": "state.json"},
                "input": {"original_path": str(tmp / "audio.m4a")},
                "last_error": None,
            },
        )
        _write_json(
            job_dir / "uncertain_terms.json",
            [
                {
                    "original": "aspirin",
                    "candidate": "aspirin",
                    "reason": "possible STT medication term",
                    "confidence": "stt_warning",
                    "category": "medication",
                    "line": 1,
                    "requires_human_confirmation": True,
                }
            ],
        )

        result = accept_latest_review(config, redraft=False)
        confirmed_terms = json.loads((job_dir / "confirmed_terms.json").read_text(encoding="utf-8"))

        assert result["ok"] is True
        assert result["job_id"] == job_id
        assert result["redraft"] is None
        assert confirmed_terms[0]["original"] == "aspirin"
        assert confirmed_terms[0]["corrected"] == "aspirin"


def test_accept_latest_can_confirm_stt_recovery_candidate_without_redraft() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        job_id = "20260507_230001_accept_recovery"
        job_dir = tmp / "output" / job_id
        job_dir.mkdir(parents=True)
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=tmp / "output",
            case_view_dir=tmp / "case_view",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
        )
        _write_json(
            job_dir / "state.json",
            {
                "schema_version": "1.0",
                "job_id": job_id,
                "created_at": "2026-05-07T23:00:01+08:00",
                "updated_at": "2026-05-07T23:00:01+08:00",
                "status": "needs_review",
                "current_step": "manual_review",
                "steps": {},
                "needs_human_review": True,
                "review_reasons": [],
                "policy": {},
                "artifacts": {"state": "state.json"},
                "input": {"original_path": str(tmp / "audio.m4a")},
                "last_error": None,
            },
        )
        _write_json(job_dir / "uncertain_terms.json", [])
        _write_json(
            job_dir / "stt_recovery_candidates.json",
            {
                "version": "1.0",
                "candidate_count": 1,
                "candidates": [
                    {
                        "candidate_id": "rec-001",
                        "gap_id": "gap-001",
                        "start": 29.0,
                        "end": 50.0,
                        "text": "血壓一百零八六十幾，心跳九十幾。",
                        "source": "local_retranscribe_no_initial_prompt",
                        "requires_human_confirmation": True,
                    }
                ],
            },
        )

        result = accept_latest_review(
            config,
            redraft=False,
            stt_recovery_candidate_ids=["rec-001"],
        )
        confirmed_recovery = json.loads((job_dir / "confirmed_stt_recovery.json").read_text(encoding="utf-8"))

        assert result["ok"] is True
        assert result["stt_recovery"]["confirmed_candidate_ids"] == ["rec-001"]
        assert confirmed_recovery[0]["candidate_id"] == "rec-001"
        assert confirmed_recovery[0]["confirmation_method"] == "manual"
        assert "血壓" in confirmed_recovery[0]["text"]


def test_auto_confirm_stt_recovery_candidates_accepts_quality_gate_matches() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        job_id = "20260507_230002_auto_recovery"
        job_dir = tmp / "output" / job_id
        job_dir.mkdir(parents=True)
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=tmp / "output",
            case_view_dir=tmp / "case_view",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
        )
        _write_json(
            job_dir / "state.json",
            {
                "schema_version": "1.0",
                "job_id": job_id,
                "created_at": "2026-05-07T23:00:02+08:00",
                "updated_at": "2026-05-07T23:00:02+08:00",
                "status": "needs_review",
                "current_step": "manual_review",
                "steps": {},
                "needs_human_review": True,
                "review_reasons": [],
                "policy": {},
                "artifacts": {"state": "state.json"},
                "input": {"original_path": str(tmp / "audio.m4a")},
                "last_error": None,
            },
        )
        _write_json(
            job_dir / "stt_recovery_candidates.json",
            {
                "version": "1.0",
                "candidate_count": 2,
                "candidates": [
                    {
                        "candidate_id": "rec-001",
                        "gap_id": "gap-001",
                        "start": 29.0,
                        "end": 50.0,
                        "text": "血壓一百零八六十幾，心跳九十幾。",
                        "segments": [{"start": 30.0, "end": 39.0, "text": "血壓一百零八六十幾，心跳九十幾。"}],
                        "removed_segments": [],
                        "source": "local_retranscribe_no_initial_prompt",
                        "requires_human_confirmation": True,
                    },
                    {
                        "candidate_id": "rec-002",
                        "gap_id": "gap-002",
                        "start": 52.0,
                        "end": 56.0,
                        "text": "太短",
                        "segments": [{"start": 52.0, "end": 53.0, "text": "太短"}],
                        "removed_segments": [],
                        "source": "local_retranscribe_no_initial_prompt",
                        "requires_human_confirmation": True,
                    },
                ],
            },
        )

        result = auto_confirm_stt_recovery_candidates(config, job_id)
        confirmed_recovery = json.loads((job_dir / "confirmed_stt_recovery.json").read_text(encoding="utf-8"))
        state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))

        assert result["ok"] is True
        assert result["auto_confirmed_candidate_ids"] == ["rec-001"]
        assert confirmed_recovery[0]["candidate_id"] == "rec-001"
        assert confirmed_recovery[0]["confidence"] == "auto_confirmed_stt_recovery"
        assert confirmed_recovery[0]["confirmation_method"] == "auto_quality_gate"
        assert state["confirmed_stt_recovery"]["auto_confirmed_count"] == 1


if __name__ == "__main__":
    test_accept_latest_confirms_all_uncertain_terms_without_redraft()
    test_accept_latest_can_confirm_stt_recovery_candidate_without_redraft()
    test_auto_confirm_stt_recovery_candidates_accepts_quality_gate_matches()
    print(json.dumps({"ok": True, "message": "accept-latest tests passed"}))
