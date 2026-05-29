from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline import jobs as jobs_module
from ward_pipeline import literature as literature_module
from ward_pipeline.jobs import draft_soap, literature_enrich, literature_plan
from ward_pipeline.literature import LITERATURE_TAXONOMY_CANDIDATES_FILE, plan_literature_queries, retrieve_literature_sources, summarize_literature_sources
from taxonomy.scripts.classify_soap import build_classification_json


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


def test_plan_literature_queries_infers_service_diagnosis_and_targets() -> None:
    plan = plan_literature_queries(
        "Assessment: small intracranial hemorrhage. No craniotomy is needed now. "
        "Monitor neurologic status and blood pressure.",
        source_type="soap_note.md",
    )

    classification = plan["clinical_classification"]
    assert classification["primary_service"] in {"neurology", "neurosurgery"}
    assert "intracranial_hemorrhage" in classification["diagnosis_topics"]
    assert any("intracerebral hemorrhage" in target for target in plan["search_targets"])
    assert plan["source_type"] == "soap_note.md"


def test_literature_plan_prefers_soap_text_after_draft() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        output_dir = tmp / "output"
        job_id = "20260514_200000_lit001"
        job_dir = output_dir / job_id
        job_dir.mkdir(parents=True)
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=output_dir,
            case_view_dir=tmp / "cases",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
            stt_review_queue_dir=tmp / "stt_queue",
        )
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-14T20:00:00+08:00",
                "updated_at": "2026-05-14T20:00:00+08:00",
                "status": "needs_review",
                "current_step": "clinical_extract",
                "steps": {"literature": "pending"},
                "artifacts": {},
                "needs_human_review": True,
                "review_reasons": [],
                "policy": {},
            },
        )
        _write_json(
            job_dir / "clinical_facts.json",
            {
                "status": "done",
                "facts": [
                    {
                        "fact_id": "fact-assessment-1",
                        "type": "assessment",
                        "section": "assessment",
                        "normalized_text": "Small intracranial hemorrhage; no surgery currently indicated.",
                        "evidence": ["intracranial hemorrhage no craniotomy"],
                        "certainty": "confirmed",
                        "allowed_in_final_soap": True,
                    },
                    {
                        "fact_id": "fact-plan-1",
                        "type": "plan",
                        "section": "plan",
                        "normalized_text": "Continue neurologic monitoring and blood pressure control.",
                        "evidence": ["monitor neurologic status blood pressure"],
                        "certainty": "confirmed",
                        "allowed_in_final_soap": True,
                    },
                ],
                "blocking_reasons": [],
            },
        )
        (job_dir / "transcript.txt").write_text("generic ward discussion\n", encoding="utf-8")

        draft = draft_soap(config, job_id)
        assert draft["ok"] is True
        assert "literature_plan" in draft

        result = literature_plan(config, job_id)
        plan = result["plan"]
        assert plan["source_type"].startswith("soap_note.md")
        assert "intracranial_hemorrhage" in plan["clinical_classification"]["diagnosis_topics"]
        assert any("intracerebral hemorrhage" in target for target in plan["search_targets"])


