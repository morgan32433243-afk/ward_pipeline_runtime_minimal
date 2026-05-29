from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.clinical_facts import extract_clinical_facts
from ward_pipeline.hermes_runtime import prepare_hermes_env
from ward_pipeline import jobs
from ward_pipeline.llm_normalizer import run_llm_normalization
from ward_pipeline.soap_drafter import draft_soap_note
from ward_pipeline.soap_validator import validate_soap_note


@dataclass(frozen=True)
class TestConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    timezone: str = "Asia/Taipei"


def _write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _base_job(tmp_path: Path) -> tuple[TestConfig, Path, dict]:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    config = TestConfig(
        incoming_dir=tmp_path / "incoming",
        output_dir=tmp_path,
        case_view_dir=tmp_path / "case_view",
        log_dir=tmp_path / "logs",
    )
    state = {
        "job_id": "test_job",
        "status": "needs_review",
        "current_step": "test",
        "steps": {},
        "policy": {},
        "artifacts": {},
        "review_reasons": [],
        "needs_human_review": True,
    }
    (job_dir / "raw_transcript.txt").write_text("Patient reports cough. No fever documented.\n", encoding="utf-8")
    (job_dir / "normalized_transcript.md").write_text("Patient reports cough. No fever documented.\n", encoding="utf-8")
    _write_json(job_dir / "correction_log.json", [])
    _write_json(job_dir / "confirmed_terms.json", [])
    _write_json(job_dir / "uncertain_terms.json", [])
    return config, job_dir, state


def _write_valid_fact_and_soap(job_dir: Path) -> None:
    _write_json(
        job_dir / "clinical_facts.json",
        {
            "version": "1.0",
            "job_id": "test_job",
            "status": "ok",
            "facts": [
                {
                    "fact_id": "f1",
                    "type": "symptom",
                    "text": "Patient reports cough.",
                    "certainty": "confirmed",
                    "risk_level": "low",
                    "source_refs": [{"artifact": "raw_transcript.txt", "line_start": 1, "line_end": 1}],
                    "allowed_in_final_soap": True,
                }
            ],
            "excluded_facts": [],
            "summary": {"facts": 1, "excluded_facts": 0, "high_risk_excluded": 0},
        },
    )
    _write_json(
        job_dir / "soap_note.json",
        {
            "version": "1.0",
            "job_id": "test_job",
            "status": "needs_review",
            "sections": {
                "subjective": {"text": "Patient reports cough.", "source_fact_ids": ["f1"]},
                "objective": {"text": "Not documented in transcript.", "source_fact_ids": []},
                "assessment": {"text": "Not documented in transcript.", "source_fact_ids": []},
                "plan": {"text": "Not documented in transcript.", "source_fact_ids": []},
            },
            "warnings": [],
            "blocking_reasons": [],
        },
    )


def _with_policy_path(path: Path):
    class _PolicyEnv:
        def __enter__(self) -> None:
            self.previous = os.environ.get("AUTO_SOAP_POLICY_PATH")
            os.environ["AUTO_SOAP_POLICY_PATH"] = str(path)

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            if self.previous is None:
                os.environ.pop("AUTO_SOAP_POLICY_PATH", None)
            else:
                os.environ["AUTO_SOAP_POLICY_PATH"] = self.previous

    return _PolicyEnv()


def _with_env(name: str, value: str):
    class _Env:
        previous: str | None = None

        def __enter__(self) -> None:
            self.previous = os.environ.get(name)
            os.environ[name] = value

        def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
            if self.previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = self.previous

    return _Env()


def _write_hermes_home(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "logs").mkdir(parents=True, exist_ok=True)
    (path / "auth.json").write_text('{"providers": {}}\n', encoding="utf-8")
    (path / "config.yaml").write_text("model: test\n", encoding="utf-8")
    (path / ".env").write_text("TEST_ENV=1\n", encoding="utf-8")


def test_prepare_hermes_env_uses_requested_home_when_usable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config, _, _ = _base_job(root)
        hermes_home = root / "hermes"
        _write_hermes_home(hermes_home)

        with _with_env("HERMES_HOME", str(hermes_home)):
            env = prepare_hermes_env(config)

        assert env["HERMES_HOME"] == str(hermes_home)
        assert (hermes_home / "logs" / "agent.log").exists()


