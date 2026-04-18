"""Workflow constraint evaluation and Codex hook output helpers."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from btwin_core.protocol_flow import ProtocolNextPlan, describe_next
from btwin_core.protocol_store import Protocol
from btwin_core.protocol_validator import ProtocolValidator


WorkflowHookEvent = Literal["SessionStart", "UserPromptSubmit", "Stop"]
WorkflowHookDecision = Literal["allow", "block", "noop"]


class WorkflowHookResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: WorkflowHookEvent
    decision: WorkflowHookDecision
    reason: str | None = None
    overlay: str | None = None
    required_result_recorded: bool = False
    details: dict[str, object] = Field(default_factory=dict)


class CodexHookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    session_id: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    hook_event_name: str
    model: str | None = None
    turn_id: str | None = None
    source: str | None = None
    prompt: str | None = None
    stop_hook_active: bool | None = None
    last_assistant_message: str | None = None

    @classmethod
    def from_text(cls, text: str) -> "CodexHookPayload | None":
        stripped = text.strip()
        if not stripped:
            return None
        try:
            return cls.model_validate(json.loads(stripped))
        except (json.JSONDecodeError, ValueError, TypeError):
            return None


class WorkflowConstraintViolation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: str
    message: str
    hint: str | None = None
    details: dict[str, object] = Field(default_factory=dict)


def _thread_id(thread: dict) -> str:
    value = thread.get("thread_id")
    return value if isinstance(value, str) else ""


def _phase_name(thread: dict) -> str | None:
    current_phase = thread.get("current_phase")
    return current_phase if isinstance(current_phase, str) and current_phase else None


def _phase_definition(protocol: Protocol, phase_name: str | None):
    if not phase_name:
        return None
    return next((item for item in protocol.phases if item.name == phase_name), None)


def _thread_participants(thread: dict) -> list[str]:
    participants = thread.get("participants", [])
    if isinstance(participants, list):
        values = [
            participant.get("name")
            for participant in participants
            if isinstance(participant, dict) and isinstance(participant.get("name"), str)
        ]
        if values:
            return values
        return [str(participant) for participant in participants if isinstance(participant, str)]
    return []


def _phase_participants(thread: dict) -> list[str]:
    phase_participants = thread.get("phase_participants", [])
    if isinstance(phase_participants, list):
        values = [str(name) for name in phase_participants if isinstance(name, str)]
        if values:
            return values
    return _thread_participants(thread)


def _artifact_actors(thread: dict, protocol: Protocol, phase_name: str | None) -> list[str]:
    phase = _phase_definition(protocol, phase_name)
    if phase is None:
        return []
    if phase.decided_by == "user":
        return ["user"]
    return _phase_participants(thread)


def _resolve_phase_guard_context(protocol: Protocol, phase_name: str | None) -> dict[str, object]:
    phase = _phase_definition(protocol, phase_name)
    if phase is None:
        phase_guard_set = None
        declared_guards: list[str] = []
    else:
        declared_guard_set = protocol.get_guard_set(phase.guard_set)
        if declared_guard_set is None:
            phase_guard_set = phase.guard_set
            declared_guards = []
        else:
            phase_guard_set = declared_guard_set.name
            declared_guards = list(declared_guard_set.guards)
    return {
        "guard_source": "baseline",
        "phase_guard_set": phase_guard_set,
        "declared_guards": declared_guards,
        "baseline_guards": [
            "contribution_required",
            "phase_actor_eligibility",
            "direct_target_eligibility",
            "transition_precondition",
        ],
    }


def _guard_note(*, phase_guard_set: str | None, declared_guards: list[str]) -> str:
    if declared_guards:
        return "baseline runtime guard remains always-on; protocol-declared guards are additive in v1."
    if phase_guard_set:
        return "baseline runtime guard remains always-on; this phase does not declare additional protocol guards."
    return "baseline runtime guard remains always-on; no protocol-declared guard set is referenced for this phase."


def _render_contribution_hint(
    *,
    thread_id: str,
    phase_name: str | None,
    agent_name: str | None,
) -> str:
    phase_value = phase_name or "<phase>"
    agent_value = agent_name or "<agent>"
    return (
        f"Try `btwin contribution submit --thread {thread_id} --agent {agent_value} "
        f"--phase {phase_value}` with the required sections."
    )


def build_protocol_plan_hint(thread_id: str, plan: ProtocolNextPlan) -> str | None:
    if not plan.passed:
        agent_name = None
        if plan.missing and isinstance(plan.missing[0], dict):
            agent = plan.missing[0].get("agent")
            if isinstance(agent, str) and agent:
                agent_name = agent
        return _append_guard_note(
            _render_contribution_hint(
                thread_id=thread_id,
                phase_name=plan.current_phase,
                agent_name=agent_name,
            ),
            plan=plan,
        )

    if plan.error == "unsupported_outcome" and plan.valid_outcomes:
        options = " | ".join(plan.valid_outcomes)
        return _append_guard_note(
            (
                f"Re-run `btwin protocol apply-next --thread {thread_id} --outcome <{options}>` "
                "with one of the valid outcomes."
            ),
            plan=plan,
        )

    if plan.suggested_action == "record_outcome" and plan.valid_outcomes:
        options = " | ".join(plan.valid_outcomes)
        return _append_guard_note(
            (
                f"Choose an outcome and re-run `btwin protocol apply-next --thread {thread_id} "
                f"--outcome <{options}>`."
            ),
            plan=plan,
        )

    if plan.suggested_action == "advance_phase":
        suffix = f" to move into `{plan.next_phase}`" if plan.next_phase else ""
        return _append_guard_note(
            f"Try `btwin protocol apply-next --thread {thread_id}`{suffix}.",
            plan=plan,
        )

    if plan.suggested_action == "close_thread":
        return _append_guard_note(
            f"Try `btwin thread close --thread {thread_id} --summary \"...\"`.",
            plan=plan,
        )

    return None


def _append_guard_note(hint: str | None, *, plan: ProtocolNextPlan) -> str | None:
    if not hint:
        return hint
    note = _guard_note(phase_guard_set=plan.guard_set, declared_guards=plan.declared_guards)
    return f"{hint} {note}"


def _hook_overlay_with_guard_context(
    *,
    overlay: str,
    protocol: Protocol,
    phase_name: str | None,
) -> str:
    context = _resolve_phase_guard_context(protocol, phase_name)
    return f"{overlay} {_guard_note(phase_guard_set=context['phase_guard_set'], declared_guards=context['declared_guards'])}"


def validate_contribution_submission(
    *,
    thread: dict,
    protocol: Protocol,
    actor: str,
    phase_name: str,
) -> WorkflowConstraintViolation | None:
    thread_id = _thread_id(thread)
    current_phase = _phase_name(thread)
    if not current_phase:
        return WorkflowConstraintViolation(
            error="phase_not_found",
            message="thread does not have an active phase",
            hint=f"Check `btwin thread show {thread_id}` and `btwin protocol next --thread {thread_id}`.",
        )

    if phase_name != current_phase:
        return WorkflowConstraintViolation(
            error="phase_mismatch",
            message=f"current phase is `{current_phase}`, not `{phase_name}`",
            hint=_render_contribution_hint(thread_id=thread_id, phase_name=current_phase, agent_name=actor),
            details={"current_phase": current_phase, "requested_phase": phase_name},
        )

    phase = _phase_definition(protocol, current_phase)
    if phase is None:
        return WorkflowConstraintViolation(
            error="phase_not_found",
            message=f"phase `{current_phase}` is not defined in protocol `{protocol.name}`",
            hint=f"Inspect the protocol with `btwin protocol show {protocol.name}`.",
        )

    if "contribute" not in phase.actions and "decide" not in phase.actions:
        return WorkflowConstraintViolation(
            error="phase_action_not_allowed",
            message=f"phase `{current_phase}` does not accept structured contributions",
            hint=f"Use `btwin thread send-message --thread {thread_id} ...` or `btwin protocol apply-next --thread {thread_id}` instead.",
            details={"current_phase": current_phase, "phase_actions": list(phase.actions)},
        )

    allowed_actors = _artifact_actors(thread, protocol, current_phase)
    if actor not in allowed_actors:
        if phase.decided_by == "user":
            hint = _render_contribution_hint(thread_id=thread_id, phase_name=current_phase, agent_name="user")
        else:
            participants = ", ".join(allowed_actors) if allowed_actors else "none"
            hint = f"Eligible phase participants for `{current_phase}`: {participants}."
        return WorkflowConstraintViolation(
            error="actor_not_allowed_for_phase",
            message=f"`{actor}` is not allowed to submit the `{current_phase}` result",
            hint=hint,
            details={"current_phase": current_phase, "actor": actor, "allowed_actors": allowed_actors},
        )

    return None


def validate_direct_message_targets(
    *,
    thread: dict,
    protocol: Protocol,
    from_agent: str,
    target_agents: list[str],
) -> WorkflowConstraintViolation | None:
    thread_id = _thread_id(thread)
    current_phase = _phase_name(thread)
    phase = _phase_definition(protocol, current_phase)
    if current_phase is None or phase is None:
        return WorkflowConstraintViolation(
            error="phase_not_found",
            message="current phase is unavailable",
            hint=f"Inspect the thread with `btwin thread show {thread_id}`.",
        )

    if "discuss" not in phase.actions and "review" not in phase.actions:
        return WorkflowConstraintViolation(
            error="direct_message_not_allowed_in_phase",
            message=f"phase `{current_phase}` does not allow direct chat routing",
            hint=(
                _render_contribution_hint(thread_id=thread_id, phase_name=current_phase, agent_name=from_agent)
                if ("contribute" in phase.actions or "decide" in phase.actions)
                else f"Try `btwin protocol apply-next --thread {thread_id}`."
            ),
            details={"current_phase": current_phase, "phase_actions": list(phase.actions)},
        )

    eligible_targets = [name for name in _phase_participants(thread) if name != from_agent]
    invalid_targets = [target for target in target_agents if target not in eligible_targets]
    if invalid_targets:
        participants = ", ".join(eligible_targets) if eligible_targets else "none"
        return WorkflowConstraintViolation(
            error="target_not_eligible_for_phase",
            message=f"direct target is not eligible in phase `{current_phase}`",
            hint=f"Eligible direct targets in `{current_phase}`: {participants}.",
            details={
                "current_phase": current_phase,
                "invalid_targets": invalid_targets,
                "eligible_targets": eligible_targets,
            },
        )

    return None


def validate_thread_close(
    *,
    thread: dict,
    protocol: Protocol,
    contributions: list[dict],
) -> WorkflowConstraintViolation | None:
    thread_id = _thread_id(thread)
    phase_name = _phase_name(thread)
    plan = describe_next(thread, protocol, contributions)
    hint = build_protocol_plan_hint(thread_id, plan)
    guard_details = _resolve_phase_guard_context(protocol, phase_name)

    if plan.error:
        return WorkflowConstraintViolation(
            error=plan.error,
            message=f"thread cannot be closed from phase `{plan.current_phase}`",
            hint=hint,
            details=guard_details,
        )

    if not plan.passed:
        return WorkflowConstraintViolation(
            error="phase_requirements_not_met",
            message=f"current phase `{plan.current_phase}` still has missing required contributions",
            hint=hint,
            details={"missing": plan.missing, "current_phase": plan.current_phase, **guard_details},
        )

    if plan.suggested_action != "close_thread":
        return WorkflowConstraintViolation(
            error="thread_not_closable_from_phase",
            message=f"current phase `{plan.current_phase}` must transition before the thread can close",
            hint=hint,
            details={
                "current_phase": plan.current_phase,
                "suggested_action": plan.suggested_action,
                "next_phase": plan.next_phase,
                "valid_outcomes": plan.valid_outcomes,
                **guard_details,
            },
        )

    return None


def _required_sections(protocol: Protocol, phase_name: str | None) -> list[str]:
    if not phase_name:
        return []
    phase = next((item for item in protocol.phases if item.name == phase_name), None)
    if phase is None:
        return []
    return [section.section for section in phase.template if section.required]


def _actor_contribution_matches(
    *,
    actor: str | None,
    phase_name: str | None,
    required_sections: list[str],
    contributions: list[dict],
) -> bool:
    if not actor or not phase_name:
        return False

    for contribution in contributions:
        if contribution.get("agent") != actor:
            continue
        if contribution.get("phase") != phase_name:
            continue
        content = contribution.get("_content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        if not required_sections:
            return True
        validation = ProtocolValidator.validate_contribution(content, required_sections)
        if validation.valid:
            return True
    return False


def evaluate_workflow_hook(
    *,
    event: WorkflowHookEvent,
    thread: dict,
    protocol: Protocol,
    actor: str | None,
    contributions: list[dict],
) -> WorkflowHookResult:
    """Evaluate the minimal workflow constraint contract for one hook event."""
    phase_name = _phase_name(thread)
    phase = _phase_definition(protocol, phase_name)
    required_sections = _required_sections(protocol, phase_name)

    if event == "SessionStart":
        return WorkflowHookResult(
            event=event,
            decision="noop",
            overlay=f"Resume thread {thread.get('thread_id')} in phase {phase_name or 'unknown'}.",
        )

    if event == "UserPromptSubmit":
        return WorkflowHookResult(
            event=event,
            decision="noop",
            overlay=f"Current phase: {phase_name or 'unknown'}. Required result type: contribution.",
        )

    if phase is None or not phase.template:
        return WorkflowHookResult(event=event, decision="allow")

    if "contribute" not in phase.actions and "decide" not in phase.actions:
        return WorkflowHookResult(event=event, decision="allow")

    allowed_actors = _artifact_actors(thread, protocol, phase_name)
    if actor and actor not in allowed_actors:
        return WorkflowHookResult(event=event, decision="allow")

    required_result_recorded = _actor_contribution_matches(
        actor=actor,
        phase_name=phase_name,
        required_sections=required_sections,
        contributions=contributions,
    )
    if required_result_recorded:
        return WorkflowHookResult(
            event=event,
            decision="allow",
            required_result_recorded=True,
        )

    guard_details = _resolve_phase_guard_context(protocol, phase_name)
    return WorkflowHookResult(
        event=event,
        decision="block",
        reason="missing_contribution",
        overlay=_hook_overlay_with_guard_context(
            overlay=(
                f"Current phase {phase_name or 'unknown'} still needs a contribution "
                f"from {actor or 'the current actor'} before stopping."
            ),
            protocol=protocol,
            phase_name=phase_name,
        ),
        required_result_recorded=False,
        details=guard_details,
    )


def build_codex_hook_response(
    payload: CodexHookPayload,
    result: WorkflowHookResult,
) -> dict[str, object] | None:
    """Render a WorkflowHookResult into the Codex hook stdout JSON shape."""
    if payload.hook_event_name == "UserPromptSubmit":
        return None

    if payload.hook_event_name == "SessionStart":
        if not result.overlay:
            return None
        return {
            "hookSpecificOutput": {
                "hookEventName": payload.hook_event_name,
                "additionalContext": result.overlay,
            }
        }

    if payload.hook_event_name == "Stop" and result.decision == "block":
        return {
            "decision": "block",
            "reason": result.overlay or result.reason or "Continue the current phase before stopping.",
        }

    return None
