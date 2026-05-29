from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline import jobs as jobs_module
from ward_pipeline.jobs import export_obsidian


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


def test_export_obsidian_writes_low_noise_note_from_soap_note_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "cases"
        log_dir = root / "logs"
        vault_dir = root / "vault"
        for path in (output_dir, case_view_dir, log_dir, vault_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_id = "20260506_161824_8a7b6c"
        job_dir = output_dir / job_id
        job_dir.mkdir()
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-06T16:18:24+08:00",
                "updated_at": "2026-05-06T16:22:19+08:00",
                "status": "delivered",
                "current_step": "delivery",
                "needs_human_review": True,
                "routing": {
                    "bed_id": "unknown-004",
                    "encounter_id": "20260506_bed-unknown-004_encounter-001",
                },
            },
        )
        (job_dir / "result.hermes.md").write_text(
            "S\n- Subjective item.\n\nO\n- Objective item.\n\nA\n- Assessment item.\n\nP\n- Plan item.\n\n需確認\n- Confirm item.\n",
            encoding="utf-8",
        )
        (job_dir / "soap_note.md").write_text(
            "S\n- Subjective item from soap_note.\n\nO\n- Objective item from soap_note.\n\nA\n- Assessment item from soap_note.\n\nP\n- Plan item from soap_note.\n",
            encoding="utf-8",
        )

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
            obsidian_vault_dir=vault_dir,
        )
        result = export_obsidian(config, job_id)

        assert result["ok"] is True
        assert "Clinical Drafts/Unsorted" in result["note_dir"]
        assert result["obsidian_route"]["status"].startswith("unsorted_")
        note_path = Path(result["note_path"])
        assert note_path.exists()
        content = note_path.read_text(encoding="utf-8")
        assert 'note_type: "ward_soap_draft"' in content
        assert 'job_id: "20260506_161824_8a7b6c"' in content
        assert 'created_at: "2026-05-06T16:18:24+08:00"' in content
        assert 'care_setting: "ward"' in content
        assert "diagnosis_topics: []" in content
        assert "problem_keywords: []" in content
        assert "teaching_topics: []" in content
        assert "paper_candidate: false" in content
        assert 'source_type: "soap_note.md"' in content
        assert "### Subjective" in content
        assert "- Subjective item from soap_note." in content
        assert "- Subjective item." not in content
        assert "## 需確認" in content
        assert "- None documented." in content
        assert "delivery.report.json" not in content


def test_export_obsidian_omits_hermes_routing_block_from_confirm_section() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "cases"
        log_dir = root / "logs"
        vault_dir = root / "vault"
        for path in (output_dir, case_view_dir, log_dir, vault_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_id = "20260521_211459_b0e5a4"
        job_dir = output_dir / job_id
        job_dir.mkdir()
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-21T21:14:59+08:00",
                "updated_at": "2026-05-21T21:20:00+08:00",
                "status": "needs_review",
                "current_step": "manual_review",
                "needs_human_review": True,
                "routing": {"bed_id": "unknown-009", "encounter_id": "encounter-009"},
            },
        )
        (job_dir / "result.hermes.md").write_text(
            """S
- Acute left eye redness.

O
- Vision preserved.

A
- Possible subconjunctival hemorrhage.

P
- Ophthalmology evaluation recommended.

需確認
- Confirm visual acuity.

## Routing
```json
{
  "primary_specialty": "ophthalmology",
  "diagnosis_topics": ["subconjunctival_hemorrhage"],
  "confidence": "high",
  "routing_rationale": "eye bleed needs ophthalmology"
}
```
""",
            encoding="utf-8",
        )

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
            obsidian_vault_dir=vault_dir,
        )
        result = export_obsidian(config, job_id)

        assert result["ok"] is True
        assert "Medicine/Ophthalmology" in result["note_dir"]
        content = Path(result["note_path"]).read_text(encoding="utf-8")
        assert 'service: "ophthalmology"' in content
        assert '- "subconjunctival_hemorrhage"' in content
        assert "- Confirm visual acuity." in content
        assert "primary_specialty" not in content
        assert "routing_rationale" not in content


