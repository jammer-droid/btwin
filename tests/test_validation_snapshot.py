from __future__ import annotations

from btwin_core.validation_snapshot import build_validation_snapshot


def test_build_validation_snapshot_includes_flow_and_evidence_context() -> None:
    snapshot = build_validation_snapshot(
        thread={
            "thread_id": "thread-1",
            "topic": "Design Review",
            "protocol": "code-review",
            "current_phase": "analysis",
        },
        phase_cycle_payload={
            "state": {"cycle_index": 1, "current_step_label": "collect-feedback"},
            "visual": {
                "gates": [
                    {"label": "Retry Gate", "status": "active"},
                    {"label": "Accept Gate", "status": "pending"},
                ]
            },
        },
        validation={
            "verdict": "WARN",
            "reasons": ["alice missing findings"],
            "checks": [
                ("protocol_match", "PASS"),
                ("required_contribution", "WARN"),
            ],
            "next_expected_action": "submit_contribution",
        },
        validation_cases=[
            "happy_path_accept: ready",
            "missing_contribution_blocked: blocked by missing contribution",
            "close_requires_summary: not triggered",
        ],
        trace_rows=[{"kind": "result", "summary": "alice analysis submitted"}],
        runtime_sessions={"alice": {"status": "active"}},
        telemetry_rows=[
            {
                "event_type": "validation.signal.recorded",
                "payload": {
                    "signal": "runtime_output_persisted",
                    "official_response_source": "agent_message_completed",
                    "official_response_basis": "final_answer_agent_message_completed",
                    "contribution_candidate_basis": "final_answer_agent_message_completed",
                },
            },
            {
                "event_type": "validation.signal.recorded",
                "evidence_level": "critical",
                "payload": {"signal": "runtime_output_persisted"},
            },
        ],
        protocol_plan={"passed": False, "missing": [{"agent": "alice", "missing_sections": ["findings"]}]},
        phase_progression="• Analysis - Discussion - Decision",
        procedure_progression="Announce - • Collect Feedback - Resolve",
    )

    assert snapshot["phase_progression"] == "• Analysis - Discussion - Decision"
    assert snapshot["procedure_progression"] == "Announce - • Collect Feedback - Resolve"
    assert snapshot["gate_progression"] == "• Retry Gate - Accept Gate"
    assert snapshot["relevant_case_progression"] == "Missing contribution blocked [WARN]"
    assert snapshot["confidence"] == "high"
    assert snapshot["evidence_summary"] == [
        "workflow trace present",
        "runtime sessions 1 tracked",
        "telemetry signals 2 recent",
        "protocol gaps 1 participant",
    ]
    assert snapshot["official_response_promotion"] == (
        "promoted from agent_message_completed via "
        "final_answer_agent_message_completed; candidate basis "
        "final_answer_agent_message_completed"
    )


def test_build_validation_snapshot_drops_to_low_confidence_without_trace_or_telemetry() -> None:
    snapshot = build_validation_snapshot(
        thread={
            "thread_id": "thread-1",
            "topic": "Design Review",
            "protocol": "code-review",
            "current_phase": "analysis",
        },
        phase_cycle_payload=None,
        validation={
            "verdict": "WARN",
            "reasons": ["no recent workflow trace"],
            "checks": [("trajectory_match", "WARN")],
            "next_expected_action": "inspect_live_trace",
        },
        validation_cases=["happy_path_accept: not evaluated in current state"],
        trace_rows=[],
        runtime_sessions={},
        telemetry_rows=[
            {
                "event_type": "validation.signal.recorded",
                "payload": {"signal": "message_persisted", "contribution_candidate_basis": "commentary_agent_message_completed"},
            }
        ],
        protocol_plan=None,
        phase_progression=None,
        procedure_progression=None,
    )

    assert snapshot["confidence"] == "low"
    assert snapshot["evidence_summary"] == [
        "workflow trace missing",
        "runtime sessions unavailable",
        "telemetry signals 1 recent",
    ]
    assert snapshot["relevant_case_progression"] == "Happy path accept [SKIP]"
    assert snapshot["official_response_promotion"] == (
        "no official-response promotion evidence; latest candidate basis "
        "commentary_agent_message_completed"
    )
