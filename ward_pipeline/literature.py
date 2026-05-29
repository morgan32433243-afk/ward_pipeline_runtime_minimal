from __future__ import annotations

import html
import json
import os
import re
import shlex
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .taxonomy import (
    LITERATURE_TAXONOMY_CANDIDATES_FILE,
    canonicalize_specialty_id,
    load_literature_diagnosis_records,
    load_literature_diagnosis_keyword_sets,
    load_specialty_legacy_aliases,
    load_specialty_keyword_sets,
    load_specialty_records,
    load_specialty_required_any,
    load_yaml_dict,
    write_yaml,
)


PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
OPENEVIDENCE_HOME_URL = "https://www.openevidence.com/"
REPO_ROOT = Path(__file__).resolve().parents[1]
OPENEVIDENCE_SESSION_DIR = Path(
    os.environ.get("WARD_OPENEVIDENCE_SESSION_DIR", str(REPO_ROOT / "data" / "openevidence"))
).expanduser()
OPENEVIDENCE_STORAGE_STATE_FILE = OPENEVIDENCE_SESSION_DIR / "storage_state.json"
OPENEVIDENCE_BROWSER_PROFILE_DIR = OPENEVIDENCE_SESSION_DIR / "browser_profile"
OPENEVIDENCE_PROVIDER_ENV = "WARD_OPENEVIDENCE_PROVIDER"
OPENEVIDENCE_MCP_CMD_ENV = "WARD_OPENEVIDENCE_MCP_COMMAND"
PUBMED_RECENT_YEARS = 5
HERMES_ROUTING_HEADINGS = ("## Routing", "## Hermes Routing")


@dataclass(frozen=True)
class LiteratureScenario:
    scenario_id: str
    label: str
    clinical_frame: str
    decision_focus: tuple[str, ...]
    required_any: tuple[str, ...]
    supportive_any: tuple[str, ...]
    search_targets: tuple[str, ...]
    preferred_sources: tuple[str, ...]
    avoid_queries: tuple[str, ...]
    rationale: str


SCENARIOS: tuple[LiteratureScenario, ...] = (
    LiteratureScenario(
        scenario_id="stemi_acs",
        label="STEMI / acute coronary syndrome",
        clinical_frame="time-sensitive cardiovascular emergency with ECG/biomarker evidence",
        decision_focus=("reperfusion timing", "antiplatelet therapy", "anticoagulation", "transfer/PCI pathway"),
        required_any=(
            "stemi",
            "st elevation",
            "st 上升",
            "st段上升",
            "心電圖 st elevation",
            "心電圖 st 上升",
            "troponin 上升",
            "肌鈣蛋白上升",
        ),
        supportive_any=(
            "胸痛",
            "chest pain",
            "急性冠心症",
            "acs",
            "心肌梗塞",
            "myocardial infarction",
        ),
        search_targets=(
            "STEMI guideline",
            "acute coronary syndrome guideline",
            "primary PCI timing STEMI guideline",
            "antiplatelet anticoagulation STEMI guideline",
            "AHA ACC ESC STEMI guideline",
        ),
        preferred_sources=("AHA", "ACC", "ESC", "major cardiology society guideline"),
        avoid_queries=(
            "general heart disease",
            "chest pain general information",
            "cardiology overview",
            "heart disease overview",
        ),
        rationale="Transcript includes ST elevation or troponin/myocardial infarction cues, so the evidence need is ACS/STEMI management rather than generic chest pain.",
    ),
    LiteratureScenario(
        scenario_id="sepsis_shock",
        label="sepsis / septic shock",
        clinical_frame="suspected infection with shock or tissue hypoperfusion",
        decision_focus=("initial resuscitation", "antibiotic timing", "fluid strategy", "vasopressor choice"),
        required_any=(
            "sepsis",
            "septic shock",
            "敗血症",
            "感染性休克",
            "lactate 上升",
            "乳酸上升",
            "疑似感染",
        ),
        supportive_any=(
            "發燒",
            "fever",
            "血壓下降",
            "低血壓",
            "hypotension",
            "意識改變",
            "altered mental status",
            "感染",
        ),
        search_targets=(
            "sepsis guideline",
            "septic shock initial management",
            "Surviving Sepsis Campaign guideline",
            "sepsis antibiotics timing",
            "septic shock fluid resuscitation guideline",
            "norepinephrine vasopressor septic shock guideline",
        ),
        preferred_sources=("Surviving Sepsis Campaign", "SCCM", "ESICM", "major critical care guideline"),
        avoid_queries=(
            "fever general information",
            "infection overview",
            "general infection treatment",
            "sepsis overview for patients",
        ),
        rationale="Transcript combines suspected infection with shock/perfusion cues, so the evidence need is sepsis bundle and shock management rather than generic fever or infection.",
    ),
    LiteratureScenario(
        scenario_id="acute_stroke_reperfusion",
        label="acute ischemic stroke / reperfusion eligibility",
        clinical_frame="time-sensitive neurologic deficit with reperfusion decision",
        decision_focus=("thrombolysis eligibility", "thrombectomy window", "stroke imaging pathway", "blood pressure targets"),
        required_any=("半身無力", "臉歪", "失語", "aphasia", "weakness", "nihss", "large vessel occlusion", "lvo", "腦中風", "stroke"),
        supportive_any=("發作時間", "last known well", "ct", "cta", "出血", "血壓", "thrombolysis", "thrombectomy"),
        search_targets=(
            "acute ischemic stroke guideline thrombolysis eligibility",
            "mechanical thrombectomy guideline time window",
            "AHA ASA acute ischemic stroke guideline",
            "blood pressure management acute ischemic stroke thrombolysis",
        ),
        preferred_sources=("AHA", "ASA", "ESO", "major stroke guideline"),
        avoid_queries=("weakness general information", "stroke overview", "neurology overview"),
        rationale="Transcript suggests focal neurologic deficit or stroke pathway, so evidence should target reperfusion eligibility and acute stroke systems of care.",
    ),
    LiteratureScenario(
        scenario_id="pulmonary_embolism",
        label="pulmonary embolism risk stratification / anticoagulation",
        clinical_frame="cardiopulmonary syndrome requiring PE probability, imaging, and anticoagulation decisions",
        decision_focus=("risk stratification", "CTPA indication", "anticoagulation", "thrombolysis for high-risk PE"),
        required_any=("pulmonary embolism", "肺栓塞", "pe", "d dimer", "d-dimer", "ctpa", "右心負荷", "right heart strain"),
        supportive_any=("胸痛", "喘", "dyspnea", "低氧", "hypoxia", "心跳快", "tachycardia", "血壓下降"),
        search_targets=(
            "pulmonary embolism guideline risk stratification",
            "ESC pulmonary embolism guideline anticoagulation",
            "high risk pulmonary embolism thrombolysis guideline",
            "CTPA D-dimer diagnostic algorithm pulmonary embolism",
        ),
        preferred_sources=("ESC", "CHEST", "ASH", "major thrombosis guideline"),
        avoid_queries=("shortness of breath general information", "chest pain general information", "lung disease overview"),
        rationale="Transcript suggests possible PE or PE workup, so evidence should target diagnostic probability, imaging, and anticoagulation rather than generic dyspnea.",
    ),
    LiteratureScenario(
        scenario_id="dka_hhs",
        label="DKA / HHS metabolic emergency",
        clinical_frame="hyperglycemic crisis with acid-base/electrolyte management decisions",
        decision_focus=("fluid resuscitation", "insulin infusion", "potassium replacement", "transition criteria"),
        required_any=("dka", "hhs", "酮酸中毒", "高血糖高滲", "ketone", "酮體", "anion gap", "陰離子間隙", "ph 下降"),
        supportive_any=("高血糖", "血糖", "脫水", "嘔吐", "意識改變", "鉀", "potassium", "bicarbonate"),
        search_targets=(
            "DKA guideline insulin infusion potassium replacement",
            "hyperosmolar hyperglycemic state guideline fluid management",
            "diabetic ketoacidosis adult management guideline",
        ),
        preferred_sources=("ADA", "Endocrine Society", "JBDS", "major diabetes guideline"),
        avoid_queries=("diabetes overview", "high blood sugar general information", "diet for diabetes"),
        rationale="Transcript suggests hyperglycemic crisis, so evidence should target DKA/HHS protocols and electrolyte safety.",
    ),
    LiteratureScenario(
        scenario_id="upper_gi_bleed",
        label="upper gastrointestinal bleeding",
        clinical_frame="acute bleeding syndrome requiring resuscitation, transfusion, and endoscopy timing decisions",
        decision_focus=("risk stratification", "transfusion threshold", "PPI", "endoscopy timing", "anticoagulant reversal"),
        required_any=("吐血", "hematemesis", "melena", "黑便", "上消化道出血", "upper gi bleed", "ugib"),
        supportive_any=("血壓下降", "休克", "hb 下降", "hemoglobin drop", "輸血", "抗凝血", "endoscopy"),
        search_targets=(
            "upper GI bleeding guideline endoscopy timing",
            "upper gastrointestinal bleeding transfusion threshold guideline",
            "nonvariceal upper GI bleeding PPI guideline",
            "anticoagulant reversal gastrointestinal bleeding guideline",
        ),
        preferred_sources=("ACG", "ESGE", "BSG", "major gastroenterology guideline"),
        avoid_queries=("abdominal pain general information", "stomach disease overview", "blood in stool general information"),
        rationale="Transcript suggests acute GI bleeding, so evidence should target resuscitation, transfusion threshold, and endoscopy timing.",
    ),
    LiteratureScenario(
        scenario_id="hyperkalemia_emergency",
        label="hyperkalemia emergency",
        clinical_frame="electrolyte emergency with ECG/cardiac stabilization and potassium-shifting decisions",
        decision_focus=("calcium stabilization", "insulin/glucose", "potassium removal", "dialysis indication"),
        required_any=("高血鉀", "hyperkalemia", "k 上升", "potassium high", "鉀離子", "peaked t", "寬 qrs"),
        supportive_any=("心電圖", "ecg", "腎衰竭", "aki", "洗腎", "dialysis", "arrhythmia"),
        search_targets=(
            "hyperkalemia emergency management guideline calcium insulin glucose",
            "severe hyperkalemia dialysis indication guideline",
            "hyperkalemia ECG changes treatment guideline",
        ),
        preferred_sources=("KDIGO", "UK Kidney Association", "major nephrology guideline"),
        avoid_queries=("potassium diet general information", "kidney disease overview", "electrolyte overview"),
        rationale="Transcript suggests dangerous hyperkalemia, so evidence should target immediate stabilization and potassium removal.",
    ),
    LiteratureScenario(
        scenario_id="anaphylaxis",
        label="anaphylaxis",
        clinical_frame="acute allergic emergency requiring immediate epinephrine and airway planning",
        decision_focus=("intramuscular epinephrine", "airway risk", "observation time", "adjunct medications"),
        required_any=("anaphylaxis", "過敏性休克", "喉頭水腫", "全身過敏", "epinephrine", "腎上腺素"),
        supportive_any=("皮疹", "蕁麻疹", "喘", "低血壓", "wheezing", "angioedema", "食物過敏", "藥物過敏"),
        search_targets=(
            "anaphylaxis guideline intramuscular epinephrine",
            "anaphylaxis emergency management guideline",
            "anaphylaxis observation biphasic reaction guideline",
        ),
        preferred_sources=("WAO", "AAAAI", "EAACI", "major allergy guideline"),
        avoid_queries=("allergy overview", "rash general information", "antihistamine general information"),
        rationale="Transcript suggests anaphylaxis physiology, so evidence should target immediate epinephrine and airway/observation decisions.",
    ),
    LiteratureScenario(
        scenario_id="aortic_dissection",
        label="acute aortic syndrome / aortic dissection",
        clinical_frame="high-risk chest/back pain syndrome requiring anti-impulse therapy and imaging/surgical pathway",
        decision_focus=("CTA diagnosis", "blood pressure and heart rate targets", "beta blockade", "surgical consultation"),
        required_any=("aortic dissection", "主動脈剝離", "撕裂痛", "tearing pain", "縱膈變寬", "widened mediastinum"),
        supportive_any=("胸痛", "背痛", "血壓差", "神經症狀", "syncope", "cta", "d dimer"),
        search_targets=(
            "acute aortic syndrome guideline diagnosis management",
            "aortic dissection anti impulse therapy blood pressure heart rate target",
            "ACC AHA aortic disease guideline acute dissection",
        ),
        preferred_sources=("ACC", "AHA", "ESC", "major vascular/cardiology guideline"),
        avoid_queries=("back pain general information", "chest pain general information", "vascular disease overview"),
        rationale="Transcript suggests acute aortic syndrome, so evidence should target CTA diagnosis and anti-impulse/surgical pathway.",
    ),
)


