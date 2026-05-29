from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.jobs import _retention_path_protected, retention_dry_run


@dataclass(frozen=True)
class TestConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    timezone: str = "Asia/Taipei"


def _set_old_mtime(path: Path, *, days_old: int = 30) -> None:
    ts = time.time() - (days_old * 86400)
    os.utime(path, (ts, ts), follow_symlinks=False)


def test_retention_dry_run_reports_only_policy_candidates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "ward_cases"
        log_dir = root / "logs"
        for path in (output_dir, case_view_dir, log_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_dir = output_dir / "20260101_000000_abcd12"
        job_dir.mkdir()
        old_delete = job_dir / "state.json"
        old_delete.write_text("{}\n", encoding="utf-8")
        old_keep = job_dir / "result.hermes.md"
        old_keep.write_text("draft\n", encoding="utf-8")
        old_soap_note = job_dir / "soap_note.md"
        old_soap_note.write_text("soap note\n", encoding="utf-8")
        old_glob = job_dir / "transcription.debug.json"
        old_glob.write_text("{}\n", encoding="utf-8")
        old_dir = job_dir / "stt_compare"
        old_dir.mkdir()
        old_dir_file = old_dir / "note.txt"
        old_dir_file.write_text("x\n", encoding="utf-8")

        recent_delete = job_dir / "delivery.report.json"
        recent_delete.write_text("{}\n", encoding="utf-8")

        case_artifacts = case_view_dir / "2026-01-01_bed-unknown-001" / "part-001" / "artifacts"
        case_artifacts.mkdir(parents=True)
        (case_artifacts / "review_summary.md").write_text("summary\n", encoding="utf-8")

        for path in (old_delete, old_keep, old_soap_note, old_glob, old_dir, case_artifacts):
            _set_old_mtime(path)

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
        )
        result = retention_dry_run(config, age_days=14, limit=50)

        assert result["ok"] is True
        assert result["action"] == "retention-dry-run"
        candidate_paths = {Path(item["path"]).name for item in result["candidates"]}
        assert "state.json" in candidate_paths
        assert "transcription.debug.json" in candidate_paths
        assert "stt_compare" in candidate_paths
        assert "artifacts" in candidate_paths
        assert "result.hermes.md" not in candidate_paths
        assert "soap_note.md" not in candidate_paths
        assert "delivery.report.json" not in candidate_paths
        groups = {item["group"]: item for item in result["groups"]}
        assert "20260101_000000_abcd12" in groups
        assert "2026-01-01_bed-unknown-001" in groups


def test_retention_guard_protects_workflow_and_hermes_paths() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert _retention_path_protected(repo_root / "ward_pipeline" / "jobs.py")
    assert _retention_path_protected(repo_root / "README.md")
    assert _retention_path_protected(Path.home() / ".hermes" / "SOUL.md")
    assert _retention_path_protected(Path.home() / ".codex" / "memories" / "ward_workflow_progress.md")
    assert not _retention_path_protected(repo_root / "data" / "output" / "20260101_000000_abcd12" / "state.json")


def test_retention_dry_run_can_write_full_review_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "ward_cases"
        log_dir = root / "logs"
        for path in (output_dir, case_view_dir, log_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_dir = output_dir / "20260101_000000_abcd12"
        job_dir.mkdir()
        for name in ("state.json", "input.meta.json", "prompt.chatgpt.md"):
            candidate = job_dir / name
            candidate.write_text("{}\n", encoding="utf-8")
            _set_old_mtime(candidate)

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
        )
        result = retention_dry_run(config, age_days=14, limit=1, write_artifact=True)

        assert result["review_artifact"]
        assert result["truncated"] is True
        assert len(result["candidates"]) == 1
        assert result["approval_gate"]["status"] == "pending_review"
        assert result["approval_gate"]["candidate_manifest_sha256"]

        artifact = Path(result["review_artifact"])
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["candidate_count"] == 3
        assert len(payload["candidates"]) == 3
        assert payload["truncated"] is False
        assert payload["candidate_manifest_sha256"] == result["candidate_manifest_sha256"]
        assert payload["approval_gate"]["required_before_deletion"] is True


if __name__ == "__main__":
    test_retention_dry_run_reports_only_policy_candidates()
    test_retention_guard_protects_workflow_and_hermes_paths()
    test_retention_dry_run_can_write_full_review_artifact()
    print(json.dumps({"ok": True, "message": "retention dry-run tests passed"}))