def test_prepare_hermes_env_falls_back_when_requested_home_log_is_unusable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config, _, _ = _base_job(root)
        hermes_home = root / "hermes"
        _write_hermes_home(hermes_home)
        (hermes_home / "logs" / "agent.log").unlink(missing_ok=True)
        (hermes_home / "logs" / "agent.log").mkdir()

        with _with_env("HERMES_HOME", str(hermes_home)):
            env = prepare_hermes_env(config)

        fallback_home = config.log_dir.parent / "hermes_runtime_home"
        assert env["HERMES_HOME"] == str(fallback_home)
        assert (fallback_home / "auth.json").exists()
        assert (fallback_home / "config.yaml").exists()
        assert (fallback_home / "logs" / "agent.log").exists()


def test_llm_normalization_policy_blocked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        result = run_llm_normalization(config, "test_job", job_dir=job_dir, state=state)
        assert result["status"] == "blocked"
        assert (job_dir / "llm_normalization.json").exists()
        assert (job_dir / "llm_normalization_audit.json").exists()
        assert not (job_dir / "llm_normalized_transcript.md").exists()


def test_no_fact_pipeline_blocks_auto_finalize() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        llm_result = run_llm_normalization(
            config,
            "test_job",
            job_dir=job_dir,
            state=state,
            allow_external_llm=True,
            model="skeleton",
            provider="contract-test",
        )
        assert llm_result["status"] == "partial"

        facts_result = extract_clinical_facts(config, "test_job", job_dir=job_dir, state=state)
        assert facts_result["status"] == "partial"

        soap_result = draft_soap_note(config, "test_job", job_dir=job_dir, state=state)
        assert soap_result["status"] == "blocked"

        validation_result = validate_soap_note(config, "test_job", job_dir=job_dir, state=state)
        assert validation_result["status"] == "blocked"
        validation = json.loads((job_dir / "soap_validation.json").read_text(encoding="utf-8"))
        assert "soap_has_no_source_fact_coverage" in validation["blocking_reasons"]


def test_validation_rejects_default_policy_even_with_source_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        _write_json(
            job_dir / "clinical_facts.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "ok",
                "facts": [
                    {
                        "fact_id": "f1",
                        "type": "symptom",
                        "text": "Patient reports cough.",
                        "certainty": "confirmed",
                        "risk_level": "low",
                        "source_refs": [{"artifact": "raw_transcript.txt", "line_start": 1, "line_end": 1}],
                        "allowed_in_final_soap": True,
                    }
                ],
                "excluded_facts": [],
            },
        )
        _write_json(
            job_dir / "soap_note.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "needs_review",
                "sections": {
                    "subjective": {"text": "Patient reports cough.", "source_fact_ids": ["f1"]},
                    "objective": {"text": "Not documented in transcript.", "source_fact_ids": []},
                    "assessment": {"text": "Not documented in transcript.", "source_fact_ids": []},
                    "plan": {"text": "Not documented in transcript.", "source_fact_ids": []},
                },
                "warnings": [],
                "blocking_reasons": [],
            },
        )
        result = validate_soap_note(config, "test_job", job_dir=job_dir, state=state)
        assert result["status"] == "blocked"
        validation = json.loads((job_dir / "soap_validation.json").read_text(encoding="utf-8"))
        assert "auto_finalize_disabled_by_policy" in validation["blocking_reasons"]