GENERAL_AVOID_QUERIES: tuple[str, ...] = (
    "general disease overview",
    "patient education overview",
    "general symptoms information",
)

ACUITY_TERMS: tuple[str, ...] = (
    "血壓下降",
    "低血壓",
    "休克",
    "shock",
    "意識改變",
    "低氧",
    "hypoxia",
    "呼吸衰竭",
    "icu",
    "急診",
    "轉加護",
)

OBJECTIVE_SIGNAL_TERMS: tuple[str, ...] = (
    "ecg",
    "心電圖",
    "troponin",
    "lactate",
    "乳酸",
    "ct",
    "cta",
    "血壓",
    "氧氣",
    "spo2",
    "ph",
    "anion gap",
    "hb",
    "hemoglobin",
    "鉀",
    "potassium",
)

MANAGEMENT_TERMS: tuple[str, ...] = (
    "抗生素",
    "antibiotic",
    "輸液",
    "fluid",
    "升壓劑",
    "norepinephrine",
    "pci",
    "抗血小板",
    "anticoagulation",
    "thrombolysis",
    "thrombectomy",
    "insulin",
    "輸血",
    "endoscopy",
    "腎上腺素",
    "epinephrine",
)

TOPIC_SEARCH_TARGETS: dict[str, tuple[str, ...]] = {
    "intracranial_hemorrhage": (
        "intracerebral hemorrhage guideline blood pressure management",
        "intracranial hemorrhage neurosurgical indication guideline",
        "AHA ASA spontaneous intracerebral hemorrhage guideline",
    ),
    "symptomatic_external_hemorrhoids": (
        "hemorrhoids guideline management",
        "external hemorrhoid thrombosis treatment guideline",
        "AGA clinical practice update hemorrhoids",
        "ASCRS hemorrhoids clinical practice guideline",
        "post hemorrhoid procedure care guideline",
    ),
    "longstanding_external_hemorrhoids": (
        "hemorrhoids guideline management",
        "external hemorrhoid thrombosis treatment guideline",
        "AGA clinical practice update hemorrhoids",
        "ASCRS hemorrhoids clinical practice guideline",
        "post hemorrhoid procedure care guideline",
    ),
    "internal_hemorrhoids": (
        "hemorrhoids guideline management",
        "internal hemorrhoids guideline management",
        "AGA clinical practice update hemorrhoids",
        "ASCRS hemorrhoids clinical practice guideline",
    ),
    "hydrocephalus_shunt": (
        "hydrocephalus VP shunt malfunction guideline",
        "external ventricular drain management guideline",
        "adult hydrocephalus shunt management review",
    ),
    "pcp_pneumonia": (
        "Pneumocystis jirovecii pneumonia guideline HIV treatment adjunctive corticosteroids",
        "NIH opportunistic infection guidelines pneumocystis pneumonia",
        "PCP pneumonia diagnosis treatment guideline",
    ),
    "pneumonia": (
        "community acquired pneumonia guideline antibiotic treatment",
        "IDSA ATS pneumonia guideline adult inpatient management",
    ),
    "heart_failure": (
        "heart failure guideline acute decompensated management",
        "AHA ACC heart failure guideline diuretic management",
    ),
    "acute_kidney_injury": (
        "KDIGO acute kidney injury guideline management",
        "acute kidney injury inpatient management guideline",
    ),
    "leukemia": (
        "acute leukemia guideline diagnosis treatment adult",
        "NCCN acute leukemia guideline adult",
    ),
    "lymphoma": (
        "lymphoma guideline diagnosis treatment adult",
        "NCCN lymphoma guideline adult",
    ),
    "multiple_myeloma": (
        "multiple myeloma guideline diagnosis treatment",
        "IMWG multiple myeloma guideline",
    ),
    "febrile_neutropenia": (
        "febrile neutropenia guideline empiric antibiotics adult",
        "IDSA febrile neutropenia guideline",
    ),
    "pancytopenia": (
        "pancytopenia evaluation guideline adult",
        "pancytopenia differential diagnosis review adult",
    ),
    "thrombocytopenia": (
        "thrombocytopenia evaluation guideline adult",
        "immune thrombocytopenia guideline adult",
    ),
    "anemia": (
        "anemia evaluation guideline adult",
        "transfusion threshold guideline hospitalized adult anemia",
    ),
    "sle": (
        "EULAR systemic lupus erythematosus management recommendations",
        "systemic lupus erythematosus guideline treatment adult",
    ),
    "rheumatoid_arthritis": (
        "ACR rheumatoid arthritis treatment guideline",
        "EULAR rheumatoid arthritis management recommendations",
    ),
    "vasculitis": (
        "ANCA associated vasculitis guideline treatment",
        "EULAR vasculitis management recommendations",
    ),
    "gout": (
        "ACR gout guideline acute flare urate lowering therapy",
        "gout management guideline adult",
    ),
    "lupus_nephritis": (
        "KDIGO lupus nephritis guideline",
        "ACR lupus nephritis guideline treatment",
    ),
    "acute_hepatitis": (
        "acute hepatitis evaluation guideline adult",
        "AASLD hepatitis guideline acute liver injury",
    ),
    "acute_liver_failure": (
        "acute liver failure guideline management adult",
        "AASLD acute liver failure position paper",
    ),
    "possible_subconjunctival_hemorrhage": (
        "subconjunctival hemorrhage evaluation",
        "subconjunctival hemorrhage causes",
        "red eye subconjunctival hemorrhage differential",
        "subconjunctival hemorrhage management adult",
    ),
    "cirrhosis_complication": (
        "AASLD cirrhosis ascites hepatic encephalopathy guideline",
        "cirrhosis complication management guideline",
    ),
    "cholangitis": (
        "Tokyo Guidelines acute cholangitis management",
        "acute cholangitis antibiotic drainage guideline",
    ),
    "cholecystitis": (
        "Tokyo Guidelines acute cholecystitis management",
        "acute cholecystitis surgery antibiotic guideline",
    ),
    "choledocholithiasis": (
        "ASGE choledocholithiasis guideline ERCP risk stratification",
        "common bile duct stone management guideline",
    ),
    "pancreatitis": (
        "acute pancreatitis guideline fluid nutrition ERCP",
        "ACG acute pancreatitis guideline management",
    ),
    "hepatocellular_carcinoma": (
        "AASLD hepatocellular carcinoma guideline surveillance treatment",
        "BCLC hepatocellular carcinoma guideline",
    ),
    "appendicitis": (
        "acute appendicitis guideline antibiotics surgery adult",
        "WSES appendicitis guideline adult",
    ),
    "bowel_obstruction": (
        "small bowel obstruction guideline nonoperative management surgery",
        "bowel obstruction management guideline adult",
    ),
    "peritonitis": (
        "secondary peritonitis guideline source control antibiotics",
        "intra abdominal infection guideline source control adult",
    ),
    "diverticulitis": (
        "acute diverticulitis guideline antibiotics surgery adult",
        "AGA diverticulitis guideline management",
    ),
    "hernia": (
        "incarcerated hernia guideline emergency surgery",
        "groin hernia management guideline adult",
    ),
    "surgical_site_infection": (
        "surgical site infection guideline treatment adult",
        "IDSA skin soft tissue infection guideline surgical wound infection",
    ),
    "thermal_injury_burn": (
        "burn wound care guideline adult",
        "acute burn management guideline",
        "American Burn Association burn care guideline",
        "burn resuscitation guideline adult",
    ),
}