def test_plan_literature_queries_covers_added_specialties() -> None:
    cases = [
        (
            "hematology_oncology",
            "Assessment: febrile neutropenia after chemotherapy for lymphoma.",
            "febrile_neutropenia",
            "febrile neutropenia guideline",
        ),
        (
            "rheumatology",
            "Assessment: systemic lupus erythematosus with suspected lupus nephritis flare.",
            "lupus_nephritis",
            "lupus nephritis guideline",
        ),
        (
            "gastroenterology_hepatology",
            "Assessment: acute cholangitis from choledocholithiasis with jaundice.",
            "cholangitis",
            "acute cholangitis",
        ),
        (
            "general_surgery",
            "Assessment: small bowel obstruction with peritonitis, needs surgical evaluation.",
            "bowel_obstruction",
            "bowel obstruction",
        ),
        (
            "general_surgery",
            "Assessment: suspected scald injury / thermal injury to the left forearm after hot water spill. "
            "Plan: continue local wound care and monitor for signs of infection.",
            "thermal_injury_burn",
            "burn wound care",
        ),
        (
            "",
            "Assessment: acute left eye redness with possible subconjunctival hemorrhage. "
            "Vision is preserved; no anticoagulant use reported.",
            "possible_subconjunctival_hemorrhage",
            "subconjunctival hemorrhage",
        ),
    ]

    for expected_service, text, expected_topic, expected_query in cases:
        plan = plan_literature_queries(text, source_type="soap_note.md")
        classification = plan["clinical_classification"]
        if expected_service:
            assert classification["primary_specialty"] == expected_service
            assert classification["primary_service"] == expected_service
        else:
            assert classification["primary_specialty"] in {"", "general_internal_medicine"}
        assert expected_topic in classification["diagnosis_topics"]
        assert any(expected_query in target for target in plan["search_targets"])
        if expected_topic == "possible_subconjunctival_hemorrhage":
            assert all("antibiotic" not in target.casefold() for target in plan["search_targets"])


def test_plan_literature_queries_adds_service_targets_when_topics_weak() -> None:
    plan = plan_literature_queries(
        "Assessment: fever with respiratory infection concern, possible covid exposure. "
        "Plan: start antibiotics and monitor oxygen.",
        source_type="soap_note.md",
    )
    assert plan["clinical_classification"]["primary_service"] in {"infectious_disease", "pulmonology"}
    targets = [str(item).casefold() for item in plan["search_targets"]]
    assert any("inpatient" in item for item in targets)
    assert any("guideline" in item for item in targets)


def test_plan_literature_queries_infers_hemorrhoid_like_pattern() -> None:
    plan = plan_literature_queries(
        "S: The patient reports ongoing bleeding from the buttock/perianal area. "
        "Bleeding is small in amount and occurs only during bowel movements. "
        "The patient is applying a topical medication twice daily. "
        "P: Continue warm sitz baths and topical treatment.",
        source_type="soap_note.md",
    )
    classification = plan["clinical_classification"]
    assert classification["primary_service"] == "gastroenterology_hepatology"
    assert "symptomatic_external_hemorrhoids" in classification["diagnosis_topics"]
    assert any("hemorrhoids guideline management" in target for target in plan["search_targets"])


def test_gastroenterology_hepatology_keeps_legacy_aliases() -> None:
    plan = plan_literature_queries(
        "Assessment: acute cholangitis from choledocholithiasis with jaundice.",
        source_type="soap_note.md",
    )
    classification = plan["clinical_classification"]

    assert classification["primary_specialty"] == "gastroenterology_hepatology"
    assert "gastroenterology" in classification["legacy_service_aliases"]
    assert "hepatobiliary_pancreatic" in classification["legacy_service_aliases"]


def test_hermes_routing_block_drives_classification_without_taxonomy_updates() -> None:
    text = """S
- Acute left eye redness.

A
- Possible subconjunctival hemorrhage.

P
- Ophthalmology evaluation recommended.

需確認
- none

## Routing
```json
{
  "primary_specialty": "ophthalmology",
  "diagnosis_topics": ["subconjunctival_hemorrhage"],
  "confidence": "high",
  "routing_rationale": "eye bleed needs ophthalmology"
}
```
"""
    plan = plan_literature_queries(text, source_type="result.hermes.md")
    classification = plan["clinical_classification"]
    assert classification["primary_specialty"] == "ophthalmology"
    assert classification["primary_service"] == "ophthalmology"
    assert classification["diagnosis_topics"] == ["subconjunctival_hemorrhage"]
    assert any("subconjunctival hemorrhage" in target for target in plan["search_targets"])

    payload = build_classification_json(text)
    assert payload["primary_specialty"] == "ophthalmology"
    assert payload["suggested_obsidian_folder"] == "Medicine/Ophthalmology"
    assert payload["diagnosis_topics"] == ["subconjunctival_hemorrhage"]


