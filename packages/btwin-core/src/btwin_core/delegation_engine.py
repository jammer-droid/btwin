"""Normalize compiled protocol and phase-cycle state into delegation decisions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from btwin_core.delegation_state import DelegationStatus
from btwin_core.phase_cycle import PhaseCycleState
from btwin_core.phase_cycle_engine import build_phase_cycle_context_core, resolve_phase_cycle_current_step
from btwin_core.protocol_flow import ProtocolNextPlan, describe_next, resolve_protocol_phase
from btwin_core.protocol_store import Protocol, ensure_protocol_compiled


class DelegationAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: DelegationStatus
    next_phase: str | None = None
    target_role: str | None = None
    resolved_agent: str | None = None
    required_action: str | None = None
    expected_output: str | None = None
    reason_blocked: str | None = None
    stop_reason: str | None = None


def build_delegation_assignment(
    *,
    thread: dict[str, object],
    protocol: Protocol,
    phase_cycle_state: PhaseCycleState,
    role_bindings: dict[str, str] | None = None,
    contributions: list[dict[str, object]] | None = None,
) -> DelegationAssignment:
    compiled_protocol = ensure_protocol_compiled(protocol)
    current_phase = _current_phase_name(thread, phase_cycle_state)
    phase = resolve_protocol_phase(compiled_protocol, current_phase)
    if phase is None:
        return DelegationAssignment(
            status="blocked",
            required_action="record_outcome",
            reason_blocked="phase_not_found",
            stop_reason="phase_not_found",
        )

    thread_snapshot = dict(thread)
    thread_snapshot["current_phase"] = phase.name

    if phase_cycle_state.status == "blocked":
        return DelegationAssignment(
            status="blocked",
            required_action="submit_contribution",
            expected_output=_fallback_expected_output(phase.name),
            reason_blocked="phase_cycle_blocked",
            stop_reason="phase_cycle_blocked",
        )

    next_plan = describe_next(
        thread_snapshot,
        compiled_protocol,
        list(contributions or []),
    )
    if next_plan.manual_outcome_required:
        return DelegationAssignment(
            status="waiting_for_human",
            next_phase=next_plan.next_phase,
            required_action=next_plan.suggested_action,
            expected_output=_manual_outcome_output(phase, next_plan),
            stop_reason="human_outcome_required",
        )
    if phase_cycle_state.status == "completed" or next_plan.suggested_action in {"advance_phase", "close_thread"}:
        return DelegationAssignment(
            status="completed",
            next_phase=next_plan.next_phase,
            required_action=next_plan.suggested_action,
            stop_reason=next_plan.suggested_action,
        )

    current_step = resolve_phase_cycle_current_step(phase, phase_cycle_state)
    target_role = current_step.role if current_step is not None else None
    context_core = build_phase_cycle_context_core(
        thread=thread_snapshot,
        protocol=compiled_protocol,
        phase=phase,
        state=phase_cycle_state,
    )
    expected_output = _expected_output(
        phase_name=phase.name,
        next_plan=next_plan,
        step_action=current_step.action if current_step is not None else None,
        fallback=context_core.required_result,
    )
    if not target_role:
        return DelegationAssignment(
            status="blocked",
            next_phase=next_plan.next_phase,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            reason_blocked="missing_target_role",
            stop_reason="missing_target_role",
        )

    resolved_agent = (role_bindings or {}).get(target_role)
    if not resolved_agent:
        return DelegationAssignment(
            status="blocked",
            next_phase=next_plan.next_phase,
            target_role=target_role,
            required_action=next_plan.suggested_action,
            expected_output=expected_output,
            reason_blocked="missing_role_binding",
            stop_reason="missing_role_binding",
        )

    return DelegationAssignment(
        status="running",
        next_phase=next_plan.next_phase,
        target_role=target_role,
        resolved_agent=resolved_agent,
        required_action=next_plan.suggested_action,
        expected_output=expected_output,
    )


def build_delegate_role_bindings(
    thread: dict[str, object],
    phase,
) -> dict[str, str]:
    participants = thread.get("phase_participants", [])
    if not isinstance(participants, list):
        participants = []
    if not phase.procedure:
        return {}

    bindings: dict[str, str] = {}
    for step, participant in zip(phase.procedure, participants):
        if isinstance(step.role, str) and step.role and isinstance(participant, str) and participant:
            bindings[step.role] = participant
    return bindings


def default_phase_participants(
    thread: dict[str, object],
    phase,
) -> list[str]:
    phase_participants = thread.get("phase_participants", [])
    if isinstance(phase_participants, list) and phase_participants:
        return [name for name in phase_participants if isinstance(name, str) and name][: len(phase.procedure or [])]

    participants = thread.get("participants", [])
    names: list[str] = []
    for participant in participants:
        if isinstance(participant, dict):
            name = participant.get("name")
            if isinstance(name, str) and name:
                names.append(name)
            continue
        if isinstance(participant, str) and participant:
            names.append(participant)
    if not phase.procedure:
        return names
    return names[: len(phase.procedure)]


def _current_phase_name(thread: dict[str, object], phase_cycle_state: PhaseCycleState) -> str | None:
    phase_name = thread.get("current_phase")
    if isinstance(phase_name, str) and phase_name:
        return phase_name
    return phase_cycle_state.phase_name


def _expected_output(
    *,
    phase_name: str,
    next_plan: ProtocolNextPlan,
    step_action: str | None,
    fallback: str | None,
) -> str | None:
    if next_plan.suggested_action == "submit_contribution":
        label = step_action or phase_name
        return f"{label} contribution"
    return fallback or _fallback_expected_output(phase_name)


def _manual_outcome_output(phase, next_plan: ProtocolNextPlan) -> str:
    outcomes = phase.policy_outcomes or next_plan.valid_outcomes
    if outcomes:
        return f"record outcome: {', '.join(outcomes)}"
    return "record outcome"


def _fallback_expected_output(phase_name: str) -> str:
    return f"{phase_name} contribution"