def _taxonomy_key(kind: str, label: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", _normalize(label)).strip("_")
    return f"{kind}:{normalized}"


def _is_medical_phrase(phrase: str) -> bool:
    normalized = _normalize(phrase)
    if not normalized:
        return False
    if any(stop in normalized for stop in STOP_PHRASES):
        return False
    if any(pattern.search(normalized) for pattern in MEDICAL_HINT_PATTERNS):
        return True
    tokens = [token for token in re.split(r"\s+", normalized) if token]
    if len(tokens) >= 2 and all(len(token) > 1 for token in tokens):
        return True
    return any(char in phrase for char in "炎癌瘤症病休克感染衰竭中毒梗塞阻塞出血腫瘤")


def _extract_candidate_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    for pattern in ENGLISH_CUE_PATTERNS:
        for match in pattern.finditer(text):
            phrase = re.sub(r"[\s,;:()]+$", "", match.group(1)).strip()
            phrase = re.sub(r"\s+", " ", phrase)
            phrase = re.split(r"\b(?:with|and|for|of|in|from|to)\b", phrase, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if 2 <= len(phrase.split()) <= 4 and _is_medical_phrase(phrase):
                phrases.append(phrase)
    for pattern in CHINESE_CUE_PATTERNS:
        for match in pattern.finditer(text):
            phrase = re.sub(r"\s+", "", match.group(1)).strip()
            if 2 <= len(phrase) <= 18 and _is_medical_phrase(phrase):
                phrases.append(phrase)
    return list(dict.fromkeys(phrases))


def _load_active_taxonomy() -> tuple[list[tuple[str, tuple[str, ...]]], list[tuple[str, tuple[str, ...]]]]:
    return tuple(load_specialty_keyword_sets()), tuple(load_literature_diagnosis_keyword_sets())


def _refresh_literature_taxonomy_candidates(text: str, *, source_type: str, source_id: str) -> dict[str, Any]:
    phrases = _extract_candidate_phrases(text)
    if not phrases:
        return {"ok": True, "items_added": 0, "candidates": []}

    taxonomy = load_yaml_dict(LITERATURE_TAXONOMY_CANDIDATES_FILE)
    if taxonomy.get("_load_error"):
        return {"ok": False, "items_added": 0, "candidates": [], "error": taxonomy["_load_error"]}
    items = list(taxonomy.get("items") or [])
    by_key: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict):
            key = str(item.get("key") or "").strip()
            if key:
                by_key[key] = item

    added: list[dict[str, Any]] = []
    changed = False
    for phrase in phrases:
        kind = "diagnosis"
        label = re.sub(r"[^a-z0-9]+", "_", _normalize(phrase)).strip("_") or "unknown"
        if len(phrase.split()) == 1 and any(char in phrase for char in "癌瘤病"):
            kind = "diagnosis"
        key = _taxonomy_key(kind, label)
        entry = by_key.get(key)
        if entry is None:
            entry = {
                "key": key,
                "status": "watch",
                "kind": kind,
                "label": label,
                "keywords": [phrase],
                "first_seen_source_type": source_type,
                "first_seen_source_id": source_id,
                "last_seen_source_type": source_type,
                "last_seen_source_id": source_id,
                "examples": [phrase],
            }
            by_key[key] = entry
            added.append(entry)
            changed = True
        else:
            keywords = list(dict.fromkeys([*(entry.get("keywords") or []), phrase]))
            if keywords != entry.get("keywords"):
                changed = True
            entry["keywords"] = keywords
            if entry.get("last_seen_source_type") != source_type or entry.get("last_seen_source_id") != source_id:
                changed = True
            entry["last_seen_source_type"] = source_type
            entry["last_seen_source_id"] = source_id
            examples = list(dict.fromkeys([*(entry.get("examples") or []), phrase]))
            if examples[:10] != entry.get("examples"):
                changed = True
            entry["examples"] = examples[:10]

    if changed:
        write_yaml(
            LITERATURE_TAXONOMY_CANDIDATES_FILE,
            {
                "version": "1.0",
                "queue_type": "literature_taxonomy_candidates",
                "items": sorted(by_key.values(), key=lambda item: str(item.get("key") or "")),
            },
        )

    return {"ok": True, "items_added": len(added), "candidates": added}

MEDICAL_HINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(itis|osis|emia|opathy|carcinoma|cancer|syndrome|disease|infection|failure|shock|bleed|bleeding|ulcer)$", re.IGNORECASE),
)

ENGLISH_CUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:diagnosis|assessment|impression|suspected|suspect|rule out|working diagnosis|due to|with)\s+([A-Za-z][A-Za-z0-9\- /]{2,60})", re.IGNORECASE),
    re.compile(r"(?:history of|h/o|post op|postoperative|postoperative)\s+([A-Za-z][A-Za-z0-9\- /]{2,60})", re.IGNORECASE),
)

CHINESE_CUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:診斷|評估|懷疑|考慮|排除|合併|併發)\s*([^\n,，。；：]{2,24})"),
)

STOP_PHRASES: tuple[str, ...] = (
    "blood pressure",
    "heart rate",
    "oxygen",
    "spo2",
    "pain control",
    "follow up",
    "follow-up",
    "patient is",
    "patient was",
    "no change",
    "stable",
    "continue",
    "monitor",
)


def _normalize(text: str) -> str:
    return text.casefold().replace("-", " ").replace("_", " ")


def _matched_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    normalized = _normalize(text)
    matches = []
    for term in terms:
        normalized_term = _normalize(term)
        if normalized_term.isascii() and re.fullmatch(r"[a-z0-9 ]+", normalized_term):
            pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
            if re.search(pattern, normalized):
                matches.append(term)
            continue
        if normalized_term in normalized:
            matches.append(term)
    return matches


SECTION_WEIGHTS: dict[str, float] = {
    "assessment": 5.0,
    "impression": 5.0,
    "problem_list": 4.0,
    "diagnosis": 4.0,
    "plan": 3.0,
    "medication": 2.0,
    "lab_imaging": 1.0,
    "past_history": 1.0,
    "family_history": 0.5,
    "body": 1.0,
}

SECTION_ALIASES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(?:assessment|a/p|ap|impression|assessments?)\b", re.IGNORECASE), "assessment"),
    (re.compile(r"^(?:problem list|problems?)\b", re.IGNORECASE), "problem_list"),
    (re.compile(r"^(?:diagnosis|diagnoses|dx)\b", re.IGNORECASE), "diagnosis"),
    (re.compile(r"^(?:plan|management|treatment plan)\b", re.IGNORECASE), "plan"),
    (re.compile(r"^(?:medication|medications|meds|rx)\b", re.IGNORECASE), "medication"),
    (re.compile(r"^(?:lab|labs|imaging|image|studies|radiology|exam)\b", re.IGNORECASE), "lab_imaging"),
    (re.compile(r"^(?:past history|past medical history|pmh|history)\b", re.IGNORECASE), "past_history"),
    (re.compile(r"^(?:family history|fh)\b", re.IGNORECASE), "family_history"),
)

SPECIALTY_FIELD_WEIGHTS: dict[str, float] = {
    "strong_keywords": 5.0,
    "aliases": 4.0,
    "medications": 2.0,
    "labs_imaging": 1.0,
    "procedures": 2.0,
    "weak_clues": 1.0,
    "negative_context": -3.0,
}

DIAGNOSIS_KEYWORD_WEIGHT = 5.0
RULE_OUT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:rule out|r/o|ro|no evidence of|no signs of|without evidence of|negative for)\b", re.IGNORECASE),
)
HISTORY_ONLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:history of|h/o|past history of|prior|previous|s/p|status post)\b", re.IGNORECASE),
)


def _section_name(raw_heading: str) -> str | None:
    normalized = raw_heading.strip().strip("#*:- ").casefold()
    for pattern, section in SECTION_ALIASES:
        if pattern.search(normalized):
            return section
    return None


def _split_clinical_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_name = "body"
    current_lines: list[str] = []
    heading_pattern = re.compile(r"^\s{0,3}(?:#{1,6}\s*)?([A-Za-z][A-Za-z /_-]{1,40}|A/P|PMH|FH|Dx|Rx)\s*:\s*(.*)$")
    for line in text.splitlines():
        match = heading_pattern.match(line)
        section = _section_name(match.group(1)) if match else None
        if match and section:
            if current_lines:
                sections.append({"section": current_name, "text": "\n".join(current_lines).strip()})
            current_name = section
            current_lines = [match.group(2).strip()] if match.group(2).strip() else []
            continue
        current_lines.append(line)
    if current_lines:
        sections.append({"section": current_name, "text": "\n".join(current_lines).strip()})
    return [section for section in sections if section["text"]] or [{"section": "body", "text": text}]


def _term_pattern(term: str) -> re.Pattern[str]:
    normalized_term = _normalize(term)
    if normalized_term.isascii() and re.fullmatch(r"[a-z0-9 ]+", normalized_term):
        return re.compile(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])")
    return re.compile(re.escape(normalized_term))


def _has_context_before(normalized_text: str, start: int, patterns: tuple[re.Pattern[str], ...], *, window: int = 48) -> bool:
    context = normalized_text[max(0, start - window) : start]
    return any(pattern.search(context) for pattern in patterns)


def _score_terms_in_sections(
    sections: list[dict[str, Any]],
    terms: tuple[str, ...],
    *,
    field: str,
    keyword_weight: float,
) -> tuple[float, list[str], list[dict[str, Any]], list[str]]:
    score = 0.0
    matched_terms: list[str] = []
    matched_details: list[dict[str, Any]] = []
    negative_matches: list[str] = []
    for section in sections:
        section_name = section["section"]
        section_weight = SECTION_WEIGHTS.get(section_name, 1.0)
        normalized = _normalize(section["text"])
        for term in terms:
            pattern = _term_pattern(term)
            matches = list(pattern.finditer(normalized))
            if not matches:
                continue
            matched_terms.append(term)
            for match in matches:
                delta = keyword_weight * section_weight
                modifiers: list[str] = []
                if field != "negative_context":
                    if _has_context_before(normalized, match.start(), RULE_OUT_PATTERNS):
                        delta += -2.0 * section_weight
                        modifiers.append("rule_out")
                    if section_name in {"past_history", "family_history"} or _has_context_before(normalized, match.start(), HISTORY_ONLY_PATTERNS):
                        delta += -1.0 * section_weight
                        modifiers.append("history_only")
                if delta < 0:
                    negative_matches.append(term)
                elif modifiers:
                    negative_matches.append(term)
                score += delta
                matched_details.append(
                    {
                        "term": term,
                        "field": field,
                        "section": section_name,
                        "section_weight": section_weight,
                        "keyword_weight": keyword_weight,
                        "delta": delta,
                        "modifiers": modifiers,
                    }
                )
    return score, list(dict.fromkeys(matched_terms)), matched_details, list(dict.fromkeys(negative_matches))


