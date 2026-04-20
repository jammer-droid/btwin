from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from btwin_core.agent_runner import AgentRunner, LaunchResolution
from btwin_core.agent_store import AgentStore
from btwin_core.auth_adapters import ResolvedLaunchAuth
from btwin_core.config import BTwinConfig
from btwin_core.event_bus import EventBus, SSEEvent
from btwin_core.protocol_store import ProtocolStore
from btwin_core.providers import CLIProvider, CodexProvider, StreamEvent
from btwin_core.prototypes.persistent_sessions.types import (
    SessionCloseResult,
    SessionConfig,
    SessionEvent,
    SessionHealth,
    SessionStartResult,
    SessionTurn,
)
from btwin_core.session_supervisor import RuntimeSession
from btwin_core.thread_store import ThreadStore


class _FakeLiveTransportAdapter:
    def __init__(self, events: list[SessionEvent]) -> None:
        self._events = events
        self.started_with: SessionConfig | None = None
        self.sent_turns: list[SessionTurn] = []
        self.closed = False

    async def start(self, config: SessionConfig) -> SessionStartResult:
        self.started_with = config
        return SessionStartResult(
            session_id="thread-abc",
            metadata={"ok": True, "provider": "codex"},
        )

    async def send_turn(self, turn: SessionTurn) -> None:
        self.sent_turns.append(turn)

    def read_events(self) -> AsyncIterator[SessionEvent]:
        async def iterator() -> AsyncIterator[SessionEvent]:
            for event in self._events:
                yield event
            raise RuntimeError("live transport boom")

        return iterator()

    async def health_check(self) -> SessionHealth:
        return SessionHealth(ok=True)

    async def close(self) -> SessionCloseResult:
        self.closed = True
        return SessionCloseResult(ok=True)


class _FakeLiveTransport:
    mode = "live_process_transport"
    fallback_mode = None
    requires_health_check_before_reuse = False
    supports_resume_fallback = False

    def __init__(self, adapter: _FakeLiveTransportAdapter) -> None:
        self._adapter = adapter

    def build_adapter(self, launch_context=None) -> _FakeLiveTransportAdapter:
        del launch_context
        return self._adapter

    def build_session_config(self, launch_context=None, *, resume_session_id=None) -> SessionConfig:
        del launch_context, resume_session_id
        return SessionConfig()


class _FakeSubprocessStdout:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    def __aiter__(self) -> _FakeSubprocessStdout:
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            await asyncio.sleep(0)
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeSubprocessStderr:
    async def read(self) -> bytes:
        return b"subprocess failed"


class _FakeSubprocess:
    pid = 4321

    def __init__(self, stdout_lines: list[bytes], returncode: int = 1) -> None:
        self.stdout = _FakeSubprocessStdout(stdout_lines)
        self.stderr = _FakeSubprocessStderr()
        self.stdin = None
        self.killed = False
        self.returncode = returncode

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class _FakeStreamingProvider(CLIProvider):
    @property
    def name(self) -> str:
        return "codex"

    def build_command(self, session_id: str | None, bypass_permissions: bool) -> list[str]:
        del session_id, bypass_permissions
        return ["fake-codex"]

    def parse_stream_line(self, line: str) -> StreamEvent | None:
        if line.strip():
            return StreamEvent(event_type="assistant", text_delta="typing", raw={"line": line})
        return None

    def parse_final_response(self, output: str) -> str:
        return output.strip()

    def parse_session_id_from_output(self, output: str) -> str | None:
        del output
        return None

    def env_overrides(self, launch_auth=None) -> dict[str, str]:
        del launch_auth
        return {}


class _FakeNonCodexStreamingProvider(_FakeStreamingProvider):
    @property
    def name(self) -> str:
        return "claude-code"