def test_validation_blocks_rollout_selector_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config, job_dir, state = _base_job(tmp_path)
        state["routing"] = {
            "bed_id": "unknown-001",
            "encounter_id": "20260512_bed-unknown-001_encounter-001",
            "same_encounter_confidence": "unverified",
            "requires_identity_review": True,
        }
        _write_valid_fact_and_soap(job_dir)
        policy_path = tmp_path / "policy.yml"
        policy_path.write_text(
            "\n".join(
                [
                    'version: "1.0"',
                    "rollout_level: 1",
                    "auto_finalize_enabled: true",
                    "rollout_selector:",
                    "  allowed_job_ids:",
                    '    - "other_job"',
                    "block_on:",
                    "  high_risk_excluded_facts: true",
                    "  missing_source_fact_coverage: true",
                    "  missing_clinical_facts: true",
                    "  missing_required_sections: true",
                    "  unresolved_uncertain_terms: true",
                    "  unresolved_stt_recovery_candidates: true",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        with _with_policy_path(policy_path):
            result = validate_soap_note(config, "test_job", job_dir=job_dir, state=state)

        assert result["status"] == "blocked"
        validation = json.loads((job_dir / "soap_validation.json").read_text(encoding="utf-8"))
        assert "rollout_selector_job_not_allowed" in validation["blocking_reasons"]
        assert validation["checks"][-1]["name"] == "rollout_selector"
        assert validation["checks"][-1]["passed"] is False


def test_validation_allows_matching_rollout_selector() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        config, job_dir, state = _base_job(tmp_path)
        state["routing"] = {
            "bed_id": "unknown-001",
            "encounter_id": "20260512_bed-unknown-001_encounter-001",
            "same_encounter_confidence": "unverified",
            "requires_identity_review": True,
        }
        _write_valid_fact_and_soap(job_dir)
        policy_path = tmp_path / "policy.yml"
        policy_path.write_text(
            "\n".join(
                [
                    'version: "1.0"',
                    "rollout_level: 1",
                    "auto_finalize_enabled: true",
                    "rollout_selector:",
                    "  allowed_job_ids:",
                    '    - "test_job"',
                    "block_on:",
                    "  high_risk_excluded_facts: true",
                    "  missing_source_fact_coverage: true",
                    "  missing_clinical_facts: true",
                    "  missing_required_sections: true",
                    "  unresolved_uncertain_terms: true",
                    "  unresolved_stt_recovery_candidates: true",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        with _with_policy_path(policy_path):
            result = validate_soap_note(config, "test_job", job_dir=job_dir, state=state)

        assert result["status"] == "auto_finalized"
        validation = json.loads((job_dir / "soap_validation.json").read_text(encoding="utf-8"))
        assert validation["checks"][-1]["name"] == "rollout_selector"
        assert validation["checks"][-1]["passed"] is True


def test_validation_blocks_unresolved_uncertain_terms() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        _write_json(
            job_dir / "uncertain_terms.json",
            [
                {
                    "original": "lasix",
                    "candidate": "Lasix",
                    "confidence": "stt_warning",
                    "requires_human_confirmation": True,
                }
            ],
        )
        _write_json(
            job_dir / "clinical_facts.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "ok",
                "facts": [
                    {
                        "fact_id": "f1",
                        "type": "symptom",
                        "text": "Patient reports cough.",
                        "certainty": "confirmed",
                        "risk_level": "low",
                        "source_refs": [{"artifact": "raw_transcript.txt", "line_start": 1, "line_end": 1}],
                        "allowed_in_final_soap": True,
                    }
                ],
                "excluded_facts": [],
                "summary": {"facts": 1, "excluded_facts": 0, "high_risk_excluded": 0},
            },
        )
        _write_json(
            job_dir / "soap_note.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "needs_review",
                "sections": {
                    "subjective": {"text": "Patient reports cough.", "source_fact_ids": ["f1"]},
                    "objective": {"text": "Not documented in transcript.", "source_fact_ids": []},
                    "assessment": {"text": "Not documented in transcript.", "source_fact_ids": []},
                    "plan": {"text": "Not documented in transcript.", "source_fact_ids": []},
                },
                "warnings": [],
                "blocking_reasons": [],
            },
        )
        result = validate_soap_note(config, "test_job", job_dir=job_dir, state=state)
        assert result["status"] == "blocked"
        validation = json.loads((job_dir / "soap_validation.json").read_text(encoding="utf-8"))
        assert "unresolved_uncertain_terms" in validation["blocking_reasons"]


def test_validation_blocks_unconfirmed_stt_recovery_candidates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        _write_json(
            job_dir / "stt_recovery_candidates.json",
            {
                "version": "1.0",
                "candidate_count": 1,
                "candidates": [
                    {
                        "candidate_id": "rec-001",
                        "text": "血壓一百零八六十幾。",
                        "requires_human_confirmation": True,
                    }
                ],
            },
        )
        _write_json(
            job_dir / "clinical_facts.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "ok",
                "facts": [
                    {
                        "fact_id": "f1",
                        "type": "objective",
                        "text": "Blood pressure was discussed.",
                        "certainty": "confirmed",
                        "risk_level": "low",
                        "source_refs": [{"artifact": "raw_transcript.txt", "line_start": 1, "line_end": 1}],
                        "allowed_in_final_soap": True,
                    }
                ],
                "excluded_facts": [],
                "summary": {"facts": 1, "excluded_facts": 0, "high_risk_excluded": 0},
            },
        )
        _write_json(
            job_dir / "soap_note.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "needs_review",
                "sections": {
                    "subjective": {"text": "Not documented in transcript.", "source_fact_ids": []},
                    "objective": {"text": "Blood pressure was discussed.", "source_fact_ids": ["f1"]},
                    "assessment": {"text": "Not documented in transcript.", "source_fact_ids": []},
                    "plan": {"text": "Not documented in transcript.", "source_fact_ids": []},
                },
                "warnings": [],
                "blocking_reasons": [],
            },
        )
        result = validate_soap_note(config, "test_job", job_dir=job_dir, state=state)
        assert result["status"] == "blocked"
        validation = json.loads((job_dir / "soap_validation.json").read_text(encoding="utf-8"))
        assert "unresolved_stt_recovery_candidates" in validation["blocking_reasons"]


def test_llm_normalization_retries_invalid_json_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        calls = []

        def fake_client(prompt: str, attempt: int) -> str:
            calls.append(attempt)
            if attempt == 0:
                return "not json"
            return json.dumps(
                {
                    "version": "1.0",
                    "job_id": "test_job",
                    "status": "ok",
                    "summary": {
                        "normalized_changes": 1,
                        "uncertain_items": 0,
                        "high_risk_items": 0,
                    },
                    "normalized_transcript": "Patient reports cough. No fever documented.",
                    "blocks": [
                        {
                            "block_id": "b1",
                            "source_text": "Patient reports cough. No fever documented.",
                            "normalized_text": "Patient reports cough. No fever documented.",
                            "change_type": "punctuation",
                            "confidence": "high",
                            "rationale": "Preserved source content with readable punctuation.",
                            "source_refs": [
                                {
                                    "artifact": "normalized_transcript.md",
                                    "line_start": 1,
                                    "line_end": 1,
                                }
                            ],
                            "flags": [],
                        }
                    ],
                    "uncertain_items": [],
                    "suppressed_items": [],
                },
                ensure_ascii=False,
            )

        result = run_llm_normalization(
            config,
            "test_job",
            job_dir=job_dir,
            state=state,
            allow_external_llm=True,
            model="fake",
            provider="fake",
            llm_client=fake_client,
        )
        assert result["status"] == "ok"
        assert calls == [0, 1]
        audit = json.loads((job_dir / "llm_normalization_audit.json").read_text(encoding="utf-8"))
        assert audit["retry_count"] == 1
        assert (job_dir / "llm_normalized_transcript.md").exists()


def test_llm_normalization_retries_empty_blocks_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        calls = []

        def fake_client(prompt: str, attempt: int) -> str:
            calls.append(attempt)
            if attempt == 0:
                return json.dumps(
                    {
                        "version": "1.0",
                        "job_id": "test_job",
                        "status": "ok",
                        "summary": {
                            "normalized_changes": 0,
                            "uncertain_items": 0,
                            "high_risk_items": 0,
                        },
                        "normalized_transcript": "Patient reports cough. No fever documented.",
                        "blocks": [],
                        "uncertain_items": [],
                        "suppressed_items": [],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "version": "1.0",
                    "job_id": "test_job",
                    "status": "ok",
                    "summary": {
                        "normalized_changes": 0,
                        "uncertain_items": 0,
                        "high_risk_items": 0,
                    },
                    "normalized_transcript": "Patient reports cough. No fever documented.",
                    "blocks": [
                        {
                            "block_id": "b1",
                            "source_text": "Patient reports cough. No fever documented.",
                            "normalized_text": "Patient reports cough. No fever documented.",
                            "change_type": "cleanup",
                            "confidence": "high",
                            "rationale": "Preserved source content.",
                            "source_refs": [{"artifact": "normalized_transcript.md", "line_start": 1, "line_end": 1}],
                            "flags": [],
                        }
                    ],
                    "uncertain_items": [],
                    "suppressed_items": [],
                },
                ensure_ascii=False,
            )

        result = run_llm_normalization(
            config,
            "test_job",
            job_dir=job_dir,
            state=state,
            allow_external_llm=True,
            model="fake",
            provider="fake",
            llm_client=fake_client,
        )
        assert result["status"] == "ok"
        assert calls == [0, 1]


def test_fact_extraction_promotes_low_risk_llm_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        _write_json(
            job_dir / "llm_normalization.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "ok",
                "summary": {
                    "normalized_changes": 0,
                    "uncertain_items": 0,
                    "high_risk_items": 0,
                },
                "blocks": [
                    {
                        "block_id": "b1",
                        "source_text": "Patient reports cough. No fever documented.",
                        "normalized_text": "Patient reports cough. No fever documented.",
                        "change_type": "cleanup",
                        "confidence": "high",
                        "rationale": "Readable transcript cleanup only.",
                        "source_refs": [
                            {
                                "artifact": "normalized_transcript.md",
                                "line_start": 1,
                                "line_end": 1,
                            }
                        ],
                        "flags": ["contains_symptom"],
                    }
                ],
                "uncertain_items": [],
                "suppressed_items": [],
            },
        )
        result = extract_clinical_facts(config, "test_job", job_dir=job_dir, state=state)
        assert result["status"] == "ok"
        facts = json.loads((job_dir / "clinical_facts.json").read_text(encoding="utf-8"))
        assert facts["summary"]["facts"] == 1
        assert facts["facts"][0]["type"] == "symptom"
        assert facts["facts"][0]["allowed_in_final_soap"] is True


def test_fact_extraction_promotes_high_confidence_review_blocks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        _write_json(
            job_dir / "llm_normalization.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "ok",
                "summary": {
                    "normalized_changes": 2,
                    "uncertain_items": 0,
                    "high_risk_items": 0,
                },
                "blocks": [
                    {
                        "block_id": "b1",
                        "source_text": "主述是發燒跟乾咳快一個月。",
                        "normalized_text": "主述是發燒跟乾咳快一個月。",
                        "change_type": "cleanup",
                        "confidence": "high",
                        "rationale": "Direct symptom report.",
                        "source_refs": [
                            {
                                "artifact": "normalized_transcript.md",
                                "line_start": 1,
                                "line_end": 1,
                            }
                        ],
                        "flags": ["needs_review"],
                    },
                    {
                        "block_id": "b2",
                        "source_text": "先幫他掛上nasal oxygen，並安排bronchoscopy。",
                        "normalized_text": "先幫他掛上nasal oxygen，並安排bronchoscopy。",
                        "change_type": "cleanup",
                        "confidence": "high",
                        "rationale": "Direct plan statement.",
                        "source_refs": [
                            {
                                "artifact": "normalized_transcript.md",
                                "line_start": 2,
                                "line_end": 2,
                            }
                        ],
                        "flags": ["needs_review"],
                    },
                ],
                "uncertain_items": [],
                "suppressed_items": [],
            },
        )
        result = extract_clinical_facts(config, "test_job", job_dir=job_dir, state=state)
        assert result["status"] == "ok"
        facts = json.loads((job_dir / "clinical_facts.json").read_text(encoding="utf-8"))
        assert facts["summary"]["facts"] == 2
        assert facts["facts"][0]["type"] == "symptom"
        assert facts["facts"][1]["type"] == "plan"
        assert all(item["review_required"] is True for item in facts["facts"])


