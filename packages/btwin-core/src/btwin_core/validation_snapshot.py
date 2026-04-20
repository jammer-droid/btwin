"""Derived validation snapshot helpers for one-screen validation context."""

from __future__ import annotations

from typing import Any


def _gate_progression(phase_cycle_payload: dict[str, object] | None) -> str | None:
    visual = phase_cycle_payload.get("visual") if isinstance(phase_cycle_payload, dict) else None
    gates = visual.get("gates") if isinstance(visual, dict) else None
    if not isinstance(gates, list) or not gates:
        return None
    items: list[str] = []
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        label = str(gate.get("label") or gate.get("key") or "").strip()
        if not label:
            continue
        status = str(gate.get("status") or "").strip().lower()
        items.append(f"• {label}" if status == "active" else label)
    return " - ".join(items) if items else None


def _case_verdict(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return "SKIP"
    if normalized.startswith("ready") or normalized.startswith("ok") or normalized.startswith("pass"):
        return "PASS"
    if normalized.startswith("fail"):
        return "FAIL"
    if (
        normalized.startswith("not triggered")
        or normalized.startswith("not evaluated")
        or normalized.startswith("not applicable")
    ):
        return "SKIP"
    return "WARN"


def _case_label(raw_name: str) -> str:
    label = raw_name.strip().replace("_", " ")
    if not label:
        return "-"
    return label[0].upper() + label[1:].lower()


def _relevant_case_progression(validation_cases: list[str]) -> str:
    active_items: list[str] = []
    pass_items: list[str] = []
    fallback: str | None = None
    for case_line in validation_cases:
        if ":" not in case_line:
            continue
        raw_name, raw_value = case_line.split(":", 1)
        verdict = _case_verdict(raw_value)
        label = f"{_case_label(raw_name)} [{verdict}]"
        if fallback is None:
            fallback = label
        if verdict in {"WARN", "FAIL"}:
            active_items.append(label)
        elif verdict == "PASS":
            pass_items.append(label)
    if active_items:
        return " - ".join(active_items)
    if pass_items:
        return " - ".join(pass_items)
    return fallback or "-"


def _evidence_summary(
    *,
    trace_rows: list[dict[str, object]],
    runtime_sessions: dict[str, dict[str, object]],
    telemetry_rows: list[dict[str, Any]],
    protocol_plan: dict[str, object] | None,
) -> list[str]:
    evidence = [
        "workflow trace present" if trace_rows else "workflow trace missing",
        (
            f"runtime sessions {len(runtime_sessions)} tracked"
            if runtime_sessions
            else "runtime sessions unavailable"
        ),
        (
            f"telemetry signals {len(telemetry_rows)} recent"
            if telemetry_rows
            else "telemetry signals missing"
        ),
    ]
    if isinstance(protocol_plan, dict):
        missing = protocol_plan.get("missing")
        if isinstance(missing, list) and missing:
            count = len(missing)
            noun = "participant" if count == 1 else "participants"
            evidence.append(f"protocol gaps {count} {noun}")
        else:
            evidence.append("protocol plan aligned")
    return evidence


def _confidence(
    *,
    trace_rows: list[dict[str, object]],
    runtime_sessions: dict[str, dict[str, object]],
    telemetry_rows: list[dict[str, Any]],
    protocol_plan: dict[str, object] | None,
) -> str:
    score = 0
    if trace_rows:
        score += 1
    if runtime_sessions:
        score += 1
    if telemetry_rows:
        score += 1
    if isinstance(protocol_plan, dict):
        score += 1
    if score >= 3:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def build_validation_snapshot(
    *,
    thread: dict[str, object],
    phase_cycle_payload: dict[str, object] | None,
    validation: dict[str, object],
    validation_cases: list[str],
    trace_rows: list[dict[str, object]],
    runtime_sessions: dict[str, dict[str, object]],
    telemetry_rows: list[dict[str, Any]],
    protocol_plan: dict[str, object] | None,
    phase_progression: str | None,
    procedure_progression: str | None,
) -> dict[str, object]:
    return {
        "thread_id": str(thread.get("thread_id") or ""),
        "topic": str(thread.get("topic") or ""),
        "protocol": str(thread.get("protocol") or ""),
        "phase": str(thread.get("current_phase") or ""),
        "verdict": str(validation.get("verdict") or "PASS").upper(),
        "reasons": list(validation.get("reasons") or []),
        "checks": list(validation.get("checks") or []),
        "next_expected_action": str(validation.get("next_expected_action") or "none"),
        "phase_progression": phase_progression or "-",
        "procedure_progression": procedure_progression or "-",
        "gate_progression": _gate_progression(phase_cycle_payload) or "-",
        "relevant_case_progression": _relevant_case_progression(validation_cases),
        "evidence_summary": _evidence_summary(
            trace_rows=trace_rows,
            runtime_sessions=runtime_sessions,
            telemetry_rows=telemetry_rows,
            protocol_plan=protocol_plan,
        ),
        "confidence": _confidence(
            trace_rows=trace_rows,
            runtime_sessions=runtime_sessions,
            telemetry_rows=telemetry_rows,
            protocol_plan=protocol_plan,
        ),
    }