class _MixedStreamingProvider(_FakeStreamingProvider):
    def parse_stream_line(self, line: str) -> StreamEvent | None:
        stripped = line.strip()
        if stripped == "delta":
            return StreamEvent(event_type="assistant", text_delta="delta", raw={"line": line})
        if stripped == "complete":
            return StreamEvent(
                event_type="turn_complete",
                is_final=True,
                final_text="Done from turn complete",
                raw={"line": line},
            )
        return None


class _ParseFallbackProvider(_FakeStreamingProvider):
    def parse_stream_line(self, line: str) -> StreamEvent | None:
        del line
        return None

    def parse_final_response(self, output: str) -> str:
        return "Parsed from batch fallback"


class _TextDeltaOnlyProvider(_FakeStreamingProvider):
    def parse_stream_line(self, line: str) -> StreamEvent | None:
        stripped = line.strip()
        if stripped:
            return StreamEvent(event_type="assistant", text_delta=stripped, raw={"line": line})
        return None

    def parse_final_response(self, output: str) -> str:
        raise AssertionError("parse_final_response should not be used for pure text_delta_only streams")


def _drain_events(queue: asyncio.Queue[SSEEvent]) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


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


@pytest.mark.asyncio
async def test_live_transport_failure_still_publishes_typing_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    event_queue = runner._event_bus.subscribe()

    adapter = _FakeLiveTransportAdapter(
        [
            SessionEvent(kind="turn_started", content="turn-1"),
            SessionEvent(kind="text_delta", content="Hello"),
        ]
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.build_transport_for_provider",
        lambda *args, **kwargs: _FakeLiveTransport(adapter),
    )

    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
    )
    launch = LaunchResolution(
        provider=CodexProvider(),
        auth=ResolvedLaunchAuth(
            provider_name="codex",
            mode="cli_environment",
        ),
        env={},
        metadata={},
    )

    result = await runner._run_live_transport(
        session,
        "prompt text",
        launch,
        thread_id="thread-123",
        agent_name="agent-1",
    )

    events = _drain_events(event_queue)
    typing_events = [event for event in events if event.type == "agent_typing"]
    typing_done_events = [event for event in events if event.type == "agent_typing_done"]

    assert result.ok is False
    assert typing_events
    assert len(typing_done_events) == 1
    assert events.index(typing_done_events[0]) > events.index(typing_events[0])
    assert adapter.closed is True


@pytest.mark.asyncio
async def test_subprocess_failure_still_publishes_typing_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    event_queue = runner._event_bus.subscribe()

    fake_proc = _FakeSubprocess([b'{"type":"assistant","message":{"content":[{"type":"text","text":"delta"}]}}\\n'])

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        return fake_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await runner._run_subprocess(
        ["fake-codex"],
        "prompt text",
        _FakeStreamingProvider(),
        thread_id="thread-123",
        agent_name="agent-1",
    )

    events = _drain_events(event_queue)
    typing_events = [event for event in events if event.type == "agent_typing"]
    typing_done_events = [event for event in events if event.type == "agent_typing_done"]

    assert result.ok is False
    assert typing_events
    assert len(typing_done_events) == 1
    assert events.index(typing_done_events[0]) > events.index(typing_events[0])
    assert fake_proc.killed is False