def test_export_obsidian_populates_taxonomy_for_pcp_case() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "cases"
        log_dir = root / "logs"
        vault_dir = root / "vault"
        for path in (output_dir, case_view_dir, log_dir, vault_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_id = "20260511_233153_60057e"
        job_dir = output_dir / job_id
        job_dir.mkdir()
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-11T23:31:53+08:00",
                "updated_at": "2026-05-11T23:49:29+08:00",
                "status": "delivered",
                "current_step": "delivery",
                "needs_human_review": True,
                "routing": {
                    "bed_id": "unknown-001",
                    "encounter_id": "20260511_bed-unknown-001_encounter-001",
                },
            },
        )
        (job_dir / "result.hermes.md").write_text(
            "S\n- Fever, cough, and 10 kg weight loss.\n\nO\n- SpO2 91% on room air.\n- Bilateral interstitial infiltrates on chest X-ray.\n\nA\n- Highly suspicious for Pneumocystis jirovecii pneumonia (PCP).\n- HIV status under evaluation.\n\nP\n- Start TMP-SMX and prednisolone.\n- HIV combo test and CD4 are pending.\n- Defer bronchoscopy for now.\n",
            encoding="utf-8",
        )

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
            obsidian_vault_dir=vault_dir,
        )
        result = export_obsidian(config, job_id)

        assert result["ok"] is True
        assert result["classification"]["ok"] is True
        assert result["obsidian_route"]["status"] == "auto_routed_by_taxonomy"
        assert "Medicine/Infectious_Disease" in result["note_dir"]
        assert Path(result["classification"]["path"]).name == "classification.json"
        classification = json.loads(Path(result["classification"]["path"]).read_text(encoding="utf-8"))
        assert classification["primary_specialty"] == "infectious_disease"
        key_insights_path = Path(result["key_insights_path"])
        assert key_insights_path.exists()
        key_insights = json.loads(key_insights_path.read_text(encoding="utf-8"))
        assert key_insights["source"] == "yaml_deterministic"
        assert key_insights["primary_specialty"] == "infectious_disease"
        assert key_insights["suggested_obsidian_folder"] == "Medicine/Infectious_Disease"
        assert key_insights["follow_up_needed"] is True
        assert "diagnostic-follow-up" in key_insights["follow_up_type"]
        content = Path(result["note_path"]).read_text(encoding="utf-8")
        assert 'service: "infectious_disease"' in content
        assert 'suggested_obsidian_folder: "Medicine/Infectious_Disease"' in content
        assert 'diagnosis_topics:' in content
        assert '- "pcp"' in content
        assert '- "hiv"' in content
        assert '- "hypoxemia"' in content
        assert '- "pneumonia"' in content
        assert 'problem_keywords:' in content
        assert '- "fever"' in content
        assert '- "cough"' in content
        assert '- "weight loss"' in content
        assert 'teaching_topics:' in content
        assert '- "opportunistic pneumonia workup"' in content
        assert '- "empiric PCP treatment"' in content
        assert 'follow_up_needed: true' in content
        assert '- "test-result-follow-up"' in content


def test_export_obsidian_infers_neurology_service_for_stroke_case() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "cases"
        log_dir = root / "logs"
        vault_dir = root / "vault"
        for path in (output_dir, case_view_dir, log_dir, vault_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_id = "20260506_161824_8a7b6c"
        job_dir = output_dir / job_id
        job_dir.mkdir()
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-06T16:18:24+08:00",
                "updated_at": "2026-05-06T16:22:19+08:00",
                "status": "delivered",
                "current_step": "delivery",
                "needs_human_review": True,
                "routing": {
                    "bed_id": "unknown-004",
                    "encounter_id": "20260506_bed-unknown-004_encounter-001",
                },
            },
        )
        (job_dir / "result.hermes.md").write_text(
            "S\n- 65-year-old patient admitted for acute ischemic stroke.\n\nO\n- NIHSS improved from 12 to 3.\n\nA\n- Left MCA infarction.\n\nP\n- Continue rehab.\n",
            encoding="utf-8",
        )

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
            obsidian_vault_dir=vault_dir,
        )
        result = export_obsidian(config, job_id)
        content = Path(result["note_path"]).read_text(encoding="utf-8")
        assert result["classification"]["ok"] is True
        assert Path(result["classification"]["path"]).exists()
        assert 'service: "neurology"' in content


