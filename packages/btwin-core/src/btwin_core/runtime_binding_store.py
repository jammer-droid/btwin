"""Project-local runtime binding store for the current thread/agent context."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class RuntimeBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    agent_name: str
    bound_at: str
    status: Literal["active", "closed"] = "active"
    opened_at: str | None = None
    last_seen_at: str | None = None
    closed_at: str | None = None
    closed_reason: str | None = None

    @model_validator(mode="after")
    def _normalize_lifecycle_fields(self) -> "RuntimeBinding":
        if self.opened_at is None:
            self.opened_at = self.bound_at
        if self.last_seen_at is None:
            self.last_seen_at = self.opened_at or self.bound_at
        return self


class RuntimeBindingState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding: RuntimeBinding | None = None
    binding_error: str | None = None

    @property
    def bound(self) -> bool:
        return self.binding is not None and self.binding.status == "active"


class RuntimeBindingStore:
    """Persist the current runtime binding under the project .btwin area."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "runtime" / "binding.json"

    def read_state(self) -> RuntimeBindingState:
        if not self.file_path.exists():
            return RuntimeBindingState()

        try:
            raw = self.file_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
            return RuntimeBindingState(binding=RuntimeBinding.model_validate(data))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            logger.warning("Failed to load runtime binding: %s", self.file_path, exc_info=True)
            return RuntimeBindingState(
                binding_error=f"Failed to load runtime binding: {exc.__class__.__name__}: {exc}",
            )

    def write(self, binding: RuntimeBinding) -> RuntimeBinding:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(binding.model_dump(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.file_path.parent,
                prefix="binding-",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_path = Path(tmp_file.name)
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
            tmp_path.replace(self.file_path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except FileNotFoundError:
                    pass
        return binding

    def bind(self, thread_id: str, agent_name: str) -> RuntimeBinding:
        now = _now_iso()
        binding = RuntimeBinding(
            thread_id=thread_id,
            agent_name=agent_name,
            bound_at=now,
            status="active",
            opened_at=now,
            last_seen_at=now,
            closed_at=None,
            closed_reason=None,
        )
        return self.write(binding)

    def observe_workflow_hook_event(self, binding: RuntimeBinding, event_name: str) -> RuntimeBinding:
        if binding.status != "active":
            return binding
        if event_name not in {"SessionStart", "UserPromptSubmit", "Stop"}:
            return binding
        observed_at = _now_iso()
        refreshed = binding.model_copy(
            update={
                "status": "active",
                "last_seen_at": observed_at,
                "closed_at": None,
                "closed_reason": None,
            }
        )
        return self.write(refreshed)

    def observe_session_start(self, binding: RuntimeBinding) -> RuntimeBinding:
        return self.observe_workflow_hook_event(binding, "SessionStart")

    def close_binding(self, binding: RuntimeBinding, *, reason: str, closed_at: str | None = None) -> RuntimeBinding:
        if binding.status == "closed":
            return binding
        closed = binding.model_copy(
            update={
                "status": "closed",
                "closed_at": closed_at or _now_iso(),
                "closed_reason": reason,
            }
        )
        return self.write(closed)

    def cleanup_stale_active_binding(
        self,
        *,
        max_age_seconds: int = 24 * 60 * 60,
        closed_reason: str = "stale_last_seen",
    ) -> RuntimeBinding | None:
        state = self.read_state()
        binding = state.binding
        if binding is None or binding.status != "active":
            return None

        last_seen_at = binding.last_seen_at or binding.opened_at or binding.bound_at
        try:
            last_seen = _parse_iso_datetime(last_seen_at)
        except ValueError:
            return None

        observed_at = _parse_iso_datetime(_now_iso())
        if observed_at - last_seen < timedelta(seconds=max_age_seconds):
            return None

        return self.close_binding(binding, reason=closed_reason, closed_at=observed_at.isoformat())

    def clear(self) -> RuntimeBindingState:
        current = self.read_state()
        if self.file_path.exists():
            self.file_path.unlink()
        return current