@pytest.mark.asyncio
async def test_run_subprocess_uses_raised_stream_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    captured_kwargs: dict[str, object] = {}
    fake_proc = _FakeSubprocess([], returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args
        captured_kwargs.update(kwargs)
        return fake_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await runner._run_subprocess(
        ["fake-codex"],
        "prompt text",
        _FakeStreamingProvider(),
        thread_id="thread-123",
        agent_name="agent-1",
    )

    assert result.ok is True
    assert captured_kwargs["limit"] == 1024 * 1024


@pytest.mark.asyncio
async def test_run_subprocess_prefers_session_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    captured_kwargs: dict[str, object] = {}

    fake_proc = _FakeSubprocess([], returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args
        captured_kwargs.update(kwargs)
        return fake_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    workspace_root = tmp_path / "project-root"
    workspace_root.mkdir()

    result = await runner._run_subprocess(
        ["fake-codex"],
        "prompt text",
        _FakeNonCodexStreamingProvider(),
        thread_id="thread-123",
        agent_name="agent-1",
        workspace_root=workspace_root,
    )

    assert result.ok is True
    assert captured_kwargs["cwd"] == str(workspace_root)


@pytest.mark.asyncio
async def test_run_subprocess_labels_mixed_text_delta_and_turn_complete_final_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    thread = runner._threads.create_thread(
        topic="Subprocess promotion",
        protocol="code-review",
        participants=["agent-1"],
        initial_phase="analysis",
    )
    fake_proc = _FakeSubprocess([b"delta\n", b"complete\n"], returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        return fake_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await runner._run_subprocess(
        ["fake-codex"],
        "prompt text",
        _MixedStreamingProvider(),
        thread_id=thread["thread_id"],
        agent_name="agent-1",
    )
    runner._persist_invocation_outputs(thread["thread_id"], "agent-1", result, chain_depth=1)

    messages = runner._threads.list_messages(thread["thread_id"])
    telemetry_rows = [
        row["payload"]
        for row in runner._validation_telemetry.tail(limit=10, thread_id=thread["thread_id"])
    ]

    assert result.ok is True
    assert result.response_text == "Done from turn complete"
    assert result.outputs[-1].promotion_source == "turn_complete_text_delta"
    assert result.outputs[-1].promotion_basis == "turn_complete_text_delta_fallback"
    assert messages[-1]["routing_source"] == "turn_complete_text_delta"
    assert messages[-1]["routing_reason"] == (
        "official_response_source=turn_complete_text_delta; "
        "official_response_basis=turn_complete_text_delta_fallback; "
        "contribution_candidate_basis=turn_complete_text_delta_fallback"
    )
    assert any(
        payload["signal"] == "message_persisted"
        and payload["official_response_source"] == "turn_complete_text_delta"
        and payload["official_response_basis"] == "turn_complete_text_delta_fallback"
        and payload["contribution_candidate_basis"] == "turn_complete_text_delta_fallback"
        for payload in telemetry_rows
    )


@pytest.mark.asyncio
async def test_run_subprocess_labels_parse_final_response_fallback_as_subprocess_final_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    thread = runner._threads.create_thread(
        topic="Subprocess fallback",
        protocol="code-review",
        participants=["agent-1"],
        initial_phase="analysis",
    )
    fake_proc = _FakeSubprocess([b"noise one\n", b"noise two\n"], returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        return fake_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await runner._run_subprocess(
        ["fake-codex"],
        "prompt text",
        _ParseFallbackProvider(),
        thread_id=thread["thread_id"],
        agent_name="agent-1",
    )
    runner._persist_invocation_outputs(thread["thread_id"], "agent-1", result, chain_depth=1)

    messages = runner._threads.list_messages(thread["thread_id"])
    telemetry_rows = [
        row["payload"]
        for row in runner._validation_telemetry.tail(limit=10, thread_id=thread["thread_id"])
    ]

    assert result.ok is True
    assert result.response_text == "Parsed from batch fallback"
    assert result.outputs[-1].promotion_source == "subprocess_final_response"
    assert result.outputs[-1].promotion_basis == "final_answer_subprocess_output"
    assert messages[-1]["routing_source"] == "subprocess_final_response"
    assert messages[-1]["routing_reason"] == (
        "official_response_source=subprocess_final_response; "
        "official_response_basis=final_answer_subprocess_output; "
        "contribution_candidate_basis=final_answer_subprocess_output"
    )
    assert any(
        payload["signal"] == "message_persisted"
        and payload["official_response_source"] == "subprocess_final_response"
        and payload["official_response_basis"] == "final_answer_subprocess_output"
        and payload["contribution_candidate_basis"] == "final_answer_subprocess_output"
        for payload in telemetry_rows
    )


@pytest.mark.asyncio
async def test_run_subprocess_labels_pure_text_delta_stream_as_text_delta_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    thread = runner._threads.create_thread(
        topic="Subprocess delta-only",
        protocol="code-review",
        participants=["agent-1"],
        initial_phase="analysis",
    )
    fake_proc = _FakeSubprocess([b"alpha\n", b"beta\n"], returncode=0)

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        return fake_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await runner._run_subprocess(
        ["fake-codex"],
        "prompt text",
        _TextDeltaOnlyProvider(),
        thread_id=thread["thread_id"],
        agent_name="agent-1",
    )
    runner._persist_invocation_outputs(thread["thread_id"], "agent-1", result, chain_depth=1)

    messages = runner._threads.list_messages(thread["thread_id"])
    telemetry_rows = [
        row["payload"]
        for row in runner._validation_telemetry.tail(limit=10, thread_id=thread["thread_id"])
    ]

    assert result.ok is True
    assert result.response_text == "alphabeta"
    assert result.outputs[-1].promotion_source == "text_delta_only"
    assert result.outputs[-1].promotion_basis == "text_delta_only_fallback"
    assert messages[-1]["routing_source"] == "text_delta_only"
    assert messages[-1]["routing_reason"] == (
        "official_response_source=text_delta_only; "
        "official_response_basis=text_delta_only_fallback; "
        "contribution_candidate_basis=text_delta_only_fallback"
    )
    assert any(
        payload["signal"] == "message_persisted"
        and payload["official_response_source"] == "text_delta_only"
        and payload["official_response_basis"] == "text_delta_only_fallback"
        and payload["contribution_candidate_basis"] == "text_delta_only_fallback"
        for payload in telemetry_rows
    )


@pytest.mark.asyncio
async def test_invoke_publishes_typing_done_once_across_live_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _build_runner(tmp_path)
    event_queue = runner._event_bus.subscribe()

    session = RuntimeSession(
        thread_id="thread-123",
        agent_name="agent-1",
        provider="codex",
        transport_mode="live_process_transport",
        fallback_mode="resume_invocation_transport",
    )
    runner._session_supervisor.sessions[("thread-123", "agent-1")] = session
    runner._session_supervisor.locks[("thread-123", "agent-1")] = asyncio.Lock()

    adapter = _FakeLiveTransportAdapter(
        [
            SessionEvent(kind="turn_started", content="turn-1"),
            SessionEvent(kind="text_delta", content="live typing"),
        ]
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.build_transport_for_provider",
        lambda *args, **kwargs: _FakeLiveTransport(adapter),
    )
    monkeypatch.setattr(
        "btwin_core.agent_runner.AgentRunner._resolve_launch_resolution",
        lambda self, runtime_session: LaunchResolution(
            provider=_FakeStreamingProvider(),
            auth=ResolvedLaunchAuth(
                provider_name="codex",
                mode="cli_environment",
            ),
            env={},
            metadata={},
        ),
    )

    success_proc = _FakeSubprocess(
        [
            b"fallback typing\n",
        ],
        returncode=0,
    )

    async def fake_create_subprocess_exec(*args, **kwargs):  # noqa: ANN001, ANN202
        del args, kwargs
        return success_proc

    monkeypatch.setattr(
        "btwin_core.agent_runner.asyncio.create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = await runner.invoke("thread-123", "agent-1", "prompt text")

    events = _drain_events(event_queue)
    typing_events = [event for event in events if event.type == "agent_typing"]
    typing_done_events = [event for event in events if event.type == "agent_typing_done"]
    typing_indices = [index for index, event in enumerate(events) if event.type == "agent_typing"]
    typing_done_index = next(
        index for index, event in enumerate(events) if event.type == "agent_typing_done"
    )

    assert result.ok is True
    assert len(typing_events) >= 2
    assert len(typing_done_events) == 1
    assert typing_done_index > max(typing_indices)
    assert adapter.closed is True