def test_literature_plan_auto_harvests_candidates_without_runtime_classification() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        output_dir = tmp / "output"
        job_id = "20260514_204000_lit002"
        job_dir = output_dir / job_id
        job_dir.mkdir(parents=True)
        queue_path = tmp / "literature_taxonomy_candidates.yml"
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=output_dir,
            case_view_dir=tmp / "cases",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
            stt_review_queue_dir=tmp / "stt_queue",
        )
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-14T20:40:00+08:00",
                "updated_at": "2026-05-14T20:40:00+08:00",
                "status": "needs_review",
                "current_step": "transcribe",
                "steps": {"literature": "pending"},
                "artifacts": {},
                "needs_human_review": True,
                "review_reasons": [],
                "policy": {},
            },
        )
        (job_dir / "transcript.manual.txt").write_text("Assessment: suspected myasthenia gravis with ptosis.\n", encoding="utf-8")

        original_lit_path = LITERATURE_TAXONOMY_CANDIDATES_FILE
        original_jobs_path = jobs_module.LITERATURE_TAXONOMY_CANDIDATES_FILE
        try:
            from ward_pipeline import literature as literature_module

            literature_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = queue_path
            jobs_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = queue_path

            result = literature_plan(config, job_id)
            assert result["taxonomy_refresh"]["items_added"] >= 1
            payload = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
            assert any(item["status"] == "watch" and "myasthenia_gravis" in item["key"] for item in payload["items"])

            for item in payload["items"]:
                if "myasthenia_gravis" in item["key"]:
                    item["status"] = "approved"
                    item["keywords"] = ["myasthenia gravis", "MG"]
            queue_path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")

            approved_plan = plan_literature_queries(
                "Assessment: myasthenia gravis with ptosis and fatigable weakness.",
                source_type="soap_note.md",
            )
            assert "myasthenia_gravis" not in approved_plan["clinical_classification"]["diagnosis_topics"]
            assert "approved.yml only" in approved_plan["clinical_classification"]["classification_basis"]
        finally:
            from ward_pipeline import literature as literature_module

            literature_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = original_lit_path
            jobs_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = original_jobs_path


