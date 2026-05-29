from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import WardConfig


DEFAULT_HERMES_HOME = Path.home() / ".hermes"
REQUIRED_HERMES_FILES = ("auth.json", "config.yaml", ".env")


class HermesRuntimeError(Exception):
    pass


def prepare_hermes_env(config: WardConfig) -> dict[str, str]:
    """Return an environment suitable for non-interactive Hermes subprocesses."""
    env = os.environ.copy()
    requested_home = Path(env.get("HERMES_HOME") or DEFAULT_HERMES_HOME).expanduser()
    hermes_home = requested_home

    if not _home_is_usable(hermes_home):
        hermes_home = _prepare_fallback_home(config, requested_home)

    _validate_required_files(hermes_home)
    _ensure_log_writable(hermes_home)
    env["HERMES_HOME"] = str(hermes_home)
    env.setdefault("HOME", str(Path.home()))
    return env


def _home_is_usable(hermes_home: Path) -> bool:
    try:
        _validate_required_files(hermes_home)
        _ensure_log_writable(hermes_home)
    except HermesRuntimeError:
        return False
    return True


def _prepare_fallback_home(config: WardConfig, source_home: Path) -> Path:
    fallback_home = config.log_dir.parent / "hermes_runtime_home"
    fallback_home.mkdir(parents=True, exist_ok=True)
    (fallback_home / "logs").mkdir(parents=True, exist_ok=True)

    for name in REQUIRED_HERMES_FILES:
        source = source_home / name
        target = fallback_home / name
        if source.is_file():
            shutil.copy2(source, target)

    return fallback_home


def _validate_required_files(hermes_home: Path) -> None:
    missing = [name for name in ("auth.json", "config.yaml") if not (hermes_home / name).is_file()]
    if missing:
        raise HermesRuntimeError(
            f"Hermes home {hermes_home} is missing required file(s): {', '.join(missing)}"
        )


def _ensure_log_writable(hermes_home: Path) -> None:
    log_dir = hermes_home / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "agent.log").open("a", encoding="utf-8"):
            pass
    except OSError as exc:
        raise HermesRuntimeError(f"Hermes log path is not writable: {log_dir}") from exc
