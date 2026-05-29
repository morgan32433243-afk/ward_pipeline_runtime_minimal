from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

from .config import WardConfig

CODEX_BIN = os.environ.get("WARD_CODEX_BIN", "codex")
CODEX_TIMEOUT_SECONDS = 900


def run_codex_exec(
    prompt: str,
    *,
    config: WardConfig,
    cwd: Path,
    output_dir: Path | None = None,
    model: str | None = None,
    timeout: int = CODEX_TIMEOUT_SECONDS,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run Codex CLI and return the clean last assistant message."""
    output_root = output_dir or cwd
    output_root.mkdir(parents=True, exist_ok=True)
    output_path = output_root / f".codex-last-message-{secrets.token_hex(8)}.md"
    command = [
        CODEX_BIN,
        "exec",
        "--skip-git-repo-check",
        "--color",
        "never",
        "-o",
        str(output_path),
    ]
    if model:
        command.extend(["--model", model])
    command.append("-")

    env = os.environ.copy()
    codex_parent = Path(CODEX_BIN).expanduser().parent
    if str(codex_parent) not in {"", "."}:
        env["PATH"] = f"{codex_parent}:{env.get('PATH', '')}"
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("WARD_OUTPUT_DIR", str(config.output_dir))

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            env=env,
        )
    finally:
        result_text = output_path.read_text(encoding="utf-8", errors="replace").strip() if output_path.exists() else ""
        output_path.unlink(missing_ok=True)

    return completed, result_text
