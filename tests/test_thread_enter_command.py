from typer.testing import CliRunner

import btwin_cli.main as main
from btwin_cli.main import app
from btwin_core.config import BTwinConfig, RuntimeConfig
from btwin_core.thread_chat import parse_thread_chat_input


runner = CliRunner()


def _attached_config() -> BTwinConfig:
    return BTwinConfig(runtime=RuntimeConfig(mode="attached"))


def test_thread_enter_parses_broadcast_prefix():
    decision = parse_thread_chat_input("! everyone please sync")

    assert decision.mode == "broadcast"
    assert decision.targets == []
    assert decision.content == "everyone please sync"


def test_thread_enter_parses_direct_prefix():
    decision = parse_thread_chat_input("@alice please review")

    assert decision.mode == "direct"
    assert decision.targets == ["alice"]
    assert decision.content == "please review"


def test_thread_enter_reads_snapshot_and_sends_direct_message(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: _attached_config())
    monkeypatch.setattr(
        main,
        "_load_thread_enter_snapshot",
        lambda thread_id, actor, config=None: {
            "thread_id": thread_id,
            "topic": "Testing",
            "protocol": "debate",
            "current_phase": "discussion",
            "participants": ["user", "alice"],
            "actor": actor,
            "interaction_mode": "orchestrated_chat",
            "pending_count": 0,
            "pending_messages": [],
            "recent_messages": [],
        },
    )

    sent_messages: list[dict] = []

    def fake_send(thread_id, actor, decision, config=None):
        payload = {
            "thread_id": thread_id,
            "fromAgent": actor,
            "content": decision.content,
            "deliveryMode": decision.mode,
            "targetAgents": decision.targets,
        }
        sent_messages.append(payload)
        return {
            "message_id": "msg-1",
            "delivery_mode": decision.mode,
            "target_agents": decision.targets,
        }

    monkeypatch.setattr(main, "_thread_enter_send_message", fake_send)

    result = runner.invoke(
        app,
        ["thread", "enter", "--thread", "thread-1", "--as", "user"],
        input="@alice check this\n/exit\n",
    )

    assert result.exit_code == 0, result.output
    assert "interaction_mode: orchestrated_chat" in result.output
    assert "route: direct" in result.output
    assert sent_messages[0]["deliveryMode"] == "direct"
    assert sent_messages[0]["targetAgents"] == ["alice"]
