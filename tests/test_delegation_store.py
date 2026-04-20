from btwin_core.delegation_state import DelegationState
from btwin_core.delegation_store import DelegationStore


def test_delegation_store_persists_thread_state(tmp_path):
    store = DelegationStore(tmp_path)
    state = DelegationState(
        thread_id="thread-1",
        status="running",
        loop_iteration=1,
        current_phase="review",
        current_cycle_index=2,
        target_role="reviewer",
        resolved_agent="alice",
        required_action="submit_contribution",
        expected_output="review contribution",
    )

    store.write(state)
    loaded = store.read("thread-1")

    assert loaded is not None
    assert loaded.thread_id == "thread-1"
    assert loaded.status == "running"
    assert loaded.target_role == "reviewer"


def test_delegation_store_lists_newest_first(tmp_path):
    store = DelegationStore(tmp_path)
    store.write(DelegationState(thread_id="a", status="idle"))
    store.write(DelegationState(thread_id="b", status="waiting_for_human"))

    items = store.list_states()

    assert [item.thread_id for item in items] == ["b", "a"]


def test_delegation_store_deletes_thread_state(tmp_path):
    store = DelegationStore(tmp_path)
    store.write(DelegationState(thread_id="a", status="idle"))
    store.write(DelegationState(thread_id="b", status="running"))

    deleted = store.delete("a")

    assert deleted is True
    assert store.read("a") is None
    assert [item.thread_id for item in store.list_states()] == ["b"]


def test_delegation_store_delete_persists_across_reopen(tmp_path):
    store = DelegationStore(tmp_path)
    store.write(DelegationState(thread_id="a", status="idle"))

    assert store.delete("a") is True

    reopened = DelegationStore(tmp_path)
    assert reopened.read("a") is None
    assert reopened.list_states() == []


def test_delegation_store_allows_rewrite_after_delete(tmp_path):
    store = DelegationStore(tmp_path)
    store.write(DelegationState(thread_id="a", status="idle"))
    assert store.delete("a") is True

    updated = DelegationState(thread_id="a", status="running", loop_iteration=1)
    store.write(updated)

    reopened = DelegationStore(tmp_path)
    loaded = reopened.read("a")

    assert loaded is not None
    assert loaded.status == "running"
    assert loaded.loop_iteration == 1
