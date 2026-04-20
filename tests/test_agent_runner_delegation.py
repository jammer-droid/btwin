from pathlib import Path

from btwin_core.agent_runner import AgentRunner, InvocationResult, RuntimeOutput
from btwin_core.agent_store import AgentStore
from btwin_core.config import BTwinConfig
from btwin_core.delegation_state import DelegationState
from btwin_core.delegation_store import DelegationStore
from btwin_core.event_bus import EventBus
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_store import PhaseCycleStore
from btwin_core.protocol_store import ProtocolStore, compile_protocol_definition
from btwin_core.thread_store import ThreadStore


def _build_runner(data_dir: Path) -> tuple[AgentRunner, ThreadStore, ProtocolStore, DelegationStore, PhaseCycleStore]:
    thread_store = ThreadStore(data_dir / "threads")
    protocol_store = ProtocolStore(data_dir / "protocols")
    runner = AgentRunner(
        thread_store,
        protocol_store,
        AgentStore(data_dir),
        EventBus(),
        config=BTwinConfig(data_dir=data_dir),
    )
    return (
        runner,
        thread_store,
        protocol_store,
        DelegationStore(data_dir),
        PhaseCycleStore(data_dir),
    )


def _seed_running_delegation(
    delegation_store: DelegationStore,
    *,
    thread_id: str,
    resolved_agent: str = "alice",
    current_phase: str = "review",
) -> None:
    delegation_store.write(
        DelegationState(
            thread_id=thread_id,
            status="running",
            loop_iteration=1,
            current_phase=current_phase,
            current_cycle_index=1,
            target_role="reviewer",
            resolved_agent=resolved_agent,
            required_action="submit_contribution",
            expected_output=f"{current_phase} contribution",
        )
    )


def test_helper_result_advances_delegation_and_dispatches_next_work(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-followup",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                    },
                    {
                        "name": "followup",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Follow Up"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate followup thread",
        protocol="delegate-followup",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    _seed_running_delegation(delegation_store, thread_id=thread["thread_id"])

    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(
            ok=True,
            outputs=(
                RuntimeOutput(
                    content="## completed\nReview is complete.\n",
                    phase="final_answer",
                    state_affecting=True,
                ),
            ),
        ),
        chain_depth=1,
    )

    state = delegation_store.read(thread["thread_id"])
    assert state is not None
    assert state.status == "running"
    assert state.current_phase == "followup"
    assert state.loop_iteration == 2
    assert state.resolved_agent == "alice"

    updated_thread = thread_store.get_thread(thread["thread_id"])
    assert updated_thread is not None
    assert updated_thread["current_phase"] == "followup"

    review_contributions = thread_store.list_contributions(thread["thread_id"], phase="review")
    assert len(review_contributions) == 1
    assert review_contributions[0]["agent"] == "alice"
    assert review_contributions[0]["_content"] == "## completed\nReview is complete."

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert len(delegation_messages) == 1
    assert delegation_messages[0]["from"] == "btwin"
    assert delegation_messages[0]["target_agents"] == ["alice"]
    assert delegation_messages[0]["message_phase"] == "followup"


def test_helper_result_waits_for_human_when_outcome_is_ambiguous(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-outcome",
                "outcome_policies": [
                    {
                        "name": "review-outcomes",
                        "emitters": ["reviewer"],
                        "actions": ["decide"],
                        "outcomes": ["retry", "accept"],
                    }
                ],
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                        "outcome_policy": "review-outcomes",
                    }
                ],
                "outcomes": ["retry", "accept"],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Delegate outcome thread",
        protocol="delegate-outcome",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    _seed_running_delegation(delegation_store, thread_id=thread["thread_id"])

    runner._persist_invocation_outputs(
        thread["thread_id"],
        "alice",
        InvocationResult(
            ok=True,
            outputs=(
                RuntimeOutput(
                    content="## completed\nNeeds another pass.\n",
                    phase="final_answer",
                    state_affecting=True,
                ),
            ),
        ),
        chain_depth=1,
    )

    state = delegation_store.read(thread["thread_id"])
    assert state is not None
    assert state.status == "waiting_for_human"
    assert state.current_phase == "review"
    assert state.loop_iteration == 1
    assert state.required_action == "record_outcome"
    assert state.expected_output is not None
    assert "retry" in state.expected_output
    assert "accept" in state.expected_output

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert delegation_messages == []


def test_duplicate_result_message_id_is_not_reprocessed(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    runner, thread_store, protocol_store, delegation_store, phase_cycle_store = _build_runner(data_dir)

    protocol_store.save_protocol(
        compile_protocol_definition(
            {
                "name": "delegate-followup",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Review"}],
                    },
                    {
                        "name": "followup",
                        "actions": ["review"],
                        "template": [{"section": "completed", "required": True}],
                        "procedure": [{"role": "reviewer", "action": "review", "alias": "Follow Up"}],
                    },
                ],
            }
        )
    )
    thread = thread_store.create_thread(
        topic="Duplicate result thread",
        protocol="delegate-followup",
        participants=["alice"],
        initial_phase="review",
    )
    phase_cycle_store.write(
        PhaseCycleState.start(
            thread_id=thread["thread_id"],
            phase_name="review",
            procedure_steps=["review"],
        )
    )
    _seed_running_delegation(delegation_store, thread_id=thread["thread_id"])

    saved_message = runner._save_agent_message(
        thread["thread_id"],
        "alice",
        "## completed\nReview is complete.\n",
        1,
        message_phase="final_answer",
        state_affecting=True,
    )

    runner._maybe_continue_delegation_from_saved_message(
        thread["thread_id"],
        "alice",
        saved_message,
    )
    runner._maybe_continue_delegation_from_saved_message(
        thread["thread_id"],
        "alice",
        saved_message,
    )

    delegation_messages = [
        message
        for message in thread_store.list_messages(thread["thread_id"])
        if message.get("msg_type") == "delegation"
    ]
    assert len(delegation_messages) == 1

    review_contributions = thread_store.list_contributions(thread["thread_id"], phase="review")
    assert len(review_contributions) == 1