def test_literature_taxonomy_queue_survives_bad_yaml_and_persists_updates() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        queue_path = tmp / "literature_taxonomy_candidates.yml"
        queue_path.write_text("items: [broken\n", encoding="utf-8")

        original_lit_path = LITERATURE_TAXONOMY_CANDIDATES_FILE
        try:
            literature_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = queue_path
            plan = plan_literature_queries("Assessment: suspected myasthenia gravis with ptosis.", source_type="soap_note.md")
            assert plan["ok"] is True
        finally:
            literature_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = original_lit_path

        queue_path.write_text(
            yaml.safe_dump(
                {
                    "version": "1.0",
                    "queue_type": "literature_taxonomy_candidates",
                    "items": [
                        {
                            "key": "diagnosis:myasthenia_gravis",
                            "status": "watch",
                            "kind": "diagnosis",
                            "label": "myasthenia_gravis",
                            "keywords": ["myasthenia gravis"],
                            "last_seen_source_id": "old-job",
                            "examples": ["myasthenia gravis"],
                        }
                    ],
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        try:
            literature_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = queue_path
            result = literature_module._refresh_literature_taxonomy_candidates(
                "Assessment: suspected myasthenia gravis with ptosis.",
                source_type="soap_note.md",
                source_id="new-job",
            )
        finally:
            literature_module.LITERATURE_TAXONOMY_CANDIDATES_FILE = original_lit_path

        assert result["ok"] is True
        payload = yaml.safe_load(queue_path.read_text(encoding="utf-8"))
        item = payload["items"][0]
        assert item["last_seen_source_id"] == "new-job"
        assert item["keywords"] == ["myasthenia gravis"]


def test_literature_retrieval_and_summary_use_pubmed_metadata() -> None:
    plan = {
        "clinical_classification": {
            "primary_service": "neurology",
            "diagnosis_topics": ["intracranial_hemorrhage"],
            "confidence": "high",
        },
        "search_targets": ["intracerebral hemorrhage guideline blood pressure management"],
    }

    def fake_http_get_text(url: str, *, timeout: int = 20) -> str:
        if "esearch.fcgi" in url:
            return json.dumps({"esearchresult": {"idlist": ["12345"]}})
        return """
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>12345</PMID>
              <Article>
                <Journal>
                  <Title>Stroke</Title>
                  <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
                </Journal>
                <ArticleTitle>Guideline for intracerebral hemorrhage management</ArticleTitle>
                <Abstract>
                  <AbstractText>This guideline reviews blood pressure management and acute care for intracerebral hemorrhage.</AbstractText>
                  <AbstractText>It emphasizes patient selection and clinician judgment.</AbstractText>
                </Abstract>
                <PublicationTypeList>
                  <PublicationType>Practice Guideline</PublicationType>
                </PublicationTypeList>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
        """

    original_http = literature_module._http_get_text
    try:
        literature_module._http_get_text = fake_http_get_text
        sources = retrieve_literature_sources(plan, max_queries=1, results_per_query=1)
        summary = summarize_literature_sources(plan, sources)
    finally:
        literature_module._http_get_text = original_http

    assert sources["ok"] is True
    assert sources["source_count"] == 1
    assert sources["sources"][0]["pmid"] == "12345"
    assert "pubmed.ncbi.nlm.nih.gov/12345" in sources["sources"][0]["url"]
    assert summary["source_count"] == 1
    assert summary["clinical_context"]["primary_service"] == "neurology"
    assert "intracerebral hemorrhage" in summary["evidence_items"][0]["summary"]


def test_literature_retrieval_relevance_gate_filters_unrelated_items() -> None:
    plan = {
        "clinical_classification": {
            "primary_service": "infectious_disease",
            "diagnosis_topics": ["pneumonia"],
            "confidence": "high",
        },
        "search_targets": ["pneumonia antibiotic guideline"],
    }

    def fake_http_get_text(url: str, *, timeout: int = 20) -> str:
        if "esearch.fcgi" in url:
            return json.dumps({"esearchresult": {"idlist": ["11111", "22222"]}})
        return """
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>11111</PMID>
              <Article>
                <Journal><Title>Chest</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
                <ArticleTitle>Pneumonia antibiotic guideline in adults</ArticleTitle>
                <Abstract><AbstractText>Guideline for pneumonia antibiotic treatment.</AbstractText></Abstract>
                <PublicationTypeList><PublicationType>Practice Guideline</PublicationType></PublicationTypeList>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>22222</PMID>
              <Article>
                <Journal><Title>Fertility</Title><JournalIssue><PubDate><Year>2025</Year></PubDate></JournalIssue></Journal>
                <ArticleTitle>Embryo transfer policy update</ArticleTitle>
                <Abstract><AbstractText>Reproductive policy statement unrelated to pulmonary infection.</AbstractText></Abstract>
                <PublicationTypeList><PublicationType>Practice Guideline</PublicationType></PublicationTypeList>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>
        """

    original_http = literature_module._http_get_text
    try:
        literature_module._http_get_text = fake_http_get_text
        sources = retrieve_literature_sources(plan, max_queries=1, results_per_query=2)
    finally:
        literature_module._http_get_text = original_http

    assert sources["ok"] is True
    titles = [str(item.get("title") or "") for item in sources["sources"]]
    assert any("Pneumonia antibiotic guideline" in title for title in titles)
    assert not any("Embryo transfer policy update" in title for title in titles)


def test_literature_enrich_writes_sources_and_summary_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        output_dir = tmp / "output"
        job_id = "20260514_210000_lit003"
        job_dir = output_dir / job_id
        job_dir.mkdir(parents=True)
        config = TestConfig(
            incoming_dir=tmp / "incoming",
            output_dir=output_dir,
            case_view_dir=tmp / "cases",
            log_dir=tmp / "logs",
            obsidian_vault_dir=tmp / "vault",
            stt_review_queue_dir=tmp / "stt_queue",
        )
        _write_json(
            job_dir / "state.json",
            {
                "job_id": job_id,
                "created_at": "2026-05-14T21:00:00+08:00",
                "updated_at": "2026-05-14T21:00:00+08:00",
                "status": "needs_review",
                "current_step": "manual_review",
                "steps": {"literature": "planned"},
                "artifacts": {},
                "needs_human_review": True,
                "review_reasons": [],
                "policy": {},
            },
        )
        _write_json(
            job_dir / "literature_query_plan.json",
            {
                "clinical_classification": {
                    "primary_service": "neurology",
                    "diagnosis_topics": ["intracranial_hemorrhage"],
                    "confidence": "high",
                },
                "search_targets": ["intracerebral hemorrhage guideline blood pressure management"],
            },
        )

        def fake_retrieve(plan: dict, *, max_queries: int = 4, results_per_query: int = 3, timeout: int = 20) -> dict:
            return {
                "ok": True,
                "source_count": 1,
                "errors": [],
                "openevidence_narrative": {
                    "ok": True,
                    "source": "browser",
                    "query": "intracerebral hemorrhage guideline blood pressure management",
                    "text": "This is a synthetic OpenEvidence narrative for test coverage.",
                },
                "clinical_classification": plan["clinical_classification"],
                "sources": [
                    {
                        "title": "Guideline for intracerebral hemorrhage management",
                        "pmid": "12345",
                        "url": "https://pubmed.ncbi.nlm.nih.gov/12345/",
                        "journal": "Stroke",
                        "publication_year": "2024",
                        "publication_types": ["Practice Guideline"],
                        "abstract": "This guideline reviews blood pressure management for intracerebral hemorrhage.",
                        "query": "intracerebral hemorrhage guideline blood pressure management",
                    }
                ],
            }

        original_retrieve = jobs_module.retrieve_literature_sources
        try:
            jobs_module.retrieve_literature_sources = fake_retrieve
            result = literature_enrich(config, job_id, max_queries=1, results_per_query=1)
        finally:
            jobs_module.retrieve_literature_sources = original_retrieve

        assert result["ok"] is True
        assert result["source_count"] == 1
        assert (job_dir / "literature_sources.json").exists()
        assert (job_dir / "literature_summary.json").exists()
        assert (job_dir / "openevidence_narrative.md").exists()
        state = json.loads((job_dir / "state.json").read_text(encoding="utf-8"))
        assert state["artifacts"]["literature_sources"] == "literature_sources.json"
        assert state["artifacts"]["openevidence_narrative"] == "openevidence_narrative.md"
        assert state["steps"]["literature"] == "summarized"


if __name__ == "__main__":
    test_plan_literature_queries_infers_service_diagnosis_and_targets()
    test_literature_plan_prefers_soap_text_after_draft()
    test_plan_literature_queries_covers_added_specialties()
    test_plan_literature_queries_adds_service_targets_when_topics_weak()
    test_literature_plan_auto_harvests_candidates_without_runtime_classification()
    test_literature_taxonomy_queue_survives_bad_yaml_and_persists_updates()
    test_literature_retrieval_and_summary_use_pubmed_metadata()
    test_literature_retrieval_relevance_gate_filters_unrelated_items()
    test_literature_enrich_writes_sources_and_summary_artifacts()
    print(json.dumps({"ok": True, "message": "literature plan tests passed"}))
