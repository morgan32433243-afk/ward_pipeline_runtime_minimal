from __future__ import annotations

import os
import platform
from pathlib import Path


def system_name() -> str:
    return platform.system().lower()


def is_windows() -> bool:
    return os.name == "nt" or system_name().startswith("win")


def runtime_root() -> Path:
    return Path(__file__).resolve().parents[1]


def stt_python_path(root: Path | None = None) -> Path:
    base = root or runtime_root()
    configured = os.environ.get("WARD_STT_PYTHON")
    if configured:
        return Path(configured).expanduser().resolve()
    if is_windows():
        return (base / ".venv" / "Scripts" / "python.exe").resolve()
    return (base / ".venv" / "bin" / "python").resolve()

