from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ward_pipeline import jobs


@dataclass(frozen=True)
class TestConfig:
    incoming_dir: Path
    output_dir: Path
    case_view_dir: Path
    log_dir: Path
    timezone: str = "Asia/Taipei"


def test_health_summary_does_not_false_flag_discord_delivery_before_send() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = TestConfig(
            incoming_dir=root / "incoming",
            output_dir=root / "output",
            case_view_dir=root / "case_view",
            log_dir=root / "logs",
        )
        for path in (config.incoming_dir, config.output_dir, config.case_view_dir, config.log_dir):
            path.mkdir(parents=True, exist_ok=True)

        original_check_network = jobs._check_network
        original_check_discord_bot = jobs._check_discord_bot
        original_run_command_check = jobs._run_command_check
        original_check_artifact_rw = jobs._check_artifact_rw
        original_check_ward_job = jobs._check_ward_job
        original_check_stt_environment = jobs._check_stt_environment
        original_check_whisper_modules = jobs._check_whisper_modules
        original_check_diarization = jobs._check_diarization
        original_check_identity_policy = jobs._check_identity_policy
        original_check_codex = jobs._check_codex
        original_check_pending_reports = jobs._check_pending_reports
        original_check_disk_space = jobs._check_disk_space
        original_parse_delivery_target = jobs._parse_delivery_target
        original_discord_post_message = jobs._discord_post_message
        original_discord_edit_message = jobs._discord_edit_message
        try:
            jobs._check_network = lambda: jobs._health_result(True, "network ok")
            jobs._check_discord_bot = lambda: jobs._health_result(True, "discord bot ok")
            jobs._run_command_check = lambda argv, timeout=30: jobs._health_result(True, "command ok", exit_code=0)
            jobs._check_artifact_rw = lambda cfg: jobs._health_result(True, "artifact ok")
            jobs._check_ward_job = lambda cfg: jobs._health_result(
                True,
                "job ok",
                job_id="test_health_job",
                job_dir=str(cfg.output_dir / "test_health_job"),
            )
            jobs._check_stt_environment = lambda: jobs._health_result(True, "stt ok", exit_code=0)
            jobs._check_whisper_modules = lambda: jobs._health_result(True, "modules ok")
            jobs._check_diarization = lambda: jobs._health_result(True, "diarization ok")
            jobs._check_identity_policy = lambda: jobs._health_result(True, "identity ok")
            jobs._check_codex = lambda: jobs._health_result(True, "codex ok", exit_code=0)
            jobs._check_pending_reports = lambda cfg: jobs._health_result(True, "0 pending report(s)", count=0, jobs=[])
            jobs._check_disk_space = lambda cfg: jobs._health_result(True, "100 GB free", free_gb=100.0)
            jobs._parse_delivery_target = lambda target: {"platform": "discord", "chat_id": "test"}

            sent_messages: list[str] = []
            edited_messages: list[str] = []

            def fake_discord_post_message(target: dict, content: str) -> dict:
                sent_messages.append(content)
                return {"ok": True, "status": 200, "message_id": "test-message", "channel_id": target["chat_id"]}

            def fake_discord_edit_message(target: dict, message_id: str, content: str) -> dict:
                assert message_id == "test-message"
                edited_messages.append(content)
                return {"ok": True, "status": 200, "message_id": message_id, "channel_id": target["chat_id"]}

            jobs._discord_post_message = fake_discord_post_message
            jobs._discord_edit_message = fake_discord_edit_message

            result = jobs.health(config, target="discord:test")
            assert result["ok"] is True
            assert sent_messages
            assert edited_messages
            assert "Discord 回傳：異常" not in sent_messages[0]
            assert "Discord 回傳：測試中" in sent_messages[0]
            assert "磁碟空間：100 GB free" in sent_messages[0]
            assert "Discord 回傳：正常" in edited_messages[0]
            assert "磁碟空間：100 GB free" in edited_messages[0]
            assert "Discord 回傳：正常" in result["summary"]
            assert "磁碟空間：100 GB free" in result["summary"]
        finally:
            jobs._check_network = original_check_network
            jobs._check_discord_bot = original_check_discord_bot
            jobs._run_command_check = original_run_command_check
            jobs._check_artifact_rw = original_check_artifact_rw
            jobs._check_ward_job = original_check_ward_job
            jobs._check_stt_environment = original_check_stt_environment
            jobs._check_whisper_modules = original_check_whisper_modules
            jobs._check_diarization = original_check_diarization
            jobs._check_identity_policy = original_check_identity_policy
            jobs._check_codex = original_check_codex
            jobs._check_pending_reports = original_check_pending_reports
            jobs._check_disk_space = original_check_disk_space
            jobs._parse_delivery_target = original_parse_delivery_target
            jobs._discord_post_message = original_discord_post_message
            jobs._discord_edit_message = original_discord_edit_message
