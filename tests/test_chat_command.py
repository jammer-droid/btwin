from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig


runner = CliRunner()


def test_chat_guardrail_uses_local_and_runtime_attached_wording(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: BTwinConfig(runtime=RuntimeConfig(mode="attached")))

    result = runner.invoke(app, ["chat"])

    assert result.exit_code == 1, result.output
    assert "Chat mode is only supported in local mode." in result.output
    assert "runtime.mode: standalone" in result.output
    assert "runtime-attached shared sessions" in result.output
    assert "standalone runtime mode" not in result.output
