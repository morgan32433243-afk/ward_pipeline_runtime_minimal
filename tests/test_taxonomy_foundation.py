from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taxonomy.scripts.validate_taxonomy import validate_literature_taxonomy, validate_specialty_map
from ward_pipeline.literature import infer_clinical_classification
from ward_pipeline.taxonomy import load_specialty_legacy_aliases, load_specialty_required_any
from taxonomy.scripts.classify_soap import build_classification_json


REPO_ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_DIR = REPO_ROOT / "taxonomy"
PHASE1_CANONICAL_SPECIALTIES = {
    "cardiology",
    "pulmonology",
    "gastroenterology_hepatology",
    "nephrology",
    "endocrinology",
    "infectious_disease",
    "hematology_oncology",
    "rheumatology",
    "neurology",
    "critical_care",
    "general_internal_medicine",
}
PHASE1_MIN_COUNTS = {
    "strong_keywords": 30,
    "aliases": 20,
    "weak_clues": 10,
}
BROAD_TERMS_NOT_STRONG = {
    "cardiology": {"troponin"},
    "infectious_disease": {"fever"},
    "pulmonology": {"dyspnea"},
    "nephrology": {"creatinine"},
}
BROAD_STRONG_KEYWORDS = {
    "pain",
    "fever",
    "dyspnea",
    "creatinine",
    "troponin",
    "oxygen",
    "weakness",
    "cough",
    "fatigue",
    "dizziness",
    "nausea",
    "vomiting",
    "edema",
}
REQUIRED_SPECIALTY_FIELDS = {
    "display_name",
    "obsidian_folder",
    "strong_keywords",
    "aliases",
    "weak_clues",
    "medications",
    "labs_imaging",
    "procedures",
    "negative_context",
    "legacy_aliases",
}


def test_taxonomy_files_validate() -> None:
    assert validate_specialty_map(TAXONOMY_DIR / "clinical_specialty_map.yml") >= 10
    assert validate_literature_taxonomy(TAXONOMY_DIR / "literature_taxonomy_approved.yml") >= 30
    assert validate_literature_taxonomy(TAXONOMY_DIR / "literature_taxonomy_candidates.yml") >= 0


def test_sample_soap_expected_outputs() -> None:
    sample_dir = TAXONOMY_DIR / "tests"
    expected_dir = sample_dir / "expected_outputs"
    for sample_path in sorted(sample_dir.glob("sample_soap_*.md")):
        expected_path = expected_dir / f"{sample_path.stem}.json"
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        result = infer_clinical_classification(sample_path.read_text(encoding="utf-8"))

        assert result["primary_specialty"] == expected["primary_specialty"]
        assert result["primary_service"] == expected["primary_specialty"]
        for topic in expected["diagnosis_topics"]:
            assert topic in result["diagnosis_topics"]


def test_phase1_canonical_specialties_and_legacy_aliases() -> None:
    import yaml

    payload = yaml.safe_load((TAXONOMY_DIR / "clinical_specialty_map.yml").read_text(encoding="utf-8"))
    canonical_ids = set(payload["specialties"])

    assert canonical_ids == PHASE1_CANONICAL_SPECIALTIES
    aliases = load_specialty_legacy_aliases()["gastroenterology_hepatology"]
    assert "gastroenterology" in aliases
    assert "hepatobiliary_pancreatic" in aliases


def test_phase1_canonical_specialty_content_density() -> None:
    import yaml

    payload = yaml.safe_load((TAXONOMY_DIR / "clinical_specialty_map.yml").read_text(encoding="utf-8"))
    for specialty_id, item in payload["specialties"].items():
        assert REQUIRED_SPECIALTY_FIELDS.issubset(item), specialty_id
        for field, minimum in PHASE1_MIN_COUNTS.items():
            values = item.get(field) or []
            assert len(values) >= minimum, f"{specialty_id}.{field}"
            assert len(values) == len(set(values)), f"{specialty_id}.{field} has duplicates"
        strong_terms = {str(value).casefold() for value in item.get("strong_keywords") or []}
        assert not (BROAD_TERMS_NOT_STRONG.get(specialty_id, set()) & strong_terms), specialty_id
        assert not (BROAD_STRONG_KEYWORDS & strong_terms), specialty_id