def test_soap_draft_uses_eligible_clinical_facts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        config, job_dir, state = _base_job(Path(tmp))
        _write_json(
            job_dir / "clinical_facts.json",
            {
                "version": "1.0",
                "job_id": "test_job",
                "status": "ok",
                "facts": [
                    {
                        "fact_id": "f1",
                        "type": "symptom",
                        "text": "Patient reports cough.",
                        "normalized_text": "Patient reports cough.",
                        "certainty": "confirmed",
                        "risk_level": "low",
                        "source_refs": [{"artifact": "normalized_transcript.md", "line_start": 1, "line_end": 1}],
                        "derived_from": ["llm_normalization:block:b1"],
                        "allowed_in_final_soap": True,
                    }
                ],
                "excluded_facts": [],
                "summary": {"facts": 1, "excluded_facts": 0, "high_risk_excluded": 0},
            },
        )
        draft = draft_soap_note(config, "test_job", job_dir=job_dir, state=state)
        assert draft["status"] == "needs_review"
        soap = json.loads((job_dir / "soap_note.json").read_text(encoding="utf-8"))
        assert "Patient reports cough" in soap["sections"]["subjective"]["text"]
        assert soap["sections"]["subjective"]["source_fact_ids"] == ["f1"]

        validation = validate_soap_note(config, "test_job", job_dir=job_dir, state=state)
        assert validation["status"] == "blocked"
        validation_payload = json.loads((job_dir / "soap_validation.json").read_text(encoding="utf-8"))
        assert "auto_finalize_disabled_by_policy" in validation_payload["blocking_reasons"]
        assert "soap_has_no_source_fact_coverage" not in validation_payload["blocking_reasons"]


