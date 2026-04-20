from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.protocol_store import Protocol, ProtocolOutcomePolicy, ProtocolPhase, ProtocolSection

from btwin_core.delegation_engine import build_delegation_assignment


def _review_protocol() -> Protocol:
    return Protocol(
        name="review-loop",
        outcome_policies=[
            ProtocolOutcomePolicy(
                name="review-outcomes",
                emitters=["reviewer"],
                actions=["decide"],
                outcomes=["retry", "accept"],
            )
        ],
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[{"role": "reviewer", "action": "review"}],
                outcome_policy="review-outcomes",
            )
        ],
        outcomes=["retry", "accept"],
    )


def _review_thread() -> dict[str, object]:
    return {
        "thread_id": "thread-1",
        "protocol": "review-loop",
        "current_phase": "review",
        "phase_participants": ["alice"],
    }


def _review_cycle_state() -> PhaseCycleState:
    return PhaseCycleState.start(
        thread_id="thread-1",
        phase_name="review",
        procedure_steps=["review"],
    )


def test_build_delegation_assignment_uses_compiled_phase_and_role():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
    )

    assert assignment.status == "running"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "alice"
    assert assignment.required_action == "submit_contribution"
    assert assignment.expected_output == "review contribution"


def test_build_delegation_assignment_blocks_when_role_binding_missing():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={},
    )

    assert assignment.status == "blocked"
    assert assignment.target_role == "reviewer"
    assert assignment.required_action == "submit_contribution"
    assert assignment.reason_blocked == "missing_role_binding"


def test_build_delegation_assignment_waits_for_human_when_outcome_is_required():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        contributions=[
            {
                "agent": "alice",
                "phase": "review",
                "created_at": "2026-04-20T00:00:00+00:00",
                "_content": "## completed\nReady for a decision.\n",
            }
        ],
    )

    assert assignment.status == "waiting_for_human"
    assert assignment.required_action == "record_outcome"
    assert assignment.expected_output is not None
    assert "retry" in assignment.expected_output
    assert "accept" in assignment.expected_output


def test_build_delegation_assignment_marks_completed_when_no_next_work_remains():
    protocol = Protocol(
        name="single-pass",
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                template=[ProtocolSection(section="completed", required=True)],
                procedure=[{"role": "reviewer", "action": "review"}],
            )
        ],
    )

    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=protocol,
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        contributions=[
            {
                "agent": "alice",
                "phase": "review",
                "created_at": "2026-04-20T00:00:00+00:00",
                "_content": "## completed\nLooks good.\n",
            }
        ],
    )

    assert assignment.status == "completed"
    assert assignment.required_action == "close_thread"


def test_build_delegation_assignment_blocks_when_runtime_recovery_has_failed():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        runtime_session={
            "degraded": True,
            "recoverable": False,
            "recovery_pending": False,
        },
    )

    assert assignment.status == "blocked"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "alice"
    assert assignment.reason_blocked == "failed_recovery"
    assert assignment.stop_reason == "failed_recovery"


def test_build_delegation_assignment_fails_when_loop_iteration_exceeds_cap():
    assignment = build_delegation_assignment(
        thread=_review_thread(),
        protocol=_review_protocol(),
        phase_cycle_state=_review_cycle_state(),
        role_bindings={"reviewer": "alice"},
        loop_iteration=2,
        max_auto_iterations=1,
    )

    assert assignment.status == "failed"
    assert assignment.target_role == "reviewer"
    assert assignment.resolved_agent == "alice"
    assert assignment.required_action == "submit_contribution"
    assert assignment.stop_reason == "max_auto_iterations_reached"