def test_approved_literature_taxonomy_has_routing_metadata() -> None:
    import yaml

    clinical = yaml.safe_load((TAXONOMY_DIR / "clinical_specialty_map.yml").read_text(encoding="utf-8"))
    allowed_specialties = set(clinical["specialties"])
    allowed_specialties.update(
        str(item.get("id"))
        for item in clinical.get("legacy_specialties") or []
        if isinstance(item, dict) and item.get("id")
    )
    approved = yaml.safe_load((TAXONOMY_DIR / "literature_taxonomy_approved.yml").read_text(encoding="utf-8"))

    for item in approved["items"]:
        assert item["specialty_primary"] in allowed_specialties, item["label"]
        assert 0 <= item["confidence"] <= 1, item["label"]
        assert item["source_trace"], item["label"]


def test_critical_care_requires_instability_signal() -> None:
    required_any = load_specialty_required_any()["critical_care"]
    assert "shock" in required_any
    assert "vasopressor" in required_any
    assert "mechanical ventilation" in required_any

    pneumonia = infer_clinical_classification("Assessment: pneumonia with hypoxemia.")
    assert pneumonia["primary_specialty"] == "pulmonology"
    assert all(item["label"] != "critical_care" for item in pneumonia["service_candidates"])

    aki = infer_clinical_classification("Assessment: acute kidney injury with hyperkalemia.")
    assert aki["primary_specialty"] == "nephrology"
    assert all(item["label"] != "critical_care" for item in aki["service_candidates"])

    unstable = infer_clinical_classification(
        "Assessment: septic shock with respiratory failure on norepinephrine and mechanical ventilation in ICU."
    )
    assert unstable["primary_specialty"] == "critical_care"
    assert unstable["service_candidates"][0]["required_matches"]

    sample = infer_clinical_classification((TAXONOMY_DIR / "tests" / "sample_soap_critical_care.md").read_text(encoding="utf-8"))
    assert sample["primary_specialty"] == "critical_care"
    assert sample["service_candidates"][0]["label"] == "critical_care"
    assert any(item["label"] == "infectious_disease" for item in sample["service_candidates"][1:])


def test_deterministic_scoring_uses_sections_and_negation() -> None:
    section_weighted = infer_clinical_classification(
        "Past history: heart failure.\n"
        "Assessment: pneumonia with hypoxemia.\n"
        "Plan: antibiotics and oxygen."
    )
    assert section_weighted["primary_specialty"] == "pulmonology"
    assert section_weighted["service_candidates"][0]["matched_by_section"]["assessment"]
    assert "Deterministic weighted scoring" in section_weighted["classification_basis"]

    negated = infer_clinical_classification(
        "Assessment: no evidence of acute coronary syndrome. Diagnosis: pneumonia. Plan: antibiotics."
    )
    cardiology = next(item for item in negated["service_candidates"] if item["label"] == "cardiology")
    assert negated["primary_specialty"] == "pulmonology"
    assert "acute coronary syndrome" in cardiology["negative_matches"]


def test_classify_soap_outputs_step7_classification_json_shape() -> None:
    payload = build_classification_json(
        "Assessment: acute decompensated heart failure with atrial fibrillation.\n"
        "Plan: IV furosemide and ECG monitoring.\n"
        "Labs: troponin pending."
    )

    assert payload["primary_specialty"] == "cardiology"
    assert isinstance(payload["confidence"], float)
    assert 0 <= payload["confidence"] <= 1
    assert payload["scores"]["cardiology"] > 0
    assert payload["suggested_obsidian_folder"] == "Medicine/Cardiology"
    assert payload["classification_reason_summary"]
    assert any(
        item["term"] == "acute decompensated heart failure"
        and item["specialty"] == "cardiology"
        and item["category"] == "strong_keywords"
        and item["section"] == "Assessment"
        and item["weight"] == 5.0
        for item in payload["matched_terms"]
    )


if __name__ == "__main__":
    test_taxonomy_files_validate()
    test_sample_soap_expected_outputs()
    test_phase1_canonical_specialties_and_legacy_aliases()
    test_phase1_canonical_specialty_content_density()
    test_approved_literature_taxonomy_has_routing_metadata()
    test_critical_care_requires_instability_signal()
    test_deterministic_scoring_uses_sections_and_negation()
    test_classify_soap_outputs_step7_classification_json_shape()
    print(json.dumps({"ok": True, "message": "taxonomy foundation tests passed"}))
