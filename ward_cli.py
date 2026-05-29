#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ward_pipeline.config import load_config
from ward_pipeline.case_view import build_case_view
from ward_pipeline.encounter import route_audio_file
from ward_pipeline.jobs import (
    WardError,
    accept_latest_review,
    attach_transcript,
    auto_confirm_stt_recovery_candidates,
    auto_soap,
    config_summary,
    confirm_terms,
    deliver,
    draft_soap,
    extract_facts,
    export_prompt,
    export_obsidian,
    health,
    import_result,
    ingest,
    literature_enrich,
    inspect,
    literature_plan,
    list_jobs,
    normalize_llm,
    openevidence_login,
    process_audio,
    resolve_report,
    resend,
    retention_dry_run,
    run,
    status,
    transcribe,
    validate_soap,
)
from ward_pipeline.stt_review import stt_promote_approved, stt_review, stt_review_queue
from ward_pipeline.watcher import scan_incoming


def _print_json(payload: dict, code: int = 0) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ward", description="Minimal local ward workflow CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config", help="Show resolved local configuration")

    ingest_parser = subparsers.add_parser("ingest", help="Create a local job for an audio file")
    ingest_parser.add_argument("audio_path")

    status_parser = subparsers.add_parser("status", help="Show a concise job status")
    status_parser.add_argument("job_id")

    list_parser = subparsers.add_parser("list", help="List jobs")
    list_parser.add_argument("--today", action="store_true", help="Only list jobs created today")

    inspect_parser = subparsers.add_parser("inspect", help="Show full job state and artifacts")
    inspect_parser.add_argument("job_id")

    case_view_parser = subparsers.add_parser("case-view", help="Create/update the human-facing audio/transcript case view")
    case_view_parser.add_argument("job_id")

    transcript_parser = subparsers.add_parser("attach-transcript", help="Attach a manual transcript to a job")
    transcript_parser.add_argument("job_id")
    transcript_parser.add_argument("transcript_path")

    transcribe_parser = subparsers.add_parser("transcribe", help="Transcribe a job's audio through Hermes STT")
    transcribe_parser.add_argument("job_id")
    transcribe_parser.add_argument("--stt-model", help="STT model override")

    stt_review_parser = subparsers.add_parser("stt-review", help="Collect STT review candidates for later batch cleanup")
    stt_review_parser.add_argument("job_id")

    stt_review_queue_parser = subparsers.add_parser("stt-review-queue", help="Summarize manually editable STT review queues")
    stt_review_queue_parser.add_argument("--limit", type=int, default=10, help="Top items per queue to include")

    stt_promote_parser = subparsers.add_parser("stt-promote-approved", help="Promote approved STT review queue items into production rules")
    stt_promote_parser.add_argument("--dry-run", action="store_true", help="Preview promotions without writing production rules")

    auto_stt_recovery_parser = subparsers.add_parser("auto-confirm-stt-recovery", help="Auto-confirm low-risk STT recovery candidates")
    auto_stt_recovery_parser.add_argument("job_id")

    export_parser = subparsers.add_parser("export-prompt", help="Create a manual ChatGPT prompt package")
    export_parser.add_argument("job_id")
    export_parser.add_argument("--target", default="chatgpt")
    export_parser.add_argument("--deidentify", action="store_true")

    obsidian_parser = subparsers.add_parser("export-obsidian", help="Export a low-noise SOAP draft note into Obsidian")
    obsidian_parser.add_argument("job_id")
    obsidian_parser.add_argument("--vault-dir", help="Override Obsidian vault root")

    confirm_parser = subparsers.add_parser("confirm-terms", help="Confirm uncertain medical terms for SOAP prompt generation")
    confirm_parser.add_argument("job_id")
    confirm_parser.add_argument("--all-uncertain", action="store_true", help="Confirm every term in uncertain_terms.json using its candidate")
    confirm_parser.add_argument("--original", help="Original uncertain transcript term")
    confirm_parser.add_argument("--corrected", help="Clinician-confirmed corrected term")

    accept_parser = subparsers.add_parser("accept-latest", help="Confirm all uncertain terms on the latest job and regenerate the mobile SOAP draft")
    accept_parser.add_argument("--job-id", default="latest", help="Job id to accept; defaults to latest")
    accept_parser.add_argument("--no-redraft", action="store_true", help="Only confirm terms; do not regenerate the SOAP draft")
    accept_parser.add_argument("--local-only", action="store_true", help="Do not allow external LLM redraft")
    accept_parser.add_argument("--model", help="Hermes model override")
    accept_parser.add_argument("--provider", help="Hermes provider override")
    accept_parser.add_argument("--deidentify", action="store_true", help="Request de-identification in the regenerated draft")
    accept_parser.add_argument("--deliver-target", help="Override delivery target: discord:CHAT_ID[:THREAD_ID]")
    accept_parser.add_argument("--no-reuse-delivery-target", action="store_true", help="Do not reuse the previous Discord delivery target")
    accept_parser.add_argument("--no-export-obsidian", action="store_true", help="Do not export the regenerated SOAP draft to Obsidian")
    accept_parser.add_argument("--obsidian-vault-dir", help="Override Obsidian vault root for export")
    accept_parser.add_argument("--include-stt-recovery", action="store_true", help="Confirm every STT recovery candidate before redrafting")
    accept_parser.add_argument("--stt-recovery", action="append", default=[], help="Confirm one STT recovery candidate id, e.g. rec-001. Can be repeated")

    import_parser = subparsers.add_parser("import-result", help="Import a manually saved result")
    import_parser.add_argument("job_id")
    import_parser.add_argument("result_path")

    literature_parser = subparsers.add_parser("literature-plan", help="Plan guideline/evidence search targets from transcript")
    literature_parser.add_argument("job_id")

    literature_enrich_parser = subparsers.add_parser("literature-enrich", help="Retrieve OpenEvidence search results and summarize them for a job")
    literature_enrich_parser.add_argument("job_id")
    literature_enrich_parser.add_argument("--max-queries", type=int, default=4, help="Maximum query targets to retrieve")
    literature_enrich_parser.add_argument("--results-per-query", type=int, default=3, help="Maximum extracted OpenEvidence results per query")
    literature_enrich_parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds")
    openevidence_login_parser = subparsers.add_parser("openevidence-login", help="Open browser for OpenEvidence login and save local session")
    openevidence_login_parser.add_argument("--timeout", type=int, default=600, help="Login timeout in seconds")

    llm_normalize_parser = subparsers.add_parser("normalize-llm", help="Run the LLM transcript normalization stage")
    llm_normalize_parser.add_argument("job_id")
    llm_normalize_parser.add_argument("--allow-external-llm", action="store_true", help="Allow external LLM normalization")
    llm_normalize_parser.add_argument("--model", help="LLM model name for audit metadata")
    llm_normalize_parser.add_argument("--provider", help="LLM provider name for audit metadata")

    facts_parser = subparsers.add_parser("extract-facts", help="Extract evidence-grounded clinical facts")
    facts_parser.add_argument("job_id")

    soap_parser = subparsers.add_parser("draft-soap", help="Draft an evidence-grounded SOAP artifact")
    soap_parser.add_argument("job_id")

    validate_soap_parser = subparsers.add_parser("validate-soap", help="Validate SOAP artifact for auto-finalization")
    validate_soap_parser.add_argument("job_id")

    auto_soap_parser = subparsers.add_parser("auto-soap", help="Run normalize, fact extraction, SOAP draft, and validation")
    auto_soap_parser.add_argument("job_id")
    auto_soap_parser.add_argument("--allow-external-llm", action="store_true", help="Allow external LLM normalization")
    auto_soap_parser.add_argument("--model", help="LLM model name for audit metadata")
    auto_soap_parser.add_argument("--provider", help="LLM provider name for audit metadata")
    auto_soap_parser.add_argument("--deliver-target", help="Deliver SOAP draft to discord:CHAT_ID[:THREAD_ID]")
    auto_soap_parser.add_argument("--export-obsidian", action="store_true", help="Export the generated SOAP draft into Obsidian")
    auto_soap_parser.add_argument("--obsidian-vault-dir", help="Override Obsidian vault root for export")
    auto_soap_parser.add_argument("--no-validate", action="store_true", help="Skip SOAP validation in the immediate output path")

    run_parser = subparsers.add_parser("run", help="Generate a clinician-editable SOAP draft from a job transcript")
    run_parser.add_argument("job_id")
    run_parser.add_argument("--transcript", dest="transcript_path", help="Attach this transcript before running")
    run_parser.add_argument("--allow-external-llm", action="store_true", help="Allow Hermes/Codex to generate the SOAP draft")
    run_parser.add_argument("--model", help="Hermes model override")
    run_parser.add_argument("--provider", help="Hermes provider override")
    run_parser.add_argument("--deidentify", action="store_true", help="Request de-identification in the generated prompt")
    run_parser.add_argument("--deliver-target", help="Deliver SOAP draft to discord:CHAT_ID[:THREAD_ID]")
    run_parser.add_argument("--export-obsidian", action="store_true", help="Export the generated SOAP draft into Obsidian")
    run_parser.add_argument("--obsidian-vault-dir", help="Override Obsidian vault root for export")

    draft_parser = subparsers.add_parser("draft", help="Alias for run: generate and optionally deliver a SOAP draft")
    draft_parser.add_argument("job_id")
    draft_parser.add_argument("--transcript", dest="transcript_path", help="Attach this transcript before drafting")
    draft_parser.add_argument("--allow-external-llm", action="store_true", help="Allow Hermes/Codex to generate the SOAP draft")
    draft_parser.add_argument("--model", help="Hermes model override")
    draft_parser.add_argument("--provider", help="Hermes provider override")
    draft_parser.add_argument("--deidentify", action="store_true", help="Request de-identification in the generated prompt")
    draft_parser.add_argument("--deliver-target", help="Deliver SOAP draft to discord:CHAT_ID[:THREAD_ID]")
    draft_parser.add_argument("--export-obsidian", action="store_true", help="Export the generated SOAP draft into Obsidian")
    draft_parser.add_argument("--obsidian-vault-dir", help="Override Obsidian vault root for export")

    process_parser = subparsers.add_parser("process", help="Ingest, transcribe, generate SOAP draft, and optionally deliver it")
    process_parser.add_argument("audio_path")
    process_parser.add_argument("--allow-external-llm", action="store_true", help="Allow Hermes/Codex to generate the SOAP draft")
    process_parser.add_argument("--stt-model", help="STT model override")
    process_parser.add_argument("--model", help="Hermes model override")
    process_parser.add_argument("--provider", help="Hermes provider override")
    process_parser.add_argument("--deidentify", action="store_true", help="Request de-identification in the generated prompt")
    process_parser.add_argument("--deliver-target", help="Deliver SOAP draft to discord:CHAT_ID[:THREAD_ID]")
    process_parser.add_argument("--export-obsidian", action="store_true", help="Export the generated SOAP draft into Obsidian")
    process_parser.add_argument("--obsidian-vault-dir", help="Override Obsidian vault root for export")

    deliver_parser = subparsers.add_parser("deliver", help="Deliver SOAP/reports to Discord with retry")
    deliver_parser.add_argument("job_id")
    deliver_parser.add_argument("--target", required=True, help="Delivery target: discord:CHAT_ID[:THREAD_ID]")

    resend_parser = subparsers.add_parser("resend", help="Resend a previously failed delivery")
    resend_parser.add_argument("job_id")
    resend_parser.add_argument("--target", help="Override delivery target: discord:CHAT_ID[:THREAD_ID]")

    resolve_parser = subparsers.add_parser("resolve-report", help="Mark a failed delivery as intentionally resolved")
    resolve_parser.add_argument("job_id")
    resolve_parser.add_argument("--reason", required=True, help="Resolution reason to record in the job artifacts")

    health_parser = subparsers.add_parser("health", help="Run ward workflow health checks and report to Discord")
    health_parser.add_argument("--target", help="Delivery target: discord:CHAT_ID[:THREAD_ID]")

    retention_parser = subparsers.add_parser("retention-dry-run", help="Report retention cleanup candidates without deleting anything")
    retention_parser.add_argument("--age-days", type=int, default=14, help="Only include candidates older than this many days")
    retention_parser.add_argument("--limit", type=int, default=200, help="Maximum number of candidate paths to include in output")
    retention_parser.add_argument("--write-artifact", action="store_true", help="Persist the full dry-run review JSON under data/output/_retention")
    retention_parser.add_argument("--artifact-path", help="Override the dry-run review JSON output path")

    route_parser = subparsers.add_parser("route-audio", help="Archive an audio file into date/bed/encounter folders")
    route_parser.add_argument("audio_path")
    route_parser.add_argument("--bed-id", help="Optional bed id override")
    route_parser.add_argument("--copy", action="store_true", help="Copy instead of moving the source audio")

    watch_parser = subparsers.add_parser("watch-incoming", help="Watch incoming audio and pass stable files into ward workflow")
    watch_parser.add_argument("--incoming-dir", help="Incoming audio folder")
    watch_parser.add_argument("--once", action="store_true", help="Scan once and exit")
    watch_parser.add_argument("--stable-seconds", type=int, default=8)
    watch_parser.add_argument("--poll-seconds", type=int, default=5)
    watch_parser.add_argument("--allow-external-llm", action="store_true")
    watch_parser.add_argument("--deidentify", action="store_true", help="Request de-identification in generated clinical prompts")
    watch_parser.add_argument("--model", help="Hermes model override")
    watch_parser.add_argument("--provider", help="Hermes provider override")
    watch_parser.add_argument("--deliver-target", help="Deliver generated reports to discord:CHAT_ID[:THREAD_ID]")
    watch_parser.add_argument("--export-obsidian", action="store_true", help="Export each generated SOAP draft into Obsidian")
    watch_parser.add_argument("--obsidian-vault-dir", help="Override Obsidian vault root for export")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()

    try:
        if args.command == "config":
            return _print_json(config_summary(config))
        if args.command == "ingest":
            return _print_json(ingest(config, Path(args.audio_path)))
        if args.command == "status":
            return _print_json(status(config, args.job_id))
        if args.command == "list":
            return _print_json(list_jobs(config, today=args.today))
        if args.command == "inspect":
            return _print_json(inspect(config, args.job_id))
        if args.command == "case-view":
            return _print_json(build_case_view(config, args.job_id))
        if args.command == "attach-transcript":
            return _print_json(attach_transcript(config, args.job_id, Path(args.transcript_path)))
        if args.command == "transcribe":
            return _print_json(transcribe(config, args.job_id, model=args.stt_model))
        if args.command == "stt-review":
            return _print_json(stt_review(config, args.job_id))
        if args.command == "stt-review-queue":
            return _print_json(stt_review_queue(config, limit=args.limit))
        if args.command == "stt-promote-approved":
            return _print_json(stt_promote_approved(config, dry_run=args.dry_run))
        if args.command == "auto-confirm-stt-recovery":
            return _print_json(auto_confirm_stt_recovery_candidates(config, args.job_id))
        if args.command == "export-prompt":
            return _print_json(export_prompt(config, args.job_id, args.target, args.deidentify))
        if args.command == "export-obsidian":
            return _print_json(
                export_obsidian(
                    config,
                    args.job_id,
                    vault_dir=Path(args.vault_dir) if args.vault_dir else None,
                )
            )
        if args.command == "confirm-terms":
            return _print_json(
                confirm_terms(
                    config,
                    args.job_id,
                    all_uncertain=args.all_uncertain,
                    original=args.original,
                    corrected=args.corrected,
                )
            )
        if args.command == "accept-latest":
            return _print_json(
                accept_latest_review(
                    config,
                    job_id=args.job_id,
                    redraft=not args.no_redraft,
                    allow_external_llm=not args.local_only,
                    model=args.model,
                    provider=args.provider,
                    deidentify=args.deidentify,
                    deliver_target=args.deliver_target,
                    reuse_delivery_target=not args.no_reuse_delivery_target,
                    export_obsidian_note=not args.no_export_obsidian,
                    obsidian_vault_dir=Path(args.obsidian_vault_dir) if args.obsidian_vault_dir else None,
                    include_stt_recovery=args.include_stt_recovery,
                    stt_recovery_candidate_ids=args.stt_recovery,
                )
            )
        if args.command == "import-result":
            return _print_json(import_result(config, args.job_id, Path(args.result_path)))
        if args.command == "literature-plan":
            return _print_json(literature_plan(config, args.job_id))
        if args.command == "literature-enrich":
            return _print_json(
                literature_enrich(
                    config,
                    args.job_id,
                    max_queries=args.max_queries,
                    results_per_query=args.results_per_query,
                    timeout=args.timeout,
                )
            )
        if args.command == "openevidence-login":
            return _print_json(openevidence_login(config, timeout=args.timeout))
        if args.command == "normalize-llm":
            return _print_json(
                normalize_llm(
                    config,
                    args.job_id,
                    allow_external_llm=args.allow_external_llm,
                    model=args.model,
                    provider=args.provider,
                    deliver_target=args.deliver_target,
                    export_obsidian_note=args.export_obsidian,
                    obsidian_vault_dir=Path(args.obsidian_vault_dir) if args.obsidian_vault_dir else None,
                )
            )
        if args.command == "extract-facts":
            return _print_json(extract_facts(config, args.job_id))
        if args.command == "draft-soap":
            return _print_json(draft_soap(config, args.job_id))
        if args.command == "validate-soap":
            return _print_json(validate_soap(config, args.job_id))
        if args.command == "auto-soap":
            return _print_json(
                auto_soap(
                    config,
                    args.job_id,
                    allow_external_llm=args.allow_external_llm,
                    model=args.model,
                    provider=args.provider,
                    deliver_target=args.deliver_target,
                    export_obsidian_note=args.export_obsidian,
                    obsidian_vault_dir=Path(args.obsidian_vault_dir) if args.obsidian_vault_dir else None,
                    validate_after_output=not args.no_validate,
                )
            )
        if args.command == "run":
            transcript_path = Path(args.transcript_path) if args.transcript_path else None
            return _print_json(
                run(
                    config,
                    args.job_id,
                    transcript_path=transcript_path,
                    allow_external_llm=args.allow_external_llm,
                    model=args.model,
                    provider=args.provider,
                    deidentify=args.deidentify,
                    deliver_target=args.deliver_target,
                    export_obsidian_note=args.export_obsidian,
                    obsidian_vault_dir=Path(args.obsidian_vault_dir) if args.obsidian_vault_dir else None,
                )
            )
        if args.command == "draft":
            transcript_path = Path(args.transcript_path) if args.transcript_path else None
            return _print_json(
                run(
                    config,
                    args.job_id,
                    transcript_path=transcript_path,
                    allow_external_llm=args.allow_external_llm,
                    model=args.model,
                    provider=args.provider,
                    deidentify=args.deidentify,
                    deliver_target=args.deliver_target,
                    export_obsidian_note=args.export_obsidian,
                    obsidian_vault_dir=Path(args.obsidian_vault_dir) if args.obsidian_vault_dir else None,
                )
            )
        if args.command == "process":
            return _print_json(
                process_audio(
                    config,
                    Path(args.audio_path),
                    allow_external_llm=args.allow_external_llm,
                    stt_model=args.stt_model,
                    model=args.model,
                    provider=args.provider,
                    deidentify=args.deidentify,
                    deliver_target=args.deliver_target,
                    export_obsidian_note=args.export_obsidian,
                    obsidian_vault_dir=Path(args.obsidian_vault_dir) if args.obsidian_vault_dir else None,
                )
            )
        if args.command == "deliver":
            return _print_json(deliver(config, args.job_id, args.target))
        if args.command == "resend":
            return _print_json(resend(config, args.job_id, target=args.target))
        if args.command == "resolve-report":
            return _print_json(resolve_report(config, args.job_id, args.reason))
        if args.command == "health":
            return _print_json(health(config, target=args.target))
        if args.command == "retention-dry-run":
            return _print_json(
                retention_dry_run(
                    config,
                    age_days=args.age_days,
                    limit=args.limit,
                    write_artifact=args.write_artifact,
                    artifact_path=Path(args.artifact_path) if args.artifact_path else None,
                )
            )
        if args.command == "route-audio":
            routed = route_audio_file(config, Path(args.audio_path), bed_id=args.bed_id, move=not args.copy)
            return _print_json(
                {
                    "ok": True,
                    "action": "route-audio",
                    "archived_path": str(routed.archived_path),
                    "encounter_dir": str(routed.encounter_dir),
                    "encounter_id": routed.encounter_id,
                    "bed_id": routed.bed_id,
                    "same_encounter_confidence": routed.confidence,
                    "requires_identity_review": routed.requires_identity_review,
                    "grouping_basis": routed.grouping_basis,
                    "route_status": routed.route_status,
                }
            )
        if args.command == "watch-incoming":
            return _print_json(
                scan_incoming(
                    config,
                    incoming_dir=Path(args.incoming_dir) if args.incoming_dir else None,
                    once=args.once,
                    stable_seconds=args.stable_seconds,
                    poll_seconds=args.poll_seconds,
                    allow_external_llm=args.allow_external_llm,
                    deidentify=args.deidentify,
                    model=args.model,
                    provider=args.provider,
                    deliver_target=args.deliver_target,
                    export_obsidian_note=args.export_obsidian,
                    obsidian_vault_dir=Path(args.obsidian_vault_dir) if args.obsidian_vault_dir else None,
                )
            )
    except WardError as exc:
        return _print_json({"ok": False, "error": str(exc)}, code=2)

    return _print_json({"ok": False, "error": f"unknown command: {args.command}"}, code=2)


if __name__ == "__main__":
    sys.exit(main())
