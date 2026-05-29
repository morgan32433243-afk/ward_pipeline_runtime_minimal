from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codex_runtime import run_codex_exec
from .config import WardConfig

LITERATURE_QUESTION_PLAN_FILE = "literature_question_plan.json"
LITERATURE_QUESTION_PLAN_FAILED_FILE = "literature_question_plan.failed.txt"
PROMPT_PATH = Path(__file__).parent / "prompts" / "literature_question_planner.md"


class LiteraturePlannerError(Exception):
    pass


BAD_EXACT_QUERIES = {
    "fluid guideline",
    "fluid acute management guideline",
    "adult inpatient guideline",
    "inpatient acute care guideline",
    "hospital medicine diagnostic and treatment guideline",
    "evidence-based acute management recommendations",
    "general surgery guideline",
    "general surgery adult inpatient guideline",
    "neurosurgery guideline",
    "neurosurgery adult inpatient guideline",
}

BAD_QUERY_PATTERNS = (
    " adult inpatient guideline",
    " acute management guideline",
)


def build_literature_question_prompt(
    *,
    job_id: str,
    source_text: str,
    source_type: str,
) -> str:
    template = PROMPT_PATH.read_text(encoding="utf-8")
    return f"""{template}

Job ID: {job_id}
Source type: {source_type}

Clinical source:
```text
{source_text[:50000]}
```
"""


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = str(text or "").strip()
    if not stripped:
        raise LiteraturePlannerError("LLM literature planner returned empty output")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise LiteraturePlannerError("LLM literature planner output did not contain a JSON object")
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LiteraturePlannerError(f"LLM literature planner output was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LiteraturePlannerError("LLM literature planner JSON must be an object")
    return payload


def _repair_json_prompt(*, invalid_output: str, error: str) -> str:
    return f"""Repair the following JSON output.

Return exactly one valid JSON object. Do not include markdown or explanations.
Do not add, remove, or reinterpret clinical content. Only fix JSON syntax.

JSON parse error:
{error}

Invalid output:
```text
{invalid_output[:30000]}
```
"""


def _as_non_empty_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise LiteraturePlannerError(f"LLM literature planner missing required field: {field}")
    return text


def _as_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise LiteraturePlannerError(f"LLM literature planner field must be a list: {field}")
    return value


def _validate_search_query(query: str) -> None:
    normalized = " ".join(str(query or "").casefold().split())
    if not normalized:
        raise LiteraturePlannerError("LLM literature planner produced an empty search query")
    if normalized in BAD_EXACT_QUERIES:
        raise LiteraturePlannerError(f"LLM literature planner produced a generic search query: {query}")
    if len(normalized.split()) < 4:
        raise LiteraturePlannerError(f"LLM literature planner search query is too broad: {query}")
    if any(normalized.endswith(pattern) and len(normalized.split()) <= 4 for pattern in BAD_QUERY_PATTERNS):
        raise LiteraturePlannerError(f"LLM literature planner search query lacks a decision point: {query}")


def validate_literature_question_plan(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise LiteraturePlannerError("LLM literature planner payload must be an object")

    _as_non_empty_string(payload.get("primary_clinical_domain"), "primary_clinical_domain")
    _as_non_empty_string(payload.get("patient_problem_representation"), "patient_problem_representation")

    active_problems = _as_list(payload.get("active_problems"), "active_problems")
    if not active_problems:
        raise LiteraturePlannerError("LLM literature planner requires at least one active problem")
    for index, problem in enumerate(active_problems, start=1):
        if not isinstance(problem, dict):
            raise LiteraturePlannerError(f"active_problems[{index}] must be an object")
        _as_non_empty_string(problem.get("problem"), f"active_problems[{index}].problem")
        certainty = _as_non_empty_string(problem.get("certainty"), f"active_problems[{index}].certainty")
        if certainty not in {"confirmed", "suspected", "possible", "unclear"}:
            raise LiteraturePlannerError(f"active_problems[{index}].certainty has invalid value: {certainty}")
        priority = _as_non_empty_string(problem.get("search_priority"), f"active_problems[{index}].search_priority")
        if priority not in {"high", "medium", "low"}:
            raise LiteraturePlannerError(f"active_problems[{index}].search_priority has invalid value: {priority}")

    questions = _as_list(payload.get("clinical_questions"), "clinical_questions")
    if not questions:
        raise LiteraturePlannerError("LLM literature planner requires at least one clinical question")
    all_queries: list[str] = []
    for index, question in enumerate(questions, start=1):
        if not isinstance(question, dict):
            raise LiteraturePlannerError(f"clinical_questions[{index}] must be an object")
        _as_non_empty_string(question.get("question"), f"clinical_questions[{index}].question")
        question_type = _as_non_empty_string(question.get("question_type"), f"clinical_questions[{index}].question_type")
        if question_type not in {"diagnosis", "management", "prognosis", "risk", "follow_up", "counseling"}:
            raise LiteraturePlannerError(f"clinical_questions[{index}].question_type has invalid value: {question_type}")
        pico = question.get("pico")
        if not isinstance(pico, dict):
            raise LiteraturePlannerError(f"clinical_questions[{index}].pico must be an object")
        _as_non_empty_string(pico.get("population"), f"clinical_questions[{index}].pico.population")
        outcomes = _as_list(pico.get("outcomes"), f"clinical_questions[{index}].pico.outcomes")
        if not outcomes:
            raise LiteraturePlannerError(f"clinical_questions[{index}].pico.outcomes requires at least one outcome")
        search_queries = _as_list(question.get("search_queries"), f"clinical_questions[{index}].search_queries")
        if not search_queries:
            raise LiteraturePlannerError(f"clinical_questions[{index}].search_queries requires at least one query")
        for raw_query in search_queries:
            query = _as_non_empty_string(raw_query, f"clinical_questions[{index}].search_queries[]")
            _validate_search_query(query)
            all_queries.append(query)

    routing = payload.get("routing")
    if not isinstance(routing, dict):
        raise LiteraturePlannerError("LLM literature planner routing must be an object")
    _as_non_empty_string(routing.get("clinical_domain_label"), "routing.clinical_domain_label")
    taxonomy_confidence = _as_non_empty_string(routing.get("taxonomy_confidence"), "routing.taxonomy_confidence")
    if taxonomy_confidence not in {"supportive_only", "fallback_only", "none"}:
        raise LiteraturePlannerError(f"routing.taxonomy_confidence has invalid value: {taxonomy_confidence}")

    do_not_search = payload.get("do_not_search")
    if do_not_search is not None:
        _as_list(do_not_search, "do_not_search")
    needs_human_review = payload.get("needs_human_review")
    if needs_human_review is not None:
        _as_list(needs_human_review, "needs_human_review")

    payload["search_targets"] = list(dict.fromkeys(all_queries))
    payload["planner_validation"] = {"ok": True, "query_count": len(payload["search_targets"])}
    return payload


def plan_literature_questions_with_llm(
    config: WardConfig,
    job_id: str,
    *,
    job_dir: Path,
    source_text: str,
    source_type: str,
    model: str | None = None,
    timeout: int = 900,
    repair_attempts: int = 1,
) -> dict[str, Any]:
    prompt = build_literature_question_prompt(job_id=job_id, source_text=source_text, source_type=source_type)
    completed, output = run_codex_exec(
        prompt,
        config=config,
        cwd=job_dir,
        output_dir=job_dir,
        model=model,
        timeout=timeout,
    )
    diagnostic = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0:
        raise LiteraturePlannerError(diagnostic or f"Codex exec exited with code {completed.returncode}")
    parse_errors: list[str] = []
    for attempt in range(repair_attempts + 1):
        try:
            payload = _extract_json_object(output)
            break
        except LiteraturePlannerError as exc:
            parse_errors.append(str(exc))
            if attempt >= repair_attempts:
                failed_path = job_dir / LITERATURE_QUESTION_PLAN_FAILED_FILE
                failed_path.write_text(
                    "LLM literature planner JSON parse failed.\n\n"
                    f"Errors:\n{json.dumps(parse_errors, ensure_ascii=False, indent=2)}\n\n"
                    f"Last output:\n{output}\n",
                    encoding="utf-8",
                )
                raise
            repair_completed, repaired_output = run_codex_exec(
                _repair_json_prompt(invalid_output=output, error=str(exc)),
                config=config,
                cwd=job_dir,
                output_dir=job_dir,
                model=model,
                timeout=timeout,
            )
            repair_diagnostic = (repair_completed.stdout or repair_completed.stderr).strip()
            if repair_completed.returncode != 0:
                raise LiteraturePlannerError(repair_diagnostic or f"Codex JSON repair exited with code {repair_completed.returncode}")
            output = repaired_output
    payload = validate_literature_question_plan(payload)
    payload.setdefault("ok", True)
    payload.setdefault("action", "literature-question-plan")
    payload.setdefault("job_id", job_id)
    payload.setdefault("source_type", source_type)
    payload.setdefault("planner_source", "llm")
    return payload