def _clean_record_terms(item: dict[str, Any], field: str) -> tuple[str, ...]:
    legacy_field = {"strong_keywords": "keywords", "weak_clues": "clues"}.get(field)
    terms: list[str] = []
    for candidate_field in (field, legacy_field):
        if not candidate_field:
            continue
        values = item.get(candidate_field)
        if isinstance(values, list):
            terms.extend(str(value).strip() for value in values if str(value).strip())
    for subcategory in item.get("subcategories") or []:
        if not isinstance(subcategory, dict):
            continue
        for candidate_field in (field, legacy_field):
            if not candidate_field:
                continue
            values = subcategory.get(candidate_field)
            if isinstance(values, list):
                terms.extend(str(value).strip() for value in values if str(value).strip())
    return tuple(dict.fromkeys(terms))


def _score_specialty_records(text: str) -> list[dict[str, Any]]:
    sections = _split_clinical_sections(text)
    required_any = load_specialty_required_any()
    candidates: list[dict[str, Any]] = []
    for item in load_specialty_records():
        label = str(item.get("id") or "").strip()
        if not label:
            continue
        total = 0.0
        matched_terms: list[str] = []
        matched_by_field: dict[str, list[str]] = {}
        matched_by_section: dict[str, list[str]] = {}
        negative_matches: list[str] = []
        details: list[dict[str, Any]] = []
        for field, keyword_weight in SPECIALTY_FIELD_WEIGHTS.items():
            terms = _clean_record_terms(item, field)
            if not terms:
                continue
            field_score, field_matches, field_details, field_negatives = _score_terms_in_sections(
                sections,
                terms,
                field=field,
                keyword_weight=keyword_weight,
            )
            total += field_score
            if field_matches:
                matched_by_field[field] = field_matches
                matched_terms.extend(field_matches)
            negative_matches.extend(field_negatives)
            details.extend(field_details)
        for detail in details:
            matched_by_section.setdefault(detail["section"], [])
            matched_by_section[detail["section"]].append(detail["term"])
        matched_terms = list(dict.fromkeys(matched_terms))
        required_matches = _matched_terms(text, required_any.get(label, ()))
        if matched_terms and label in required_any and not required_matches:
            continue
        if matched_terms and total > 0:
            candidates.append(
                {
                    "label": label,
                    "score": round(total, 2),
                    "matched_terms": matched_terms,
                    "matched_by_field": matched_by_field,
                    "matched_by_section": {key: list(dict.fromkeys(value)) for key, value in matched_by_section.items()},
                    "negative_matches": list(dict.fromkeys(negative_matches)),
                    "required_matches": required_matches,
                    "match_details": details[:20],
                }
            )
    return sorted(
        candidates,
        key=lambda item: (item["score"], len(item["matched_terms"]), sum(len(term) for term in item["matched_terms"])),
        reverse=True,
    )


def _score_diagnosis_records(text: str) -> list[dict[str, Any]]:
    sections = _split_clinical_sections(text)
    candidates: list[dict[str, Any]] = []
    for item in load_literature_diagnosis_records():
        label = str(item.get("label") or "").strip()
        terms = tuple(str(value).strip() for value in item.get("keywords") or () if str(value).strip())
        if not label or not terms:
            continue
        score, matched_terms, details, negative_matches = _score_terms_in_sections(
            sections,
            terms,
            field="keywords",
            keyword_weight=DIAGNOSIS_KEYWORD_WEIGHT,
        )
        if matched_terms and score > 0:
            candidates.append(
                {
                    "label": label,
                    "score": round(score, 2),
                    "matched_terms": matched_terms,
                    "matched_by_field": {"keywords": matched_terms},
                    "matched_by_section": {
                        key: list(dict.fromkeys(detail["term"] for detail in details if detail["section"] == key))
                        for key in sorted({detail["section"] for detail in details})
                    },
                    "negative_matches": negative_matches,
                    "match_details": details[:20],
                }
            )
    return sorted(
        candidates,
        key=lambda item: (item["score"], len(item["matched_terms"]), sum(len(term) for term in item["matched_terms"])),
        reverse=True,
    )


def _normalize_topic_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(label or "").casefold()).strip("_")


def _normalize_specialty_label(label: str) -> str:
    return canonicalize_specialty_id(re.sub(r"[^a-z0-9]+", "_", str(label or "").casefold()).strip("_"))


def _extract_hermes_routing_payload(text: str) -> dict[str, Any] | None:
    def _parse_routing_block(block: str) -> dict[str, Any] | None:
        block = block.strip()
        if not block:
            return None
        if block.startswith("```"):
            block = re.sub(r"^```(?:json)?\s*", "", block, flags=re.IGNORECASE)
            block = re.sub(r"\s*```$", "", block)
        start = block.find("{")
        end = block.rfind("}")
        if start >= 0 and end > start:
            block = block[start : end + 1]
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        routing_keys = {"primary_specialty", "primary_service", "service", "diagnosis_topics", "topics"}
        if not routing_keys.intersection(payload):
            return None
        return payload

    lines = str(text or "").splitlines()
    for index, line in enumerate(lines):
        if line.strip() not in HERMES_ROUTING_HEADINGS:
            continue
        block_lines: list[str] = []
        in_fence = False
        for follow in lines[index + 1 :]:
            stripped = follow.strip()
            if stripped.startswith("## ") and block_lines:
                break
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if not in_fence and not stripped and not block_lines:
                continue
            block_lines.append(follow)
        block = "\n".join(block_lines).strip()
        payload = _parse_routing_block(block)
        if payload is not None:
            return payload
        return None
    for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", str(text or ""), flags=re.IGNORECASE | re.DOTALL):
        payload = _parse_routing_block(match.group(1))
        if payload is not None:
            return payload
    return None


def _routing_confidence_value(confidence: str | None) -> float:
    mapping = {"high": 0.92, "moderate": 0.78, "medium": 0.78, "low": 0.58, "uncertain": 0.5}
    return mapping.get(str(confidence or "").strip().casefold(), 0.68)


def _hermes_routed_classification(text: str) -> dict[str, Any] | None:
    payload = _extract_hermes_routing_payload(text)
    if not isinstance(payload, dict):
        return None
    primary = _normalize_specialty_label(
        str(payload.get("primary_specialty") or payload.get("primary_service") or payload.get("service") or "").strip()
    )
    diagnosis_topics_raw = payload.get("diagnosis_topics") or payload.get("topics") or []
    if isinstance(diagnosis_topics_raw, str):
        diagnosis_topics_raw = [diagnosis_topics_raw]
    diagnosis_topics = [
        topic
        for topic in (_normalize_topic_label(str(item)) for item in diagnosis_topics_raw if str(item).strip())
        if topic
    ]
    confidence = str(payload.get("confidence") or payload.get("confidence_level") or "moderate").strip().casefold()
    diagnosis_candidates: list[dict[str, Any]] = []
    for topic in diagnosis_topics[:5]:
        diagnosis_candidates.append(
            {
                "label": topic,
                "score": 10.0,
                "matched_terms": [topic],
                "matched_by_field": {"routing": [topic]},
                "matched_by_section": {"routing": [topic]},
                "negative_matches": [],
                "match_details": [
                    {
                        "term": topic,
                        "field": "routing",
                        "section": "routing",
                        "section_weight": 1.0,
                        "keyword_weight": 1.0,
                        "delta": 1.0,
                        "modifiers": [],
                    }
                ],
            }
        )
    service_candidates: list[dict[str, Any]] = []
    if primary:
        service_candidates.append(
            {
                "label": primary,
                "score": round(100.0 * _routing_confidence_value(confidence), 2),
                "matched_terms": diagnosis_topics[:3] or [primary],
                "matched_by_field": {"routing": diagnosis_topics[:3] or [primary]},
                "matched_by_section": {"routing": diagnosis_topics[:3] or [primary]},
                "negative_matches": [],
                "required_matches": [],
                "match_details": [
                    {
                        "term": term,
                        "field": "routing",
                        "section": "routing",
                        "section_weight": 1.0,
                        "keyword_weight": 1.0,
                        "delta": 1.0,
                        "modifiers": [],
                    }
                    for term in (diagnosis_topics[:3] or [primary])
                ],
            }
        )
    return {
        "primary_specialty": primary,
        "primary_service": primary,
        "legacy_service_aliases": list(load_specialty_legacy_aliases().get(primary, ())) if primary else [],
        "service_candidates": service_candidates,
        "diagnosis_topics": diagnosis_topics,
        "diagnosis_candidates": diagnosis_candidates,
        "confidence": confidence if confidence in {"high", "moderate", "medium", "low", "uncertain"} else "moderate",
        "classification_basis": "Hermes routing block parsed from result.hermes.md; taxonomy fallback used only when absent.",
        "routing_source": "hermes",
    }


def _score_scenario(text: str, scenario: LiteratureScenario) -> dict:
    required_matches = _matched_terms(text, scenario.required_any)
    supportive_matches = _matched_terms(text, scenario.supportive_any)
    score = len(required_matches) * 3 + len(supportive_matches)
    return {
        "scenario_id": scenario.scenario_id,
        "label": scenario.label,
        "clinical_frame": scenario.clinical_frame,
        "decision_focus": list(scenario.decision_focus),
        "score": score,
        "required_matches": required_matches,
        "supportive_matches": supportive_matches,
        "matched": bool(required_matches) and score >= 3,
    }


def _clinical_context(text: str) -> dict:
    return {
        "acuity_signals": _matched_terms(text, ACUITY_TERMS),
        "objective_signals": _matched_terms(text, OBJECTIVE_SIGNAL_TERMS),
        "management_signals": _matched_terms(text, MANAGEMENT_TERMS),
        "planning_warning": (
            "Do not choose evidence targets from symptoms alone. Use objective findings, working diagnosis, acuity, and management decisions to infer the guideline domain."
        ),
    }


