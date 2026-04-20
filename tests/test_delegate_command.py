import json
from pathlib import Path

from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.protocol_store import ProtocolStore, compile_protocol_definition
from btwin_core.thread_store import ThreadStore


runner = CliRunner()


def _standalone_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="standalone"), data_dir=data_dir)


def _attached_config(data_dir: Path) -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"), data_dir=data_dir)


def _parse_json_output(output: str):
    return json.loads(output.strip())


def _seed_delegate_thread(project_root: Path):
    thread_store = ThreadStore(project_root / ".btwin" / "threads")
    protocol_store = ProtocolStore(project_root / ".btwin" / "protocols")
    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-review",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [
                            {"role": "reviewer", "action": "review", "alias": "Review"},
                        ],
                    }
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate thread",
        protocol="delegate-review",
        participants=["alice"],
        initial_phase="review",
    )
    return thread_store, thread


def test_delegate_start_outputs_running_state(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"
    thread_store, thread = _seed_delegate_thread(project_root)

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _standalone_config(data_dir))

    start_result = runner.invoke(
        app,
        ["delegate", "start", "--thread", thread["thread_id"], "--json"],
    )

    assert start_result.exit_code == 0, start_result.output
    start_payload = _parse_json_output(start_result.output)
    assert start_payload["status"] == "running"
    assert start_payload["target_role"] == "reviewer"
    assert start_payload["resolved_agent"] == "alice"
    assert start_payload["required_action"] == "submit_contribution"
    assert start_payload["expected_output"] == "review contribution"
    assert "reason_blocked" not in start_payload

    inbox = thread_store.list_inbox(thread["thread_id"], "alice")
    assert len(inbox) == 1

    status_result = runner.invoke(
        app,
        ["delegate", "status", "--thread", thread["thread_id"], "--json"],
    )

    assert status_result.exit_code == 0, status_result.output
    status_payload = _parse_json_output(status_result.output)
    assert status_payload == start_payload


def test_delegate_commands_use_attached_api_when_attached(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    data_dir = tmp_path / ".btwin"

    monkeypatch.setattr(main, "_project_root", lambda: project_root)
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config(data_dir))
    monkeypatch.setattr(main, "_get_thread_store", lambda: (_ for _ in ()).throw(AssertionError("local thread store should not be used")))
    monkeypatch.setattr(main, "_get_protocol_store", lambda: (_ for _ in ()).throw(AssertionError("local protocol store should not be used")))

    calls: list[tuple[str, object]] = []

    def fake_attached_call(path: str, data: dict) -> dict:
        calls.append((path, data))
        return {
            "thread_id": "thread-1",
            "status": "running",
            "updated_at": "2026-04-20T00:00:00Z",
            "target_role": "reviewer",
            "resolved_agent": "alice",
            "required_action": "submit_contribution",
            "expected_output": "review contribution",
        }

    def fake_attached_get(path: str, params: dict | None = None) -> dict:
        calls.append((path, params))
        return {
            "thread_id": "thread-1",
            "status": "running",
            "updated_at": "2026-04-20T00:00:00Z",
            "target_role": "reviewer",
            "resolved_agent": "alice",
            "required_action": "submit_contribution",
            "expected_output": "review contribution",
        }

    monkeypatch.setattr(main, "_attached_api_call_or_exit", fake_attached_call)
    monkeypatch.setattr(main, "_attached_api_get_or_exit", fake_attached_get)

    start_result = runner.invoke(app, ["delegate", "start", "--thread", "thread-1", "--json"])
    assert start_result.exit_code == 0, start_result.output
    assert _parse_json_output(start_result.output)["status"] == "running"

    status_result = runner.invoke(app, ["delegate", "status", "--thread", "thread-1", "--json"])
    assert status_result.exit_code == 0, status_result.output
    assert _parse_json_output(status_result.output)["resolved_agent"] == "alice"

    assert calls == [
        ("/api/threads/thread-1/delegate/start", {}),
        ("/api/threads/thread-1/delegate/status", None),
    ]
