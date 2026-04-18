from btwin_core.protocol_store import Protocol, ProtocolGuardSet, ProtocolPhase, ProtocolSection
from btwin_core.workflow_constraints import evaluate_workflow_hook, validate_thread_close


def _protocol() -> Protocol:
    return Protocol(
        name="workflow-check",
        description="Protocol for workflow constraint tests",
        phases=[
            ProtocolPhase(
                name="implementation",
                actions=["contribute"],
                template=[
                    ProtocolSection(section="completed", required=True),
                ],
            )
        ],
    )


def _protocol_with_guard_set() -> Protocol:
    return Protocol(
        name="workflow-check-guards",
        description="Protocol with a declared guard set",
        guard_sets=[
            ProtocolGuardSet(
                name="review-default",
                guards=["contribution_required", "transition_precondition"],
            )
        ],
        phases=[
            ProtocolPhase(
                name="review",
                actions=["contribute"],
                template=[
                    ProtocolSection(section="completed", required=True),
                ],
                guard_set="review-default",
            ),
            ProtocolPhase(
                name="decision",
                actions=["decide"],
                decided_by="user",
            ),
        ],
    )


def test_baseline_stop_guard_still_applies_without_guard_set():
    thread = {
        "thread_id": "thread-123",
        "current_phase": "implementation",
        "phase_participants": ["alice"],
    }

    result = evaluate_workflow_hook(
        event="Stop",
        thread=thread,
        protocol=_protocol(),
        actor="alice",
        contributions=[],
    )

    assert result.event == "Stop"
    assert result.decision == "block"
    assert result.reason == "missing_contribution"
    assert result.required_result_recorded is False
    assert result.details["guard_source"] == "baseline"
    assert result.details["phase_guard_set"] is None
    assert result.details["declared_guards"] == []
    assert "baseline runtime guard remains always-on" in (result.overlay or "")
    assert "no protocol-declared guard set" in (result.overlay or "")
    assert "alice" in (result.overlay or "")


def test_stop_allows_when_current_actor_has_required_phase_contribution():
    thread = {
        "thread_id": "thread-123",
        "current_phase": "implementation",
        "phase_participants": ["alice"],
    }
    contributions = [
        {
            "agent": "alice",
            "phase": "implementation",
            "_content": "## completed\nImplemented the requested change.\n",
        }
    ]

    result = evaluate_workflow_hook(
        event="Stop",
        thread=thread,
        protocol=_protocol(),
        actor="alice",
        contributions=contributions,
    )

    assert result.event == "Stop"
    assert result.decision == "allow"
    assert result.reason is None
    assert result.required_result_recorded is True


def test_protocol_guard_set_does_not_disable_transition_precondition():
    protocol = _protocol_with_guard_set()
    thread = {
        "thread_id": "thread-456",
        "current_phase": "review",
        "participants": ["alice"],
        "phase_participants": ["alice"],
    }
    contributions = [
        {
            "agent": "alice",
            "phase": "review",
            "_content": "## completed\nImplemented the requested change.\n",
        }
    ]

    violation = validate_thread_close(thread=thread, protocol=protocol, contributions=contributions)

    assert violation is not None
    assert violation.error == "thread_not_closable_from_phase"
    assert violation.details["guard_source"] == "baseline"
    assert violation.details["phase_guard_set"] == "review-default"
    assert violation.details["declared_guards"] == [
        "contribution_required",
        "transition_precondition",
    ]
    assert "baseline runtime guard" in (violation.hint or "")


def test_stop_allows_when_actor_is_not_required_for_user_decision_phase():
    protocol = Protocol(
        name="decision-check",
        description="Protocol with user-only decision",
        phases=[
            ProtocolPhase(
                name="decision",
                actions=["decide"],
                decided_by="user",
                template=[ProtocolSection(section="agreed_points", required=True)],
            )
        ],
    )
    thread = {
        "thread_id": "thread-123",
        "current_phase": "decision",
        "phase_participants": ["alice", "bob"],
    }

    result = evaluate_workflow_hook(
        event="Stop",
        thread=thread,
        protocol=protocol,
        actor="alice",
        contributions=[],
    )

    assert result.event == "Stop"
    assert result.decision == "allow"
    assert result.required_result_recorded is False