def infer_clinical_classification(text: str) -> dict:
    hermes_routed = _hermes_routed_classification(text)
    if hermes_routed:
        return hermes_routed
    normalized = _normalize(text)
    hemorrhoid_like = (
        any(token in normalized for token in ("perianal", "anal", "rectal", "肛周", "肛門", "屁股"))
        and any(token in normalized for token in ("bleed", "bleeding", "出血"))
        and any(
            token in normalized
            for token in ("bowel movement", "bowel movements", "defecation", "排便", "sitz bath", "warm sitz baths", "ointment", "藥膏", "溫水作浴")
        )
    )
    if hemorrhoid_like:
        primary_specialty = "gastroenterology_hepatology"
        diagnosis_topics = ["symptomatic_external_hemorrhoids"]
        legacy_aliases = list(load_specialty_legacy_aliases().get(primary_specialty, ())) if primary_specialty else []
        service_candidates = [
            {
                "label": primary_specialty,
                "score": 18.0,
                "matched_terms": ["perianal", "bleeding", "bowel movements", "sitz bath", "ointment"],
                "matched_by_field": {
                    "strong_keywords": ["perianal", "bleeding"],
                    "weak_clues": ["bowel movements", "sitz bath", "ointment"],
                },
                "matched_by_section": {"body": ["perianal", "bleeding", "bowel movements", "sitz bath", "ointment"]},
                "negative_matches": [],
                "required_matches": [],
                "match_details": [
                    {
                        "term": "perianal",
                        "field": "strong_keywords",
                        "section": "body",
                        "section_weight": 1.0,
                        "keyword_weight": 5.0,
                        "delta": 5.0,
                        "modifiers": [],
                    },
                    {
                        "term": "bleeding",
                        "field": "strong_keywords",
                        "section": "body",
                        "section_weight": 1.0,
                        "keyword_weight": 5.0,
                        "delta": 5.0,
                        "modifiers": [],
                    },
                    {
                        "term": "bowel movements",
                        "field": "weak_clues",
                        "section": "body",
                        "section_weight": 1.0,
                        "keyword_weight": 1.0,
                        "delta": 1.0,
                        "modifiers": [],
                    },
                    {
                        "term": "sitz bath",
                        "field": "weak_clues",
                        "section": "body",
                        "section_weight": 1.0,
                        "keyword_weight": 1.0,
                        "delta": 1.0,
                        "modifiers": [],
                    },
                    {
                        "term": "ointment",
                        "field": "weak_clues",
                        "section": "body",
                        "section_weight": 1.0,
                        "keyword_weight": 1.0,
                        "delta": 1.0,
                        "modifiers": [],
                    },
                ],
            }
        ]
        return {
            "primary_specialty": primary_specialty,
            "primary_service": primary_specialty,
            "legacy_service_aliases": legacy_aliases,
            "service_candidates": service_candidates,
            "diagnosis_topics": diagnosis_topics,
            "diagnosis_candidates": [],
            "confidence": "moderate",
            "classification_basis": (
                "Heuristic hemorrhoid-like pattern detected from perianal bleeding with bowel-movement association and local treatment language. "
                "Hermes routing block was absent, so this fallback is used to keep hemorrhoid cases out of generic acute-care search routing."
            ),
            "routing_source": "heuristic",
        }
    service_matches = _score_specialty_records(text)
    diagnosis_matches = _score_diagnosis_records(text)
    primary_specialty = service_matches[0]["label"] if service_matches else ""
    diagnosis_topics = [item["label"] for item in diagnosis_matches]
    confidence = "low"
    if diagnosis_matches and service_matches:
        confidence = "high" if service_matches[0]["score"] >= 25 and diagnosis_matches[0]["score"] >= 10 else "moderate"
    elif diagnosis_matches or service_matches:
        confidence = "moderate"
    legacy_aliases = list(load_specialty_legacy_aliases().get(primary_specialty, ())) if primary_specialty else []

    return {
        "primary_specialty": primary_specialty,
        "primary_service": primary_specialty,
        "legacy_service_aliases": legacy_aliases,
        "service_candidates": service_matches[:3],
        "diagnosis_topics": diagnosis_topics,
        "diagnosis_candidates": diagnosis_matches[:5],
        "confidence": confidence,
        "classification_basis": (
            "Deterministic weighted scoring from taxonomy/clinical_specialty_map.yml "
            "and taxonomy/literature_taxonomy_approved.yml only. "
            "Use as search routing only; not a diagnosis confirmation."
        ),
        "routing_source": "taxonomy",
    }


def _topic_search_targets(diagnosis_topics: list[str]) -> list[str]:
    targets: list[str] = []
    for topic in diagnosis_topics:
        targets.extend(TOPIC_SEARCH_TARGETS.get(topic, ()))
        if topic not in TOPIC_SEARCH_TARGETS:
            targets.append(f"{topic.replace('_', ' ')} guideline")
            targets.append(f"{topic.replace('_', ' ')} treatment guideline")
    return list(dict.fromkeys(targets))


def _keyword_fallback_targets(text: str) -> list[str]:
    lowered = str(text or "").casefold()
    targets: list[str] = []
    hemorrhoid_like = any(token in lowered for token in ("hemorrhoid", "haemorrhoid", "痔瘡", "外痔", "內痔", "thrombosed external hemorrhoid"))
    hemorrhoid_like = hemorrhoid_like or (
        any(token in lowered for token in ("perianal", "anal", "rectal", "肛周", "肛門", "屁股"))
        and any(token in lowered for token in ("bleed", "bleeding", "出血"))
        and any(token in lowered for token in ("bowel movement", "bowel movements", "defecation", "排便", "sitz bath", "warm sitz baths", "ointment", "藥膏", "溫水作浴"))
    )
    if hemorrhoid_like:
        targets.extend(
            [
                "hemorrhoids guideline management",
                "external hemorrhoid thrombosis treatment guideline",
                "AGA clinical practice update hemorrhoids",
                "ASCRS hemorrhoids clinical practice guideline",
                "post hemorrhoid procedure care guideline",
            ]
        )
    return list(dict.fromkeys(targets))


def _context_search_targets(context: dict[str, Any], *, diagnosis_topics: list[str] | None = None) -> list[str]:
    targets: list[str] = []
    suppress_antibiotic = bool(diagnosis_topics)
    suppressed_terms = {
        "antibiotic",
        "antibiotics",
        "abx",
        "抗生素",
        "抗生素藥水",
        "抗生素藥膏",
        "fluid",
        "輸液",
    }
    for key in ("objective_signals", "management_signals", "acuity_signals"):
        values = context.get(key)
        if not isinstance(values, list):
            continue
        for value in values[:4]:
            term = str(value).strip()
            if not term:
                continue
            lowered = term.casefold()
            if suppress_antibiotic and (lowered in suppressed_terms or term in suppressed_terms):
                continue
            targets.append(f"{term} guideline")
            if key == "acuity_signals":
                targets.append(f"{term} acute management guideline")
    return list(dict.fromkeys(targets))


