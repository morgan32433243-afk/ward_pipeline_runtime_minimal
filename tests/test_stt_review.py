from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ward_pipeline.stt_review as stt_review_module
from ward_pipeline.stt_review import stt_promote_approved, stt_review, stt_review_queue


@dataclass(frozen=True)
class TestConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    obsidian_vault_dir: Path
    stt_review_queue_dir: Path
    timezone: str = "Asia/Taipei"


def _write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_stt_review_writes_job_artifact_and_home_level_queue() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        job_id = "20260512_120000_review"
        job_dir = tmp / "output" / job_id
        job_dir.mkdir(parents=True)
        queue_dir = tmp / "ward_stt_review_queue"
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=tmp / "output",
            case_view_dir=tmp / "cases",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
            stt_review_queue_dir=queue_dir,
        )
        _write_json(job_dir / "state.json", {"job_id": job_id})
        (job_dir / "raw_transcript.txt").write_text("肝殼 and coffee tail\n", encoding="utf-8")
        (job_dir / "normalized_transcript.md").write_text(
            "先把家屬支開，等報告出來再跟個管室一起說明。\n有狀況再call我，先處理PCP。\n",
            encoding="utf-8",
        )
        _write_json(
            job_dir / "correction_log.json",
            [
                {
                    "original": "肝殼",
                    "corrected": "cough",
                    "confidence": "high",
                    "category": "symptoms",
                    "line": 1,
                }
            ],
        )
        _write_json(
            job_dir / "uncertain_terms.json",
            [
                {
                    "original": "美毒",
                    "candidate": "syphilis screen",
                    "confidence": "medium",
                    "category": "procedures",
                    "line": 2,
                    "context": "記得驗一下美毒",
                }
            ],
        )
        _write_json(
            job_dir / "transcription.debug.json",
            {
                "transcript_filter": {
                    "removed_segments": [
                        {
                            "start": 172.0,
                            "end": 181.0,
                            "speaker": "SPEAKER_00",
                            "text": "老師我等一下去幫你買美式咖啡",
                            "reason": "nonclinical_hallucination",
                        }
                    ]
                }
            },
        )

        result = stt_review(config, job_id)

        assert result["ok"] is True
        assert result["candidate_counts"]["medical_terms"] == 2
        assert result["candidate_counts"]["bedside_phrases"] == 5
        assert result["candidate_counts"]["tail_noise"] == 1
        review_path = job_dir / "stt_review_candidates.json"
        assert review_path.exists()
        review = json.loads(review_path.read_text(encoding="utf-8"))
        assert review["review_queue_dir"] == str(queue_dir)
        assert review["medical_term_candidates"][0]["original"] == "肝殼"
        assert {item["phrase"] for item in review["bedside_phrase_candidates"]} >= {"支開家屬", "報告出來", "個管室"}
        assert review["tail_noise_candidates"][0]["text"].endswith("美式咖啡")
        assert (queue_dir / "README.md").exists()

        medical_queue = yaml.safe_load((queue_dir / "stt_medical_term_candidates.yml").read_text(encoding="utf-8"))
        bedside_queue = yaml.safe_load((queue_dir / "stt_bedside_phrase_candidates.yml").read_text(encoding="utf-8"))
        tail_queue = yaml.safe_load((queue_dir / "stt_tail_noise_candidates.yml").read_text(encoding="utf-8"))
        assert len(medical_queue["items"]) == 2
        assert any(item["original"] == "肝殼" and item["status"] == "watch" for item in medical_queue["items"])
        assert len(bedside_queue["items"]) == 5
        assert any(item["phrase"] == "個管室" and item["status"] == "watch" for item in bedside_queue["items"])
        assert len(tail_queue["items"]) == 1
        assert tail_queue["items"][0]["status"] == "watch"

        summary = stt_review_queue(config, limit=2)
        assert summary["ok"] is True
        assert summary["review_queue_dir"] == str(queue_dir)
        assert len(summary["queues"]) == 3
        assert summary["queues"][1]["queue_type"] == "stt_bedside_phrase_candidates"
        assert summary["queues"][1]["status_counts"]["watch"] == 5
        assert len(summary["queues"][1]["top_items"]) == 2


