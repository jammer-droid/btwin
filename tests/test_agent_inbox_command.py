import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.storage import Storage
from btwin_core.thread_store import ThreadStore
from btwin_core.workflow_engine import WorkflowEngine


runner = CliRunner()


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def _build_agent_inbox_fixtures(tmp_path: Path):
    data_dir = tmp_path / ".btwin"
    project_root = tmp_path
    agent_store = AgentStore(data_dir)
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    workflow_engine = WorkflowEngine(Storage(data_dir))

    agent_store.register(
        name="alice",
        model="gpt-5",
        alias="alice",
        provider="codex",
        role="implementer",
    )

    workflow = workflow_engine.create_workflow(
        name="Inbox workflow",
        task_names=["Draft plan"],
        assigned_agents=["alice"],
    )
    task = workflow["tasks"][0]
    agent_store.enqueue_task("alice", workflow["workflow_id"], task["task_id"])

    primary_thread = thread_store.create_thread(
        topic="Primary thread",
        protocol="debate",
        participants=["alice", "bob"],
        initial_phase="context",
    )
    thread_store.send_message(
        thread_id=primary_thread["thread_id"],
        from_agent="bob",
        content="Please review the queue item.",
        tldr="Review the queue item.",
        delivery_mode="direct",
        target_agents=["alice"],
    )

    thread_store.create_thread(
        topic="Idle thread",
        protocol="debate",
        participants=["alice"],
        initial_phase="context",
    )
    thread_store.create_thread(
        topic="Other agent thread",
        protocol="debate",
        participants=["bob"],
        initial_phase="context",
    )

    return data_dir, agent_store, thread_store, workflow


def test_agent_inbox_standalone_summarizes_queue_and_threads(tmp_path, monkeypatch):
    data_dir, agent_store, thread_store, workflow = _build_agent_inbox_fixtures(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    result = runner.invoke(app, ["agent", "inbox", "alice", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)

    assert payload["agent"]["name"] == "alice"
    assert payload["queue_count"] == 1
    assert payload["active_thread_count"] == 2
    assert payload["pending_thread_count"] == 1
    assert payload["pending_message_count"] == 1
    assert payload["runtime_session_count"] == 0
    assert payload["runtime_sessions"] == []

    queue_item = payload["queue"][0]
    assert queue_item["workflow_id"] == workflow["workflow_id"]
    assert queue_item["workflow_name"] == "Inbox workflow"
    assert queue_item["task_name"] == "Draft plan"
    assert queue_item["task_status"] == "pending"

    assert len(payload["active_threads"]) == 2
    assert {thread["topic"] for thread in payload["active_threads"]} == {
        "Primary thread",
        "Idle thread",
    }
    primary_thread = next(thread for thread in payload["active_threads"] if thread["pending_message_count"] == 1)
    assert primary_thread["topic"] == "Primary thread"
    assert primary_thread["participant_status"] == "joined"
    assert primary_thread["pending_messages"][0]["tldr"] == "Review the queue item."


def test_agent_inbox_attached_enriches_runtime_sessions(tmp_path, monkeypatch):
    data_dir, agent_store, thread_store, _workflow = _build_agent_inbox_fixtures(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)
    monkeypatch.setattr(
        main,
        "_api_get",
        lambda path, params=None: {
            "agents": {
                "alice": [
                    {
                        "thread_id": "thread-20260413-abc123",
                        "provider": "codex",
                        "transport_mode": "stdio",
                        "status": "active",
                    }
                ]
            }
        },
    )

    result = runner.invoke(app, ["agent", "inbox", "alice", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["runtime_session_count"] == 1
    assert payload["runtime_sessions"][0]["thread_id"] == "thread-20260413-abc123"
    assert payload["runtime_sessions"][0]["provider"] == "codex"


def test_agent_inbox_missing_runtime_data_does_not_fail(tmp_path, monkeypatch):
    data_dir, agent_store, thread_store, _workflow = _build_agent_inbox_fixtures(tmp_path)

    monkeypatch.setattr(main, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)
    monkeypatch.setattr(main, "_get_thread_store", lambda: thread_store)

    def fail_runtime_status(path, params=None):
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(main, "_api_get", fail_runtime_status)

    result = runner.invoke(app, ["agent", "inbox", "alice", "--json"])

    assert result.exit_code == 0, result.output
    payload = _parse_json_output(result.output)
    assert payload["runtime_session_count"] == 0
    assert payload["runtime_sessions"] == []


def test_agent_inbox_missing_agent_exits_4(tmp_path, monkeypatch):
    data_dir = tmp_path / ".btwin"
    agent_store = AgentStore(data_dir)

    monkeypatch.setattr(main, "_get_agent_store", lambda: agent_store)

    result = runner.invoke(app, ["agent", "inbox", "missing"])

    assert result.exit_code == 4
    assert "Agent not found" in result.output
