from pathlib import Path

import btwin_core.runtime_binding_store as runtime_binding_store
from btwin_core.runtime_binding_store import RuntimeBinding, RuntimeBindingStore


def test_close_binding_marks_record_closed_without_deleting_file(tmp_path, monkeypatch):
    data_dir = tmp_path / ".btwin"
    store = RuntimeBindingStore(data_dir)
    timestamps = iter([
        "2026-04-15T00:00:00+00:00",
        "2026-04-15T00:05:00+00:00",
    ])
    monkeypatch.setattr(runtime_binding_store, "_now_iso", lambda: next(timestamps))

    binding = store.bind("thread-123", "alice")
    closed = store.close_binding(binding, reason="stale_last_seen")

    assert closed.status == "closed"
    assert closed.closed_at == "2026-04-15T00:05:00+00:00"
    assert closed.closed_reason == "stale_last_seen"
    assert store.file_path.exists()
    assert store.read_state().bound is False


def test_cleanup_stale_active_binding_closes_only_stale_records(tmp_path, monkeypatch):
    data_dir = tmp_path / ".btwin"
    store = RuntimeBindingStore(data_dir)
    store.write(
        RuntimeBinding(
            thread_id="thread-123",
            agent_name="alice",
            bound_at="2026-04-15T00:00:00+00:00",
            status="active",
            opened_at="2026-04-15T00:00:00+00:00",
            last_seen_at="2026-04-15T00:00:00+00:00",
            closed_at=None,
            closed_reason=None,
        )
    )
    monkeypatch.setattr(runtime_binding_store, "_now_iso", lambda: "2026-04-15T00:30:00+00:00")

    cleaned = store.cleanup_stale_active_binding(max_age_seconds=600)

    assert cleaned is not None
    assert cleaned.status == "closed"
    assert cleaned.closed_at == "2026-04-15T00:30:00+00:00"
    assert cleaned.closed_reason == "stale_last_seen"

    state = store.read_state()
    assert state.bound is False
    assert state.binding is not None
    assert state.binding.status == "closed"
    assert state.binding.closed_reason == "stale_last_seen"
