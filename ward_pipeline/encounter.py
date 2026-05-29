from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import WardConfig


AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".caf", ".flac", ".ogg"}
DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "data" / "ward_audio_archive"
PROBABLE_SAME_ENCOUNTER_MINUTES = 45
UNCERTAIN_SAME_ENCOUNTER_MINUTES = 120
ENCOUNTER_META_FILE = "encounter.meta.json"


@dataclass(frozen=True)
class RoutedAudio:
    archived_path: Path
    encounter_dir: Path
    encounter_id: str
    bed_id: str
    confidence: str
    requires_identity_review: bool
    grouping_basis: list[str]
    route_status: str


def _now(config: WardConfig) -> datetime:
    return datetime.now(ZoneInfo(config.timezone))


def _archive_root() -> Path:
    return Path(os.environ.get("WARD_AUDIO_ARCHIVE_DIR", str(DEFAULT_ARCHIVE_DIR))).expanduser().resolve()


def is_audio_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS


def infer_bed_id(path: Path, explicit_bed_id: str | None = None) -> str:
    if explicit_bed_id:
        return _sanitize_bed_id(explicit_bed_id)

    parent_names = [path.parents[index].name for index in range(min(3, len(path.parents)))]
    text = " ".join([path.stem, *parent_names])
    patterns = (
        r"\bbed[-_\s]?([A-Za-z0-9]{1,4})\b",
        r"\b床號?[-_\s]?([A-Za-z0-9]{1,4})\b",
        r"\b([0-9]{1,3}[A-Za-z]?)床\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return _sanitize_bed_id(match.group(1))
    return "unknown"


def _sanitize_bed_id(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9-]", "", raw.strip())
    return cleaned or "unknown"


def _is_synthetic_unknown_bed(name: str) -> bool:
    return bool(re.fullmatch(r"unknown-\d{3}", name))


def _date_dir(config: WardConfig, received_at: datetime) -> Path:
    return _archive_root() / received_at.strftime("%Y-%m-%d")


def _bed_dir(config: WardConfig, received_at: datetime, bed_id: str) -> Path:
    return _date_dir(config, received_at) / f"bed-{bed_id}"


def _next_unknown_bed_id(config: WardConfig, received_at: datetime) -> str:
    date_dir = _date_dir(config, received_at)
    if not date_dir.exists():
        return "unknown-001"

    highest = 0
    for bed_dir in date_dir.glob("bed-unknown-*"):
        if not bed_dir.is_dir():
            continue
        bed_id = bed_dir.name.removeprefix("bed-")
        if not _is_synthetic_unknown_bed(bed_id):
            continue
        suffix = int(bed_id.rsplit("-", 1)[-1])
        highest = max(highest, suffix)
    return f"unknown-{highest + 1:03d}"


def _existing_encounters(bed_dir: Path) -> list[Path]:
    if not bed_dir.exists():
        return []
    return sorted(path for path in bed_dir.glob("encounter-*") if path.is_dir())


def _read_meta(encounter_dir: Path) -> dict:
    meta_path = encounter_dir / ENCOUNTER_META_FILE
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _write_meta(encounter_dir: Path, meta: dict) -> None:
    (encounter_dir / ENCOUNTER_META_FILE).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _next_encounter_dir(bed_dir: Path) -> Path:
    existing = _existing_encounters(bed_dir)
    if not existing:
        return bed_dir / "encounter-001"
    last_num = max(int(path.name.split("-")[-1]) for path in existing if path.name.split("-")[-1].isdigit())
    return bed_dir / f"encounter-{last_num + 1:03d}"


def _last_audio_time(meta: dict) -> datetime | None:
    timestamps = [
        item.get("received_at")
        for item in meta.get("audio_files", [])
        if isinstance(item, dict) and item.get("received_at")
    ]
    if not timestamps:
        return None
    try:
        return datetime.fromisoformat(max(timestamps))
    except ValueError:
        return None


def _choose_encounter(config: WardConfig, bed_dir: Path, bed_id: str, received_at: datetime) -> tuple[Path, str, bool, list[str], str]:
    existing = _existing_encounters(bed_dir)
    if not existing:
        return (
            bed_dir / "encounter-001",
            "unverified",
            bed_id == "unknown",
            ["same_date", "same_bed", "first_encounter_for_bed"],
            "new_encounter",
        )

    latest = existing[-1]
    meta = _read_meta(latest)
    last_time = _last_audio_time(meta)
    if last_time is None:
        return (
            latest,
            "unverified",
            True,
            ["same_date", "same_bed", "missing_previous_audio_timestamp"],
            "append_uncertain",
        )

    gap_minutes = abs((received_at - last_time).total_seconds()) / 60
    if gap_minutes <= PROBABLE_SAME_ENCOUNTER_MINUTES:
        return (
            latest,
            "probable",
            bed_id == "unknown",
            ["same_date", "same_bed", f"time_gap_{gap_minutes:.1f}_min_under_45"],
            "append_probable",
        )
    if gap_minutes <= UNCERTAIN_SAME_ENCOUNTER_MINUTES:
        return (
            latest,
            "uncertain",
            True,
            ["same_date", "same_bed", f"time_gap_{gap_minutes:.1f}_min_between_45_and_120"],
            "append_uncertain",
        )
    return (
        _next_encounter_dir(bed_dir),
        "unverified",
        True,
        ["same_date", "same_bed", f"time_gap_{gap_minutes:.1f}_min_over_120"],
        "new_encounter",
    )


def _unique_audio_path(audio_dir: Path, received_at: datetime, part_number: int, suffix: str) -> Path:
    base = f"{received_at.strftime('%Y%m%d_%H%M%S')}_part-{part_number:03d}{suffix.lower()}"
    candidate = audio_dir / base
    if not candidate.exists():
        return candidate
    for index in range(1, 1000):
        candidate = audio_dir / f"{received_at.strftime('%Y%m%d_%H%M%S')}_part-{part_number:03d}_{index:03d}{suffix.lower()}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"could not allocate unique archive path in {audio_dir}")


def route_audio_file(
    config: WardConfig,
    audio_path: Path,
    *,
    bed_id: str | None = None,
    move: bool = True,
    received_at: datetime | None = None,
) -> RoutedAudio:
    source = audio_path.expanduser().resolve()
    if not is_audio_file(source):
        raise ValueError(f"not a supported audio file: {source}")

    received_at = received_at or _now(config)
    raw_inferred_bed = infer_bed_id(source, explicit_bed_id=bed_id)
    inferred_bed = raw_inferred_bed
    bed_requires_identity_review = raw_inferred_bed == "unknown" or _is_synthetic_unknown_bed(raw_inferred_bed)
    if raw_inferred_bed == "unknown":
        inferred_bed = _next_unknown_bed_id(config, received_at)
        bed_requires_identity_review = True
    bed_dir = _bed_dir(config, received_at, inferred_bed)
    encounter_dir, confidence, needs_review, basis, route_status = _choose_encounter(
        config, bed_dir, inferred_bed, received_at
    )
    needs_review = needs_review or bed_requires_identity_review
    audio_dir = encounter_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    existing_meta = _read_meta(encounter_dir)
    part_number = len(existing_meta.get("audio_files", [])) + 1
    destination = _unique_audio_path(audio_dir, received_at, part_number, source.suffix)
    if move:
        shutil.move(str(source), str(destination))
    else:
        shutil.copy2(source, destination)

    encounter_id = f"{received_at.strftime('%Y%m%d')}_bed-{inferred_bed}_{encounter_dir.name}"
    meta = existing_meta or {
        "schema_version": "1.0",
        "date": received_at.strftime("%Y-%m-%d"),
        "bed_id": inferred_bed,
        "encounter_id": encounter_id,
        "routing_metadata_role": "file_organization_only_not_clinical_evidence",
        "identity_status": "unverified",
        "same_encounter_confidence": "unverified",
        "requires_identity_review": bed_requires_identity_review,
        "grouping_basis": [],
        "safety_flags": [
            "bed_id_is_location_only",
            "bed_id_is_not_patient_identity",
            "do_not_merge_across_dates",
            "do_not_use_routing_metadata_as_clinical_fact",
            "requires_clinician_review_before_final_note",
        ],
        "audio_files": [],
        "ward_jobs": [],
    }

    meta["same_encounter_confidence"] = confidence
    meta["requires_identity_review"] = bool(meta.get("requires_identity_review")) or needs_review
    meta["grouping_basis"] = list(dict.fromkeys([*meta.get("grouping_basis", []), *basis]))
    meta["audio_files"].append(
        {
            "file": destination.name,
            "path": str(destination),
            "received_at": received_at.isoformat(timespec="seconds"),
            "source_path": str(source),
            "route_status": route_status,
        }
    )
    _write_meta(encounter_dir, meta)

    return RoutedAudio(
        archived_path=destination,
        encounter_dir=encounter_dir,
        encounter_id=encounter_id,
        bed_id=inferred_bed,
        confidence=confidence,
        requires_identity_review=bool(meta["requires_identity_review"]),
        grouping_basis=meta["grouping_basis"],
        route_status=route_status,
    )


def append_ward_job(encounter_dir: Path, job_id: str, job_dir: str, audio_path: Path) -> None:
    meta = _read_meta(encounter_dir)
    jobs = meta.setdefault("ward_jobs", [])
    if not any(item.get("job_id") == job_id for item in jobs):
        jobs.append({"job_id": job_id, "job_dir": job_dir, "audio_path": str(audio_path)})
    _write_meta(encounter_dir, meta)