def _service_search_targets(classification: dict[str, Any]) -> list[str]:
    service = str(classification.get("primary_service") or "").strip().replace("_", " ")
    if not service:
        return []
    targets: list[str] = []
    service_defaults: dict[str, tuple[str, ...]] = {
        "infectious disease": (
            "infectious disease inpatient antibiotic guideline",
            "community acquired pneumonia antibiotic guideline adult inpatient",
            "sepsis empiric antibiotic guideline adult",
        ),
        "gastroenterology hepatology": (
            "hemorrhoids guideline management",
            "external hemorrhoid thrombosis treatment guideline",
        ),
        "general surgery": (
            "burn wound care guideline adult",
            "acute burn management guideline adult",
        ),
        "pulmonology": (
            "adult inpatient pulmonary infection guideline",
            "community acquired pneumonia guideline adult",
        ),
        "cardiology": (
            "acute coronary syndrome guideline adult inpatient",
            "heart failure acute decompensation guideline",
        ),
        "neurology": (
            "acute stroke guideline blood pressure management",
            "intracerebral hemorrhage guideline adult inpatient",
        ),
        "obstetrics gynecology": (
            "ASRM hydrosalpinx infertility guideline",
            "ESHRE endometriosis endometrioma infertility guideline",
            "tubal factor infertility IVF hydrosalpinx guideline",
        ),
    }
    targets.extend(service_defaults.get(service, (f"{service} adult inpatient guideline",)))
    for candidate in classification.get("service_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        for term in (candidate.get("matched_terms") or [])[:4]:
            text = str(term).strip()
            if not text:
                continue
            targets.append(f"{service} {text} guideline")
            targets.append(f"{service} {text} treatment guideline")
        break
    return list(dict.fromkeys(targets))


def plan_literature_queries(transcript: str, *, source_type: str = "transcript") -> dict:
    classification = infer_clinical_classification(transcript)
    context = _clinical_context(transcript)
    scenario_scores = [_score_scenario(transcript, scenario) for scenario in SCENARIOS]
    matched_scores = [score for score in scenario_scores if score["matched"]]
    matched_scores.sort(key=lambda item: item["score"], reverse=True)

    selected = []
    search_targets = []
    preferred_sources = []
    avoid_queries = list(GENERAL_AVOID_QUERIES)
    rationales = []

    for score in matched_scores[:2]:
        scenario = next(item for item in SCENARIOS if item.scenario_id == score["scenario_id"])
        selected.append(score)
        search_targets.extend(scenario.search_targets)
        preferred_sources.extend(scenario.preferred_sources)
        avoid_queries.extend(scenario.avoid_queries)
        rationales.append(scenario.rationale)

    topic_targets = _topic_search_targets(classification["diagnosis_topics"])
    search_targets.extend(topic_targets)
    keyword_targets = _keyword_fallback_targets(transcript)
    search_targets.extend(keyword_targets)
    context_targets = _context_search_targets(context, diagnosis_topics=classification["diagnosis_topics"])
    search_targets.extend(context_targets)
    service_targets = _service_search_targets(classification)
    search_targets.extend(service_targets)
    search_targets = list(dict.fromkeys(search_targets))

    if not search_targets:
        # Keep auto-literature forward progress even when classification is weak.
        search_targets = [
            "inpatient acute care guideline",
            "hospital medicine diagnostic and treatment guideline",
            "evidence-based acute management recommendations",
        ]

    if not selected:
        inferred_targets = list(dict.fromkeys(search_targets))
        return {
            "ok": True,
            "action": "literature-plan",
            "source_type": source_type,
            "clinical_classification": classification,
            "selected_scenarios": [],
            "confidence": classification["confidence"] if inferred_targets else "low",
            "clinical_context": context,
            "search_targets": inferred_targets,
            "preferred_sources": [],
            "avoid_queries": list(GENERAL_AVOID_QUERIES),
            "query_construction_rules": [
                "Infer the working syndrome from objective findings and treatment decisions before searching.",
                "Use query shape: [suspected syndrome/diagnosis] + guideline + [decision point].",
                "If only a symptom is available, ask for the clinical question instead of searching broad symptom overviews.",
                "Prefer society guidelines and high-quality reviews over patient education pages.",
            ],
            "planner_instruction": (
                "No predefined high-specificity scenario was detected. Use the clinical_classification and objective context to build focused guideline queries. "
                "Do not search broad patient-education pages or generic symptom overviews."
            ),
            "rationale": "No predefined scenario matched; query targets were inferred from diagnosis/context and fallback acute-care guidance templates.",
            "debug_scores": scenario_scores,
        }

    return {
        "ok": True,
        "action": "literature-plan",
        "source_type": source_type,
        "clinical_classification": classification,
        "selected_scenarios": selected,
        "confidence": "high" if selected[0]["score"] >= 5 else "moderate",
        "clinical_context": context,
        "search_targets": list(dict.fromkeys(search_targets)),
        "preferred_sources": list(dict.fromkeys(preferred_sources)),
        "avoid_queries": list(dict.fromkeys(avoid_queries)),
        "query_construction_rules": [
            "Search the selected syndrome/guideline domain, not the presenting symptom by itself.",
            "Include management decision points such as timing, eligibility, medication choice, or escalation threshold.",
            "Prefer society guidelines and consensus statements before general reviews.",
            "Use avoid_queries as a hard block list unless the user explicitly asks for patient education.",
        ],
        "planner_instruction": (
            "Search guideline/evidence sources for the selected clinical context and decision focus only. "
            "Do not downgrade to generic symptom, organ-system, or disease-overview searches."
        ),
        "rationale": " ".join(rationales),
        "debug_scores": scenario_scores,
    }


def _http_get_text(url: str, *, timeout: int = 20) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "ward-pipeline-literature/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _browser_type(playwright: Any) -> Any:
    browser_name = str(os.environ.get("WARD_OPENEVIDENCE_BROWSER", "chromium")).strip().lower()
    if browser_name == "webkit":
        return playwright.webkit
    if browser_name == "firefox":
        return playwright.firefox
    return playwright.chromium


def openevidence_login(*, timeout: int = 600) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {
            "ok": False,
            "action": "openevidence-login",
            "message": f"playwright unavailable: {exc}",
        }

    OPENEVIDENCE_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    OPENEVIDENCE_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    browser_channel = str(os.environ.get("WARD_OPENEVIDENCE_BROWSER_CHANNEL", "")).strip()
    launch_args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
    with sync_playwright() as playwright:
        browser = _browser_type(playwright)
        launch_kwargs: dict[str, Any] = {"headless": False}
        if browser is playwright.chromium:
            launch_kwargs["args"] = launch_args
            if browser_channel:
                launch_kwargs["channel"] = browser_channel
            context = browser.launch_persistent_context(str(OPENEVIDENCE_BROWSER_PROFILE_DIR), **launch_kwargs)
        else:
            context = browser.launch_persistent_context(str(OPENEVIDENCE_BROWSER_PROFILE_DIR), **launch_kwargs)
        page = context.new_page()
        page.goto(OPENEVIDENCE_HOME_URL, wait_until="domcontentloaded", timeout=timeout * 1000)
        print("\nOpenEvidence login page opened. Complete login in browser. The session will be saved automatically.")
        try:
            input("Press Enter after login is complete...\n")
        except EOFError:
            page.wait_for_timeout(timeout * 1000)
        page.wait_for_timeout(2000)
        is_logged_out = False
        try:
            is_logged_out = page.get_by_role("button", name=re.compile(r"Log In", re.IGNORECASE)).count() > 0
        except Exception:
            is_logged_out = False
        if is_logged_out:
            context.close()
            return {
                "ok": False,
                "action": "openevidence-login",
                "message": "login not detected (Log In button still visible); session not saved",
            }
        context.storage_state(path=str(OPENEVIDENCE_STORAGE_STATE_FILE))
        context.close()
    return {
        "ok": True,
        "action": "openevidence-login",
        "storage_state_path": str(OPENEVIDENCE_STORAGE_STATE_FILE),
    }


def _openevidence_extract_results(page: Any, *, max_results: int) -> list[dict[str, str]]:
    items = page.evaluate(
        """
() => {
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const seen = new Set();
  const results = [];
  for (const a of anchors) {
    const href = (a.getAttribute("href") || "").trim();
    const text = (a.textContent || "").trim().replace(/\\s+/g, " ");
    if (!href || !text) continue;
    const abs = href.startsWith("http") ? href : new URL(href, window.location.origin).toString();
    const low = (abs + " " + text).toLowerCase();
    if (seen.has(abs)) continue;
    if (low.includes("/api/auth/") || low.includes("/login") || low.includes("/signup") || low.includes("sign up")) continue;
    if (low.includes("cookie") || low.includes("privacy") || low.includes("terms")) continue;
    if (text.length < 15) continue;
    if (!/pubmed|nejm|jamanetwork|thelancet|bmj|nih|who|guideline|doi|uptodate|aacr|nature|science/i.test(low)) continue;
    seen.add(abs);
    results.push({ title: text, url: abs });
  }
  return results;
}
"""
    )
    results: list[dict[str, str]] = []
    for item in items:
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not title or not url:
            continue
        results.append({"title": title, "url": url})
        if len(results) >= max_results:
            break
    return results


def _openevidence_run_search(query: str, *, max_results: int, timeout: int = 30) -> list[dict[str, str]]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(f"playwright unavailable: {exc}") from exc

    if not OPENEVIDENCE_STORAGE_STATE_FILE.exists():
        raise RuntimeError("missing OpenEvidence login session; run `ward openevidence-login` first")

    headless = str(os.environ.get("WARD_OPENEVIDENCE_HEADLESS", "1")).strip() != "0"
    browser_channel = str(os.environ.get("WARD_OPENEVIDENCE_BROWSER_CHANNEL", "")).strip()
    launch_args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
    with sync_playwright() as playwright:
        browser_type = _browser_type(playwright)
        launch_kwargs: dict[str, Any] = {"headless": headless}
        if browser_type is playwright.chromium:
            launch_kwargs["args"] = launch_args
            if browser_channel:
                launch_kwargs["channel"] = browser_channel
        browser = browser_type.launch(**launch_kwargs)
        context = browser.new_context(storage_state=str(OPENEVIDENCE_STORAGE_STATE_FILE))
        page = context.new_page()
        page.goto(OPENEVIDENCE_HOME_URL, wait_until="domcontentloaded", timeout=timeout * 1000)
        typed = False
        selectors = (
            "textarea",
            "input[type='search']",
            "input[placeholder*='Search' i]",
            "input[placeholder*='Ask' i]",
            "textarea[placeholder*='Ask' i]",
            "textarea[placeholder*='Search' i]",
            "input[type='text']",
        )
        for selector in selectors:
            locator = page.locator(selector).first
            if locator.count() == 0:
                continue
            try:
                locator.click(timeout=1000)
                locator.fill(query, timeout=1500)
                locator.press("Enter", timeout=1500)
                typed = True
                break
            except Exception:
                continue
        if not typed:
            context.close()
            browser.close()
            raise RuntimeError("could not locate OpenEvidence search input")

        page.wait_for_timeout(5000)
        results = _openevidence_extract_results(page, max_results=max_results)
        context.close()
        browser.close()
        return results


def _openevidence_extract_narrative(page: Any) -> str:
    text = page.evaluate(
        """
() => {
  const badPhrases = [
    "OpenEvidence has signed content agreements",
    "weekly question limit",
    "Add questions to your favorites",
    "Log In",
    "Sign Up",
  ];
  const squashForCheck = (s) => (s || "").replace(/\\s+/g, " ").toLowerCase();
  const looksBad = (s) => {
    const x = squashForCheck(s);
    return badPhrases.some(p => x.includes(p.toLowerCase()));
  };
  const normalizeBlock = (s) => {
    const lines = String(s || "")
      .replace(/\\r/g, "")
      .split("\\n")
      .map(line => line.replace(/[ \\t]+$/g, ""));
    return lines.join("\\n").trim();
  };
  const selectors = [
    "[data-testid*='assistant' i]",
    "[data-testid*='message' i]",
    "[class*='assistant' i]",
    "[class*='message' i]",
    "article",
    "[data-testid*='answer' i]",
    "[class*='answer' i]",
    "[class*='response' i]",
    "main",
  ];
  const candidates = [];
  for (const sel of selectors) {
    for (const node of document.querySelectorAll(sel)) {
      const t = normalizeBlock(node.innerText || "");
      if (t.length < 120) continue;
      if (looksBad(t)) continue;
      candidates.push(t);
    }
  }
  if (candidates.length > 0) {
    candidates.sort((a, b) => b.length - a.length);
    return candidates[0];
  }
  const paras = Array.from(document.querySelectorAll("p, li"))
    .map(n => normalizeBlock(n.innerText || ""))
    .filter(t => t.length >= 40 && !looksBad(t));
  return paras.join("\\n\\n");
}
"""
    )
    raw = str(text or "").replace("\r", "")
    filtered_lines: list[str] = []
    for line in raw.split("\n"):
        stripped = line.rstrip()
        check = " ".join(stripped.split()).lower()
        if (
            "openevidence has signed content agreements" in check
            or "weekly question limit" in check
            or "add questions to your favorites" in check
        ):
            continue
        filtered_lines.append(stripped)
    # Preserve paragraph breaks while preventing excessive empty lines.
    normalized_lines: list[str] = []
    blank_run = 0
    for line in filtered_lines:
        if line.strip():
            blank_run = 0
            normalized_lines.append(line)
            continue
        blank_run += 1
        if blank_run <= 1:
            normalized_lines.append("")
    return "\n".join(normalized_lines).strip()


def _openevidence_run_narrative(query: str, *, timeout: int = 45) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        return {"ok": False, "source": "browser", "error": f"playwright unavailable: {exc}"}

    if not OPENEVIDENCE_STORAGE_STATE_FILE.exists():
        return {"ok": False, "source": "browser", "error": "missing OpenEvidence login session"}

    headless = str(os.environ.get("WARD_OPENEVIDENCE_HEADLESS", "1")).strip() != "0"
    browser_channel = str(os.environ.get("WARD_OPENEVIDENCE_BROWSER_CHANNEL", "")).strip()
    launch_args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
    try:
        with sync_playwright() as playwright:
            browser_type = _browser_type(playwright)
            launch_kwargs: dict[str, Any] = {"headless": headless}
            if browser_type is playwright.chromium:
                launch_kwargs["args"] = launch_args
                if browser_channel:
                    launch_kwargs["channel"] = browser_channel
            browser = browser_type.launch(**launch_kwargs)
            context = browser.new_context(storage_state=str(OPENEVIDENCE_STORAGE_STATE_FILE))
            page = context.new_page()
            page.goto(OPENEVIDENCE_HOME_URL, wait_until="domcontentloaded", timeout=timeout * 1000)
            typed = False
            selectors = (
                "textarea",
                "input[type='search']",
                "input[placeholder*='Search' i]",
                "input[placeholder*='Ask' i]",
                "textarea[placeholder*='Ask' i]",
                "textarea[placeholder*='Search' i]",
                "input[type='text']",
            )
            for selector in selectors:
                locator = page.locator(selector).first
                if locator.count() == 0:
                    continue
                try:
                    locator.click(timeout=1000)
                    locator.fill(query, timeout=1500)
                    locator.press("Enter", timeout=1500)
                    typed = True
                    break
                except Exception:
                    continue
            if not typed:
                context.close()
                browser.close()
                return {"ok": False, "source": "browser", "error": "could not locate OpenEvidence search input"}

            # Wait for answer content to stabilize to avoid capturing UI placeholders.
            narrative = ""
            stable_rounds = 0
            prev_len = 0
            for _ in range(12):
                page.wait_for_timeout(2500)
                current = _openevidence_extract_narrative(page)
                current_len = len(current)
                if current_len > 0:
                    narrative = current
                if abs(current_len - prev_len) <= 24 and current_len >= 200:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                prev_len = current_len
                if stable_rounds >= 2:
                    break
            context.close()
            browser.close()
            if not narrative:
                return {"ok": False, "source": "browser", "error": "empty narrative"}
            return {"ok": True, "source": "browser", "query": query, "text": narrative}
    except Exception as exc:
        return {"ok": False, "source": "browser", "error": str(exc)}


def _openevidence_provider() -> str:
    value = str(os.environ.get(OPENEVIDENCE_PROVIDER_ENV, "auto")).strip().lower()
    if value in {"mcp", "browser", "auto"}:
        return value
    return "auto"


def _openevidence_mcp_search(query: str, *, max_results: int, timeout: int = 30) -> list[dict[str, str]]:
    command = str(os.environ.get(OPENEVIDENCE_MCP_CMD_ENV, "")).strip()
    if not command:
        raise RuntimeError(f"missing {OPENEVIDENCE_MCP_CMD_ENV}")
    parts = shlex.split(command)
    completed = subprocess.run(
        [*parts, "--query", query, "--max-results", str(max_results)],
        capture_output=True,
        text=True,
        timeout=max(timeout, int(os.environ.get("WARD_OPENEVIDENCE_MCP_TIMEOUT", "120"))),
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(f"mcp command failed: exit={completed.returncode} {stderr}")
    stdout = (completed.stdout or "").strip()
    payload = json.loads(stdout) if stdout else {}
    raw_results = payload.get("results") if isinstance(payload, dict) else []
    if not isinstance(raw_results, list):
        return []
    results: list[dict[str, str]] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not title or not url:
            continue
        results.append({"title": title, "url": url, "summary": summary})
        if len(results) >= max_results:
            break
    return results


def _pubmed_url(params: dict[str, object], *, endpoint: str) -> str:
    base = PUBMED_SEARCH_URL if endpoint == "search" else PUBMED_FETCH_URL
    return f"{base}?{urllib.parse.urlencode(params, doseq=True)}"


def _pubmed_search(query: str, *, retmax: int, timeout: int = 20) -> list[str]:
    current_year = datetime.now().year
    min_year = max(1900, current_year - PUBMED_RECENT_YEARS + 1)
    params = {
        "db": "pubmed",
        "term": query,
        "retmode": "json",
        "retmax": retmax,
        "sort": "pub date",
        "datetype": "pdat",
        "mindate": str(min_year),
        "maxdate": str(current_year),
    }
    payload = json.loads(_http_get_text(_pubmed_url(params, endpoint="search"), timeout=timeout))
    ids = payload.get("esearchresult", {}).get("idlist") or []
    return [str(item) for item in ids]


def _xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())


def _pubmed_fetch(pmids: list[str], *, timeout: int = 20) -> list[dict[str, Any]]:
    if not pmids:
        return []
    params = {
        "db": "pubmed",
        "id": ",".join(pmids),
        "retmode": "xml",
    }
    root = ET.fromstring(_http_get_text(_pubmed_url(params, endpoint="fetch"), timeout=timeout))
    sources: list[dict[str, Any]] = []
    for article in root.findall(".//PubmedArticle"):
        pmid = _xml_text(article.find(".//PMID"))
        title = html.unescape(_xml_text(article.find(".//ArticleTitle")))
        abstract_parts = [_xml_text(node) for node in article.findall(".//Abstract/AbstractText")]
        abstract = " ".join(part for part in abstract_parts if part)
        journal = _xml_text(article.find(".//Journal/Title"))
        year = _xml_text(article.find(".//JournalIssue/PubDate/Year"))
        pub_type_nodes = article.findall(".//PublicationTypeList/PublicationType")
        publication_types = [_xml_text(node) for node in pub_type_nodes if _xml_text(node)]
        sources.append(
            {
                "source_type": "pubmed",
                "pmid": pmid,
                "title": title,
                "journal": journal,
                "publication_year": year,
                "publication_types": publication_types,
                "abstract": abstract,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else "",
            }
        )
    return sources


def _pubmed_id_from_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", text)
    if m:
        return m.group(1)
    parsed = urllib.parse.urlparse(text)
    qs = urllib.parse.parse_qs(parsed.query)
    pmid_values = qs.get("pmid") or []
    if pmid_values:
        candidate = str(pmid_values[0]).strip()
        if candidate.isdigit():
            return candidate
    return ""


def _source_family(source: dict[str, Any]) -> str:
    source_type = str(source.get("source_type") or "").strip().lower()
    if source_type.startswith("openevidence"):
        return "openevidence"
    if source_type.startswith("pubmed"):
        return "pubmed"
    return "other"


def _within_recent_window(source: dict[str, Any], *, current_year: int) -> bool:
    year = str(source.get("publication_year") or "").strip()
    if not year.isdigit():
        return True
    min_year = max(1900, current_year - PUBMED_RECENT_YEARS + 1)
    return int(year) >= min_year


def _keyword_tokens(text: str) -> set[str]:
    text_value = str(text or "")
    raw_ascii = re.findall(r"[A-Za-z0-9_]+", text_value.casefold())
    raw_cjk = re.findall(r"[\u4e00-\u9fff]{2,}", text_value)
    raw = raw_ascii + raw_cjk
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "are",
        "was",
        "were",
        "guideline",
        "guidelines",
        "management",
        "acute",
        "review",
        "consensus",
        "recommendation",
    }
    tokens: set[str] = set()
    for tok in raw:
        if re.search(r"[\u4e00-\u9fff]", tok):
            tokens.add(tok)
            continue
        if len(tok) >= 3 and tok not in stop:
            tokens.add(tok)
    return tokens