def test_stt_promote_approved_merges_only_approved_items() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        queue_dir = tmp / "ward_stt_review_queue"
        queue_dir.mkdir(parents=True)
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=tmp / "output",
            case_view_dir=tmp / "cases",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
            stt_review_queue_dir=queue_dir,
        )
        medical_lexicon = tmp / "medical_lexicon.yml"
        bedside_rules = tmp / "stt_bedside_phrase_rules.yml"
        tail_rules = tmp / "stt_tail_noise_phrases.yml"
        medical_lexicon.write_text(
            yaml.safe_dump({"symptoms": []}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (queue_dir / "stt_medical_term_candidates.yml").write_text(
            yaml.safe_dump(
                {
                    "version": "1.0",
                    "queue_type": "stt_medical_term_candidates",
                    "items": [
                        {
                            "key": "med-approved",
                            "status": "approved",
                            "original": "肝殼",
                            "candidate": "cough",
                            "category": "symptoms",
                            "confidence": "high",
                        },
                        {
                            "key": "med-watch",
                            "status": "watch",
                            "original": "噪音",
                            "candidate": "noise",
                            "category": "symptoms",
                            "confidence": "high",
                        },
                        {
                            "key": "med-noop",
                            "status": "approved",
                            "original": "SpO2",
                            "candidate": "SpO2",
                            "category": "lab",
                            "confidence": "stt_warning",
                        },
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (queue_dir / "stt_bedside_phrase_candidates.yml").write_text(
            yaml.safe_dump(
                {
                    "version": "1.0",
                    "queue_type": "stt_bedside_phrase_candidates",
                    "items": [
                        {"key": "bed-approved", "status": "approved", "phrase": "白紙黑字", "category": "result_confirmation"},
                        {"key": "bed-watch", "status": "watch", "phrase": "先等等", "category": "management_decision"},
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        (queue_dir / "stt_tail_noise_candidates.yml").write_text(
            yaml.safe_dump(
                {
                    "version": "1.0",
                    "queue_type": "stt_tail_noise_candidates",
                    "items": [
                        {"key": "tail-approved", "status": "approved", "text": "幫你買美式咖啡"},
                        {"key": "tail-rejected", "status": "rejected", "text": "保留真實病句"},
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        original_medical = stt_review_module.MEDICAL_LEXICON_FILE
        original_bedside = stt_review_module.BEDSIDE_PHRASE_RULES_FILE
        original_tail = stt_review_module.TAIL_NOISE_PHRASES_FILE
        try:
            stt_review_module.MEDICAL_LEXICON_FILE = medical_lexicon
            stt_review_module.BEDSIDE_PHRASE_RULES_FILE = bedside_rules
            stt_review_module.TAIL_NOISE_PHRASES_FILE = tail_rules

            dry_run = stt_promote_approved(config, dry_run=True)
            assert dry_run["ok"] is True
            assert dry_run["dry_run"] is True
            assert dry_run["approved_counts"] == {"medical_terms": 2, "bedside_phrases": 1, "tail_noise": 1}
            assert not bedside_rules.exists()
            assert not tail_rules.exists()
            assert yaml.safe_load(medical_lexicon.read_text(encoding="utf-8")) == {"symptoms": []}

            result = stt_promote_approved(config)
            assert result["ok"] is True
            assert result["dry_run"] is False

            lexicon = yaml.safe_load(medical_lexicon.read_text(encoding="utf-8"))
            symptoms = lexicon["symptoms"]
            assert symptoms == [{"canonical": "cough", "confidence": "high", "variants": ["肝殼"]}]
            assert "labs" not in lexicon
            assert "噪音" not in str(lexicon)

            bedside = yaml.safe_load(bedside_rules.read_text(encoding="utf-8"))
            assert bedside["queue_type"] == "stt_bedside_phrase_rules"
            assert bedside["items"] == [
                {
                    "phrase": "白紙黑字",
                    "category": "result_confirmation",
                    "variants": ["白紙黑字"],
                    "source_queue_key": "bed-approved",
                }
            ]

            tail = yaml.safe_load(tail_rules.read_text(encoding="utf-8"))
            assert tail["queue_type"] == "stt_tail_noise_phrases"
            assert tail["items"] == [{"text": "幫你買美式咖啡", "source_queue_key": "tail-approved"}]
            assert "保留真實病句" not in str(tail)

            second = stt_promote_approved(config)
            assert second["results"]["medical_terms"]["skipped"][0]["reason"] == "already_present"
            assert second["results"]["bedside_phrases"]["skipped"][0]["reason"] == "already_present"
            assert second["results"]["tail_noise"]["skipped"][0]["reason"] == "already_present"
        finally:
            stt_review_module.MEDICAL_LEXICON_FILE = original_medical
            stt_review_module.BEDSIDE_PHRASE_RULES_FILE = original_bedside
            stt_review_module.TAIL_NOISE_PHRASES_FILE = original_tail


if __name__ == "__main__":
    test_stt_review_writes_job_artifact_and_home_level_queue()
    test_stt_promote_approved_merges_only_approved_items()
    print(json.dumps({"ok": True, "message": "stt review tests passed"}))