def test_auto_soap_foreground_output_is_not_failed_by_validation_block() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        job_id = "test_job"
        job_dir = root / job_id
        job_dir.mkdir()
        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=root,
            case_view_dir=root / "case_view",
            log_dir=root / "logs",
        )
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "status": "needs_review",
                "current_step": "test",
                "steps": {},
                "policy": {},
                "artifacts": {
                    "soap_note": "soap_note.md",
                },
                "review_reasons": [],
                "needs_human_review": True,
            },
        )

        original_normalize = jobs.normalize_llm
        original_extract = jobs.extract_facts
        original_draft = jobs.draft_soap
        original_validate = jobs.validate_soap
        try:
            jobs.normalize_llm = lambda *args, **kwargs: {"ok": True, "action": "normalize-llm", "status": "ok"}
            jobs.extract_facts = lambda *args, **kwargs: {"ok": True, "action": "extract-facts", "status": "ok"}
            jobs.draft_soap = lambda *args, **kwargs: {"ok": True, "action": "draft-soap", "status": "needs_review"}
            jobs.validate_soap = lambda *args, **kwargs: {
                "ok": False,
                "action": "validate-soap",
                "status": "blocked",
                "message": "auto_finalize_disabled_by_policy",
            }

            result = jobs.auto_soap(config, job_id)
        finally:
            jobs.normalize_llm = original_normalize
            jobs.extract_facts = original_extract
            jobs.draft_soap = original_draft
            jobs.validate_soap = original_validate

        assert result["ok"] is True
        assert result["status"] == "output_ready"
        assert result["validation"]["ok"] is False
        assert result["validation"]["status"] == "blocked"


if __name__ == "__main__":
    test_llm_normalization_policy_blocked()
    test_no_fact_pipeline_blocks_auto_finalize()
    test_validation_rejects_default_policy_even_with_source_coverage()
    test_validation_blocks_rollout_selector_mismatch()
    test_validation_allows_matching_rollout_selector()
    test_validation_blocks_unresolved_uncertain_terms()
    test_validation_blocks_unconfirmed_stt_recovery_candidates()
    test_llm_normalization_retries_invalid_json_once()
    test_llm_normalization_retries_empty_blocks_once()
    test_fact_extraction_promotes_low_risk_llm_block()
    test_soap_draft_uses_eligible_clinical_facts()
    test_auto_soap_foreground_output_is_not_failed_by_validation_block()
    print("auto SOAP contract tests passed")