def _source_relevance_overlap(source: dict[str, Any], *, focus_tokens: set[str]) -> int:
    if not focus_tokens:
        return 1
    source_text = " ".join(
        [
            str(source.get("title") or ""),
            str(source.get("abstract") or ""),
            str(source.get("journal") or ""),
            " ".join(str(x) for x in (source.get("publication_types") or [])),
        ]
    )
    source_tokens = _keyword_tokens(source_text)
    return len(source_tokens.intersection(focus_tokens))


def _source_rank(source: dict[str, Any]) -> int:
    text = f"{source.get('title', '')} {' '.join(source.get('publication_types') or [])}".casefold()
    score = 0
    for marker in ("guideline", "practice guideline", "recommendation", "consensus", "position statement"):
        if marker in text:
            score += 5
    for marker in ("review", "systematic review", "meta-analysis"):
        if marker in text:
            score += 2
    year = str(source.get("publication_year") or "")
    if year.isdigit():
        score += max(0, int(year) - 2018)
    return score


def _ebm_level(source: dict[str, Any]) -> str:
    text = f"{source.get('title', '')} {' '.join(source.get('publication_types') or [])}".casefold()
    if any(marker in text for marker in ("practice guideline", "guideline", "consensus", "position statement", "recommendation")):
        return "guideline"
    if any(marker in text for marker in ("meta-analysis", "systematic review")):
        return "meta_analysis"
    if any(marker in text for marker in ("randomized controlled trial", "randomised controlled trial", "rct")):
        return "rct"
    return "other"


def _pubmed_ebm_query(query: str, level: str) -> str:
    base = f"({query})"
    if level == "guideline":
        return (
            f"{base} AND ((\"Practice Guideline\"[Publication Type]) OR guideline[Title] OR consensus[Title] "
            "OR recommendation[Title])"
        )
    if level == "meta_analysis":
        return (
            f"{base} AND ((\"Meta-Analysis\"[Publication Type]) OR \"systematic review\"[Title/Abstract] "
            "OR \"meta-analysis\"[Title/Abstract])"
        )
    if level == "rct":
        return (
            f"{base} AND ((\"Randomized Controlled Trial\"[Publication Type]) OR random*[Title/Abstract])"
        )
    return base


