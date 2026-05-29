from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline import jobs
import ward_pipeline.stt_review as stt_review_module


@dataclass(frozen=True)
class TestConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    obsidian_vault_dir: Path
    stt_review_queue_dir: Path
    timezone: str = "Asia/Taipei"


def test_stt_rule_sync_promotes_approved_items_and_writes_reports() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        output_dir = tmp / "output"
        queue_dir = tmp / "ward_stt_review_queue"
        job_id = "20260512_130000_sync"
        job_dir = output_dir / job_id
        job_dir.mkdir(parents=True)
        queue_dir.mkdir(parents=True)

        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=output_dir,
            case_view_dir=tmp / "cases",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
            stt_review_queue_dir=queue_dir,
        )

        medical_lexicon = tmp / "medical_lexicon.yml"
        bedside_rules = tmp / "stt_bedside_phrase_rules.yml"
        tail_rules = tmp / "stt_tail_noise_phrases.yml"
        medical_lexicon.write_text(yaml.safe_dump({"symptoms": []}, allow_unicode=True, sort_keys=False), encoding="utf-8")
        (queue_dir / "stt_medical_term_candidates.yml").write_text(
            yaml.safe_dump(
                {
                    "version": "1.0",
                    "queue_type": "stt_medical_term_candidates",
                    "items": [
                        {
                            "key": "approved-med",
                            "status": "approved",
                            "original": "肝殼",
                            "candidate": "cough",
                            "category": "symptoms",
                            "confidence": "high",
                        }
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (queue_dir / "stt_bedside_phrase_candidates.yml").write_text(
            yaml.safe_dump({"version": "1.0", "queue_type": "stt_bedside_phrase_candidates", "items": []}, sort_keys=False),
            encoding="utf-8",
        )
        (queue_dir / "stt_tail_noise_candidates.yml").write_text(
            yaml.safe_dump({"version": "1.0", "queue_type": "stt_tail_noise_candidates", "items": []}, sort_keys=False),
            encoding="utf-8",
        )

        original_medical = stt_review_module.MEDICAL_LEXICON_FILE
        original_bedside = stt_review_module.BEDSIDE_PHRASE_RULES_FILE
        original_tail = stt_review_module.TAIL_NOISE_PHRASES_FILE
        try:
            stt_review_module.MEDICAL_LEXICON_FILE = medical_lexicon
            stt_review_module.BEDSIDE_PHRASE_RULES_FILE = bedside_rules
            stt_review_module.TAIL_NOISE_PHRASES_FILE = tail_rules

            report = jobs._sync_stt_rules(config, job_id)

            assert report["ok"] is True
            assert report["promotion"]["approved_counts"]["medical_terms"] == 1
            lexicon = yaml.safe_load(medical_lexicon.read_text(encoding="utf-8"))
            assert lexicon["symptoms"] == [{"canonical": "cough", "confidence": "high", "variants": ["肝殼"]}]

            job_report = json.loads((job_dir / "stt_rule_sync.json").read_text(encoding="utf-8"))
            latest_report = json.loads((output_dir / "_stt_rule_sync" / "latest.json").read_text(encoding="utf-8"))
            assert job_report["job_id"] == job_id
            assert latest_report["job_id"] == job_id
        finally:
            stt_review_module.MEDICAL_LEXICON_FILE = original_medical
            stt_review_module.BEDSIDE_PHRASE_RULES_FILE = original_bedside
            stt_review_module.TAIL_NOISE_PHRASES_FILE = original_tail


if __name__ == "__main__":
    test_stt_rule_sync_promotes_approved_items_and_writes_reports()
    print(json.dumps({"ok": True, "message": "stt rule sync tests passed"}))
