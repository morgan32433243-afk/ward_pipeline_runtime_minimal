from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline.medical_normalizer import normalize_transcript


def test_stt_warning_marks_medication_and_numeric_dose_for_review() -> None:
    result = normalize_transcript("Plan: continue aspirin 100 mg daily and check creatinine 1.8 tomorrow.")
    warnings = [item for item in result["uncertain_terms"] if item.get("confidence") == "stt_warning"]
    originals = {item["original"].lower() for item in warnings}

    assert "aspirin" in originals
    assert "creatinine" in originals
    assert result["summary"]["stt_warnings"] >= 2
    assert all(item["requires_human_confirmation"] for item in warnings)
    assert any("numeric context" in item["reason"] for item in warnings)


def test_stt_warning_does_not_delete_or_change_transcript() -> None:
    text = "EKG was ordered and metformin was mentioned without clear dose."
    result = normalize_transcript(text)

    assert result["normalized_transcript"] == text
    assert any(item["original"].lower() == "metformin" for item in result["uncertain_terms"])


def test_stt_normalizer_rewrites_common_real_ward_terms() -> None:
    text = "主訴是發燒跟肝殼。SPO2 91%，先掛上NASO量力。HIV的combo test跟CDC4抽了。Bactrim跟Prenisolone開上去。"
    result = normalize_transcript(text)
    normalized = result["normalized_transcript"]

    assert "cough" in normalized
    assert "SpO2" in normalized
    assert "nasal oxygen" in normalized
    assert "HIV combo test" in normalized
    assert "CD4" in normalized
    assert "trimethoprim-sulfamethoxazole" in normalized
    assert "prednisolone" in normalized
    assert result["summary"]["corrections"] >= 6


if __name__ == "__main__":
    test_stt_warning_marks_medication_and_numeric_dose_for_review()
    test_stt_warning_does_not_delete_or_change_transcript()
    test_stt_normalizer_rewrites_common_real_ward_terms()
    print(json.dumps({"ok": True, "message": "stt warning tests passed"}))
