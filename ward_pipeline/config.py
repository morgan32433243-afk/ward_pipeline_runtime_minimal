from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WardConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    obsidian_vault_dir: Path
    stt_review_queue_dir: Path
    timezone: str


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _path_from_env(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default)).expanduser().resolve()


def load_config(project_root: Path | None = None) -> WardConfig:
    root = project_root or Path(__file__).resolve().parents[1]
    _load_env_file(Path.home() / ".hermes" / ".env")
    _load_env_file(root / ".env")

    return WardConfig(
        incoming_dir=_path_from_env("INCOMING_DIR", str(root / "data" / "incoming_audio")),
        output_dir=_path_from_env("OUTPUT_DIR", str(root / "data" / "output")),
        case_view_dir=_path_from_env("CASE_VIEW_DIR", str(root / "data" / "case_views")),
        log_dir=_path_from_env("LOG_DIR", str(root / "data" / "logs")),
        obsidian_vault_dir=_path_from_env(
            "OBSIDIAN_VAULT_DIR",
            str(root / "data" / "obsidian_vault"),
        ),
        stt_review_queue_dir=_path_from_env("WARD_STT_REVIEW_QUEUE_DIR", str(root / "data" / "stt_review_queue")),
        timezone=os.environ.get("WARD_TIMEZONE", "Asia/Taipei"),
    )
