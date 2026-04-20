import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig


runner = CliRunner()


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def test_doctor_reports_attached_runtime_health_and_path_alignment(tmp_path, monkeypatch):
    config_dir = tmp_path / "config-btwin"
    config_path = tmp_path / "config" / "btwin.yaml"
    current_btwin = tmp_path / "bin" / "btwin"
    path_btwin = tmp_path / "bin-path" / "btwin"

    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(config_dir))
    monkeypatch.setattr(main, "_config_path", lambda: config_path)
    monkeypatch.setattr(main, "_get_active_data_dir", lambda config=None: config_dir)
    monkeypatch.setattr(main, "_api_base_url", lambda: "http://attached-api.local")
    monkeypatch.setattr(main, "_current_btwin_command_path", lambda: current_btwin)
    monkeypatch.setattr(main.shutil, "which", lambda name: str(path_btwin) if name == "btwin" else None)
    monkeypatch.setattr(main.sys, "platform", "darwin", raising=False)
    monkeypatch.setattr(main.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        main,
        "_api_get",
        lambda path, params=None: {"status": "ok"} if path == "/api/sessions/status" else None,
    )
    monkeypatch.setattr(
        main,
        "_run_service_command",
        lambda args, check=True: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="service running",
            stderr="",
        ),
    )

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["ok"] is True
    assert payload["runtime_mode"] == "attached"
    assert payload["config_path"] == str(config_path)
    assert payload["data_dir"] == str(config_dir)
    assert payload["checks"]["attached_api"]["status"] == "ok"
    assert payload["checks"]["attached_api"]["ok"] is True
    assert payload["checks"]["launchd_service"]["status"] == "ok"
    assert payload["checks"]["launchd_service"]["ok"] is True
    assert payload["checks"]["path_btwin"]["path"] == str(path_btwin)
    assert payload["checks"]["path_btwin"]["matches_current"] is False


def test_doctor_skips_attached_api_in_standalone_mode(tmp_path, monkeypatch):
    config_dir = tmp_path / "config-btwin"
    config_path = tmp_path / "config" / "btwin.yaml"
    current_btwin = tmp_path / "bin" / "btwin"

    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(config_dir))
    monkeypatch.setattr(main, "_config_path", lambda: config_path)
    monkeypatch.setattr(main, "_get_active_data_dir", lambda config=None: config_dir)
    monkeypatch.setattr(main, "_current_btwin_command_path", lambda: current_btwin)
    monkeypatch.setattr(main.shutil, "which", lambda name: str(current_btwin) if name == "btwin" else None)
    monkeypatch.setattr(main.sys, "platform", "linux", raising=False)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["runtime_mode"] == "standalone"
    assert payload["checks"]["attached_api"]["status"] == "skipped"
    assert payload["checks"]["attached_api"]["detail"] == "runtime.mode is standalone (local mode)"
    assert payload["checks"]["launchd_service"]["status"] == "skipped"
    assert payload["checks"]["path_btwin"]["matches_current"] is True