def test_export_obsidian_falls_back_when_yaml_classification_fails() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "cases"
        log_dir = root / "logs"
        vault_dir = root / "vault"
        for path in (output_dir, case_view_dir, log_dir, vault_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_id = "20260517_080000_fallback"
        job_dir = output_dir / job_id
        job_dir.mkdir()
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-17T08:00:00+08:00",
                "updated_at": "2026-05-17T08:05:00+08:00",
                "status": "delivered",
                "current_step": "delivery",
                "needs_human_review": True,
                "routing": {"bed_id": "unknown-002", "encounter_id": "encounter-002"},
            },
        )
        (job_dir / "result.hermes.md").write_text(
            "S\n- Dyspnea.\n\nO\n- Pulmonary edema.\n\nA\n- Heart failure.\n\nP\n- Diuresis.\n",
            encoding="utf-8",
        )

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
            obsidian_vault_dir=vault_dir,
        )
        original_builder = jobs_module.build_classification_json
        try:
            def fail_classifier(_: str) -> dict:
                raise RuntimeError("taxonomy unavailable")

            jobs_module.build_classification_json = fail_classifier
            result = export_obsidian(config, job_id)
        finally:
            jobs_module.build_classification_json = original_builder

        assert result["ok"] is True
        assert result["classification"]["ok"] is False
        assert result["obsidian_route"]["status"] == "unsorted_classification_failed"
        assert "Clinical Drafts/Unsorted" in result["note_dir"]
        assert "taxonomy unavailable" in result["classification"]["error"]
        assert not (job_dir / "classification.json").exists()
        key_insights_path = Path(result["key_insights_path"])
        assert key_insights_path.exists()
        key_insights = json.loads(key_insights_path.read_text(encoding="utf-8"))
        assert key_insights["source"] == "fallback_lightweight"
        assert key_insights["primary_specialty"] == ""
        assert key_insights["suggested_obsidian_folder"] == ""
        content = Path(result["note_path"]).read_text(encoding="utf-8")
        assert 'service: "cardiology"' in content
        assert 'source_type: "result.hermes.md"' in content


def test_export_obsidian_includes_openevidence_narrative_when_available() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        output_dir = root / "output"
        case_view_dir = root / "cases"
        log_dir = root / "logs"
        vault_dir = root / "vault"
        for path in (output_dir, case_view_dir, log_dir, vault_dir):
            path.mkdir(parents=True, exist_ok=True)

        job_id = "20260521_182501_9bce00"
        job_dir = output_dir / job_id
        job_dir.mkdir()
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-21T18:25:01+08:00",
                "updated_at": "2026-05-21T19:55:25+08:00",
                "status": "delivered",
                "current_step": "delivery",
                "needs_human_review": True,
                "routing": {"bed_id": "unknown-001", "encounter_id": "encounter-001"},
            },
        )
        (job_dir / "result.hermes.md").write_text(
            "S\n- Fever.\n\nO\n- CXR infiltrates.\n\nA\n- Infection.\n\nP\n- Start antibiotics.\n",
            encoding="utf-8",
        )
        _write_json(
            job_dir / "literature_summary.json",
            {
                "source_count": 1,
                "key_points": ["Test point"],
                "evidence_items": [],
            },
        )
        (job_dir / "openevidence_narrative.md").write_text(
            "# OpenEvidence Narrative\n\n- Query: `infection guideline`\n\nNarrative body text.\n",
            encoding="utf-8",
        )

        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=output_dir,
            case_view_dir=case_view_dir,
            log_dir=log_dir,
            obsidian_vault_dir=vault_dir,
        )
        result = export_obsidian(config, job_id)
        content = Path(result["note_path"]).read_text(encoding="utf-8")
        assert "## Literature Summary" in content
        assert "### OpenEvidence Narrative" in content
        assert "Narrative body text." in content


if __name__ == "__main__":
    test_export_obsidian_writes_low_noise_note_from_soap_note_artifact()
    test_export_obsidian_omits_hermes_routing_block_from_confirm_section()
    test_export_obsidian_populates_taxonomy_for_pcp_case()
    test_export_obsidian_infers_neurology_service_for_stroke_case()
    test_export_obsidian_falls_back_when_yaml_classification_fails()
    test_export_obsidian_includes_openevidence_narrative_when_available()
    print(json.dumps({"ok": True, "message": "export obsidian tests passed"}))
