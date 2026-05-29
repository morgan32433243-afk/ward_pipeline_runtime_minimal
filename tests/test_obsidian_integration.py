from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline import jobs


@dataclass(frozen=True)
class TestConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    obsidian_vault_dir: Path
    timezone: str = "Asia/Taipei"


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_run_can_optionally_export_obsidian_note() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "cases"
        log_dir = root / "logs"
        vault_dir = root / "vault"
        for path in (output_dir, case_view_dir, log_dir, vault_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_id = "20260507_100000_abcd12"
        job_dir = output_dir / job_id
        job_dir.mkdir()
        _write_json(
            job_dir / "state.json",
            {
                "schema_version": "1.0",
                "job_id": job_id,
                "created_at": "2026-05-07T10:00:00+08:00",
                "updated_at": "2026-05-07T10:00:00+08:00",
                "status": "needs_review",
                "current_step": "transcribe",
                "input": {},
                "steps": {
                    "ingest": "done",
                    "normalize": "done",
                    "transcribe": "done",
                    "diarize": "done",
                    "clinical_extract": "pending",
                    "soap_generate": "pending",
                    "literature": "pending",
                    "delivery": "blocked",
                },
                "needs_human_review": True,
                "review_reasons": [],
                "policy": {
                    "external_llm_allowed": False,
                    "discord_allowed": False,
                    "requires_local_only": True,
                },
                "artifacts": {
                    "transcript_auto": "transcript.manual.txt",
                },
                "last_error": None,
                "routing": {
                    "bed_id": "unknown-001",
                    "encounter_id": "20260507_bed-unknown-001_encounter-001",
                },
            },
        )
        (job_dir / "transcript.manual.txt").write_text("Patient says cough.\n", encoding="utf-8")

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
            obsidian_vault_dir=vault_dir,
        )

        original_subprocess_run = jobs.subprocess.run
        original_plan_literature_queries = jobs.plan_literature_queries
        try:
            jobs.plan_literature_queries = lambda transcript, **kwargs: {
                "selected_scenarios": [],
                "search_targets": [],
                "clinical_question": "",
            }

            class FakeCompleted:
                returncode = 0
                stdout = "S\n- Subjective item.\n\nO\n- Objective item.\n\nA\n- Assessment item.\n\nP\n- Plan item.\n\n需確認\n- Confirm item.\n"
                stderr = ""

            jobs.subprocess.run = lambda *args, **kwargs: FakeCompleted()

            result = jobs.run(
                config,
                job_id,
                allow_external_llm=True,
                export_obsidian_note=True,
            )
            assert result["ok"] is True
            assert result["literature_auto_enrich"]["action"] == "literature-enrich"
            assert result["literature_auto_enrich"]["status"] in {"summarized", "retrieval_empty", "needs_clinical_question"}
            assert result["literature_auto_enrich"].get("auto_trigger_warning") == "skip_no_search_targets"
            assert result["obsidian_export"]["ok"] is True
            note_path = Path(result["obsidian_export"]["note_path"])
            assert note_path.exists()
            content = note_path.read_text(encoding="utf-8")
            assert 'draft_status: "draft"' in content
            assert "follow_up_type: []" in content
            assert "### Subjective" in content
            assert "- Subjective item." in content
            assert "## Literature Summary" in content
        finally:
            jobs.subprocess.run = original_subprocess_run
            jobs.plan_literature_queries = original_plan_literature_queries


if __name__ == "__main__":
    test_run_can_optionally_export_obsidian_note()
    print(json.dumps({"ok": True, "message": "obsidian integration tests passed"}))
