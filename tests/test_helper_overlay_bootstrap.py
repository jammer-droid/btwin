from __future__ import annotations

from pathlib import Path

import pytest

from btwin_core.agent_runner import AgentRunner
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig
from btwin_core.event_bus import EventBus
from btwin_core.protocol_store import ProtocolStore
from btwin_core.thread_store import ThreadStore


def _build_runner(tmp_path: Path) -> AgentRunner:
    data_dir = tmp_path / "data"
    threads_dir = data_dir / "threads"
    threads_dir.mkdir(parents=True)
    return AgentRunner(
        ThreadStore(threads_dir),
        ProtocolStore(data_dir / "protocols"),
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )


def _write_codex_trust_config(home_dir: Path, project_path: Path, *, trust_level: str = "trusted") -> None:
    codex_dir = home_dir / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "config.toml").write_text(
        f'[projects."{project_path}"]\ntrust_level = "{trust_level}"\n',
        encoding="utf-8",
    )


def test_helper_overlay_paths_are_repo_local_and_agent_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    _write_codex_trust_config(home_dir, repo_root)
    requested_workspace = repo_root / "packages" / "btwin-core"
    requested_workspace.mkdir(parents=True)

    overlay = runner._derive_helper_overlay_paths(
        agent_name="agent-1",
        workspace_root=requested_workspace,
    )

    assert overlay.repo_root == repo_root
    assert overlay.overlay_root == repo_root / ".btwin" / "helpers" / "agent-1"
    assert overlay.launch_cwd == repo_root / ".btwin" / "helpers" / "agent-1" / "workspace"


def test_helper_overlay_preflight_materializes_launch_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    _write_codex_trust_config(home_dir, repo_root)
    requested_workspace = repo_root / "app"
    requested_workspace.mkdir()

    launch_cwd = runner._prepare_helper_workspace(
        provider_name="codex",
        agent_name="agent-1",
        workspace_root=requested_workspace,
    )

    assert launch_cwd == repo_root / ".btwin" / "helpers" / "agent-1" / "workspace"
    assert launch_cwd.is_dir()
    assert launch_cwd.parent.is_dir()


def test_helper_overlay_preflight_materializes_agents_and_hooks_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    _write_codex_trust_config(home_dir, repo_root)
    requested_workspace = repo_root / "app"
    requested_workspace.mkdir()

    launch_cwd = runner._prepare_helper_workspace(
        provider_name="codex",
        agent_name="agent-1",
        workspace_root=requested_workspace,
    )

    overlay_root = launch_cwd.parent
    agents_path = overlay_root / "AGENTS.md"
    hooks_path = overlay_root / ".codex" / "hooks.json"

    assert agents_path.is_file()
    assert hooks_path.is_file()
    assert "B-TWIN-managed helper workspace" in agents_path.read_text(encoding="utf-8")
    assert "workflow hook" in hooks_path.read_text(encoding="utf-8")


def test_helper_overlay_preflight_keeps_repo_user_files_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    _write_codex_trust_config(home_dir, repo_root)
    repo_agents_path = repo_root / "AGENTS.md"
    repo_hooks_path = repo_root / ".codex" / "hooks.json"
    repo_hooks_path.parent.mkdir(parents=True)
    repo_agents_path.write_text("USER ROOT AGENTS\n", encoding="utf-8")
    repo_hooks_path.write_text('{"hooks":{"Stop":[]}}\n', encoding="utf-8")
    requested_workspace = repo_root / "app"
    requested_workspace.mkdir()

    runner._prepare_helper_workspace(
        provider_name="codex",
        agent_name="agent-1",
        workspace_root=requested_workspace,
    )

    assert repo_agents_path.read_text(encoding="utf-8") == "USER ROOT AGENTS\n"
    assert repo_hooks_path.read_text(encoding="utf-8") == '{"hooks":{"Stop":[]}}\n'


def test_helper_overlay_preflight_fails_clearly_when_repo_is_not_trusted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    home_dir = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home_dir))
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    _write_codex_trust_config(home_dir, repo_root, trust_level="untrusted")
    requested_workspace = repo_root / "app"
    requested_workspace.mkdir()

    with pytest.raises(RuntimeError) as excinfo:
        runner._prepare_helper_workspace(
            provider_name="codex",
            agent_name="agent-1",
            workspace_root=requested_workspace,
        )

    assert "trusted Codex project" in str(excinfo.value)
    assert str(repo_root) in str(excinfo.value)


def test_helper_overlay_preflight_fails_clearly_outside_git_repo(tmp_path: Path) -> None:
    runner = _build_runner(tmp_path)
    requested_workspace = tmp_path / "plain-workspace"
    requested_workspace.mkdir()

    with pytest.raises(RuntimeError) as excinfo:
        runner._prepare_helper_workspace(
            provider_name="codex",
            agent_name="agent-1",
            workspace_root=requested_workspace,
        )

    assert "not inside a git repo" in str(excinfo.value)
    assert str(requested_workspace) in str(excinfo.value)