def retrieve_literature_sources(plan: dict[str, Any], *, max_queries: int = 4, results_per_query: int = 3, timeout: int = 20) -> dict[str, Any]:
    queries = [str(item).strip() for item in plan.get("search_targets") or [] if str(item).strip()]
    queries = list(dict.fromkeys(queries))[:max_queries]
    retrieved_at = datetime.now().isoformat(timespec="seconds")
    query_results: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    provider = _openevidence_provider()
    provider_used = "unknown"
    provider_fallbacks: list[str] = []
    narrative_payload: dict[str, Any] | None = None
    for query in queries:
        query_with_focus = f"{query} guideline recommendations consensus review"
        try:
            found: list[dict[str, str]] = []
            if provider in {"mcp", "auto"}:
                try:
                    found = _openevidence_mcp_search(query_with_focus, max_results=results_per_query, timeout=max(timeout, 30))
                    provider_used = "mcp"
                except Exception as exc:
                    if provider == "mcp":
                        raise
                    provider_fallbacks.append(f"mcp_failed:{exc}")
            if not found:
                found = _openevidence_run_search(query_with_focus, max_results=results_per_query, timeout=max(timeout, 30))
                if provider_used == "unknown":
                    provider_used = "browser"
        except Exception as exc:
            errors.append({"query": query, "error": str(exc)})
            query_results.append(
                {
                    "query": query,
                    "openevidence_query": query_with_focus,
                    "source_count": 0,
                    "error": str(exc),
                }
            )
            continue
        for item in found:
            source = {
                "source_type": f"openevidence_{provider_used if provider_used != 'unknown' else provider}",
                "title": str(item.get("title") or "").strip() or f"OpenEvidence result for {query}",
                "query": query,
                "query_with_focus": query_with_focus,
                "url": str(item.get("url") or "").strip(),
                "journal": "",
                "publication_year": "",
                "publication_types": ["search_result"],
                "abstract": str(item.get("summary") or "").strip(),
            }
            if source["url"]:
                sources.append(source)
        query_results.append(
            {
                "query": query,
                "openevidence_query": query_with_focus,
                "source_count": len(found),
            }
        )
        if narrative_payload is None:
            narrative_payload = _openevidence_run_narrative(query, timeout=max(timeout, 45))

    # Enrich OpenEvidence links that point to PubMed with year/publication-type metadata
    # so a consistent recent-year filter can be applied.
    openevidence_pmids: list[str] = []
    for source in sources:
        if _source_family(source) != "openevidence":
            continue
        pmid = _pubmed_id_from_url(str(source.get("url") or ""))
        if pmid:
            source["pmid"] = pmid
            openevidence_pmids.append(pmid)
    openevidence_meta_by_pmid: dict[str, dict[str, Any]] = {}
    if openevidence_pmids:
        unique_pmids = list(dict.fromkeys(openevidence_pmids))
        try:
            for row in _pubmed_fetch(unique_pmids, timeout=timeout):
                pmid = str(row.get("pmid") or "").strip()
                if pmid:
                    openevidence_meta_by_pmid[pmid] = row
        except Exception as exc:
            errors.append({"query": "openevidence_pubmed_metadata", "error": str(exc)})
    if openevidence_meta_by_pmid:
        for source in sources:
            if _source_family(source) != "openevidence":
                continue
            pmid = str(source.get("pmid") or "").strip()
            meta = openevidence_meta_by_pmid.get(pmid)
            if not meta:
                continue
            source["publication_year"] = str(meta.get("publication_year") or source.get("publication_year") or "")
            source["publication_types"] = list(meta.get("publication_types") or source.get("publication_types") or [])

    # PubMed EBM retrieval: guideline > meta-analysis > RCT
    ebm_order = ("guideline", "meta_analysis", "rct")
    for query in queries:
        pubmed_level_count = 0
        for level in ebm_order:
            try:
                term = _pubmed_ebm_query(query, level)
                ids = _pubmed_search(term, retmax=max(1, results_per_query), timeout=timeout)
                fetched = _pubmed_fetch(ids, timeout=timeout)
            except Exception as exc:
                errors.append({"query": query, "error": f"pubmed_{level}: {exc}"})
                continue
            for item in fetched:
                source = {
                    **item,
                    "query": query,
                    "query_with_focus": term,
                    "ebm_level": level,
                }
                if source.get("url"):
                    sources.append(source)
                    pubmed_level_count += 1
        query_results.append(
            {
                "query": query,
                "pubmed_ebm_levels": list(ebm_order),
                "source_count": pubmed_level_count,
            }
        )

    # Apply recent-year filter to any source with resolvable publication year.
    current_year = datetime.now().year
    filtered_sources: list[dict[str, Any]] = []
    for source in sources:
        if _within_recent_window(source, current_year=current_year):
            filtered_sources.append(source)
    sources = filtered_sources

    # Relevance gate: keep only items with at least one overlap token
    # against query + diagnosis topic focus terms.
    classification = plan.get("clinical_classification") if isinstance(plan.get("clinical_classification"), dict) else {}
    topic_tokens = _keyword_tokens(" ".join(str(x) for x in (classification.get("diagnosis_topics") or [])))
    service_tokens = _keyword_tokens(
        " ".join(
            [
                str(classification.get("primary_service") or ""),
                str(classification.get("primary_specialty") or ""),
            ]
        )
    )
    for candidate in classification.get("service_candidates") or []:
        if not isinstance(candidate, dict):
            continue
        service_tokens.update(_keyword_tokens(" ".join(str(x) for x in (candidate.get("matched_terms") or []))))
    relevance_filtered_sources: list[dict[str, Any]] = []
    relevance_filtered_out = 0
    max_overlap = 0
    for source in sources:
        query_tokens = _keyword_tokens(str(source.get("query") or ""))
        focus_tokens = topic_tokens.union(service_tokens).union(query_tokens)
        overlap = _source_relevance_overlap(source, focus_tokens=focus_tokens)
        source["relevance_overlap"] = overlap
        max_overlap = max(max_overlap, overlap)
        if overlap >= 1:
            relevance_filtered_sources.append(source)
        else:
            relevance_filtered_out += 1
    relevance_gate_bypassed = False
    if max_overlap >= 1:
        sources = relevance_filtered_sources
    else:
        # Common when query is mixed-language and source metadata is mostly English.
        # Keep original items instead of forcing an empty retrieval.
        relevance_filtered_out = 0
        relevance_gate_bypassed = True

    # Deduplicate by (URL + source family) so PubMed does not get hidden
    # by an OpenEvidence link pointing to the same URL.
    dedup: dict[str, dict[str, Any]] = {}
    for source in sources:
        url = str(source.get("url") or "").strip()
        if not url:
            continue
        dedupe_key = f"{_source_family(source)}::{url}"
        existing = dedup.get(dedupe_key)
        if existing is None:
            dedup[dedupe_key] = source
            continue
        if _source_rank(source) > _source_rank(existing):
            dedup[dedupe_key] = source
    level_weight = {"guideline": 3, "meta_analysis": 2, "rct": 1, "other": 0}
    family_weight = {"pubmed": 2, "openevidence": 1, "other": 0}
    ranked = sorted(
        dedup.values(),
        key=lambda item: (
            -level_weight.get(str(item.get("ebm_level") or _ebm_level(item)), 0),
            -_source_rank(item),
            -family_weight.get(_source_family(item), 0),
        ),
    )
    sources = ranked[: max(1, max_queries * max(1, results_per_query))]
    return {
        "ok": bool(sources),
        "action": "literature-retrieve",
        "retrieved_at": retrieved_at,
        "source": "OpenEvidence + PubMed EBM Search",
        "provider": provider,
        "provider_used": provider_used if provider_used != "unknown" else provider,
        "provider_fallbacks": provider_fallbacks,
        "clinical_classification": plan.get("clinical_classification") or {},
        "queries": query_results,
        "sources": sources,
        "source_count": len(sources),
        "errors": errors,
        "openevidence_narrative": narrative_payload or {},
        "notes": [
            "Retrieved sources are for clinician reference and do not modify the SOAP draft.",
            "EBM priority order: guideline > meta-analysis > randomized controlled trial.",
            f"Topical relevance gate removed {relevance_filtered_out} low-overlap item(s).",
            f"Topical relevance gate bypassed: {'yes' if relevance_gate_bypassed else 'no'}.",
            "Set WARD_OPENEVIDENCE_PROVIDER=auto|mcp|browser (default auto).",
            "For MCP mode, set WARD_OPENEVIDENCE_MCP_COMMAND to a command that returns JSON: {\"results\":[{\"title\":\"...\",\"url\":\"...\"}]}.",
            "Browser mode requires a valid logged-in session captured by `ward openevidence-login`.",
        ],
    }


def _compact_abstract_summary(abstract: str) -> str:
    abstract = " ".join(str(abstract or "").split())
    if not abstract:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", abstract)
    selected = " ".join(sentences[:2]).strip()
    if len(selected) > 420:
        selected = selected[:417].rstrip() + "..."
    return selected


def summarize_literature_sources(plan: dict[str, Any], sources_payload: dict[str, Any], *, max_sources: int = 5) -> dict[str, Any]:
    classification = plan.get("clinical_classification") or sources_payload.get("clinical_classification") or {}
    topics = classification.get("diagnosis_topics") or []
    service = classification.get("primary_specialty") or classification.get("primary_service") or ""
    sources = list(sources_payload.get("sources") or [])[:max_sources]
    confidence = str(classification.get("confidence") or "").strip().lower()
    evidence_items: list[dict[str, Any]] = []
    for source in sources:
        source_type = str(source.get("source_type") or "").lower()
        source_name = "OpenEvidence" if source_type.startswith("openevidence") else "PubMed"
        ebm_level = str(source.get("ebm_level") or _ebm_level(source))
        evidence_items.append(
            {
                "title": source.get("title") or "",
                "source": source_name,
                "evidence_level": ebm_level,
                "pmid": source.get("pmid") or "",
                "url": source.get("url") or "",
                "journal": source.get("journal") or "",
                "year": source.get("publication_year") or "",
                "publication_types": source.get("publication_types") or [],
                "relevance": (
                    f"Matches query target: {source.get('query')}"
                    if source.get("query")
                    else "Retrieved from the selected literature query plan."
                ),
                "summary": (
                    _compact_abstract_summary(str(source.get("abstract") or ""))
                    or "OpenEvidence query link generated; open the link to review ranked literature."
                ),
            }
        )

    key_points = [
        "Prioritize society guidelines, consensus statements, and high-quality reviews from the retrieved list.",
        "Treat this as reference support for clinician review, not as an automatic treatment recommendation.",
        "Check publication year, population, and setting before applying to the patient.",
    ]
    if confidence in {"low", "moderate"}:
        key_points.insert(0, "Low-confidence classification: treat retrieved links as broad directional evidence and verify clinical fit manually.")

    return {
        "ok": True,
        "action": "literature-summarize",
        "summary_generated_at": datetime.now().isoformat(timespec="seconds"),
        "clinical_context": {
            "primary_specialty": service,
            "primary_service": service,
            "diagnosis_topics": topics,
            "classification_confidence": classification.get("confidence") or "",
        },
        "overview": (
            f"Evidence search focused on {', '.join(topics) if topics else 'the inferred clinical topic'}"
            + (f" under {service}." if service else ".")
        ),
        "key_points": key_points,
        "evidence_items": evidence_items,
        "source_count": len(evidence_items),
        "limitations": [
            "OpenEvidence retrieval in this pipeline is query-link based and depends on interactive platform results.",
            "Returned links do not include full-text extraction in this offline pipeline step.",
            "Clinical decisions still require clinician review and local guideline alignment.",
        ],
    }
