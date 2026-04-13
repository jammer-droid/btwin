from btwin_cli.api_terminals import _load_providers
from btwin_core.agent_runner import AgentRunner


def test_terminal_provider_loader_returns_unconfigured_payload_when_missing(tmp_path):
    payload = _load_providers(tmp_path)

    assert payload["configured"] is False
    assert payload["providers"] == []
    assert "btwin init --provider codex" in payload["setup_hint"]


def test_agent_runner_provider_loader_returns_empty_without_user_config(tmp_path):
    assert AgentRunner._load_providers(tmp_path / "providers.json") == []
