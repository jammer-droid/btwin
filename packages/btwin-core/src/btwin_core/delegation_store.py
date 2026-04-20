"""JSONL-backed store for delegation state snapshots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from btwin_core.delegation_state import DelegationState

_ENTRY_KIND_STATE: Literal["state"] = "state"
_ENTRY_KIND_DELETED: Literal["deleted"] = "deleted"


class DelegationStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "runtime" / "delegation-state.jsonl"

    def read(self, thread_id: str) -> DelegationState | None:
        if not self.file_path.exists():
            return None

        for kind, payload in reversed(self._read_entries()):
            if kind == _ENTRY_KIND_DELETED and payload == thread_id:
                return None
            if kind == _ENTRY_KIND_STATE and payload.thread_id == thread_id:
                return payload
        return None

    def write(self, state: DelegationState) -> DelegationState:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(state.model_dump(), ensure_ascii=False, sort_keys=True) + "\n")
        return state

    def list_states(self) -> list[DelegationState]:
        states: list[DelegationState] = []
        seen_thread_ids: set[str] = set()

        for kind, payload in reversed(self._read_entries()):
            thread_id = payload if kind == _ENTRY_KIND_DELETED else payload.thread_id
            if thread_id in seen_thread_ids:
                continue
            seen_thread_ids.add(thread_id)
            if kind == _ENTRY_KIND_STATE:
                states.append(payload)

        return states

    def delete(self, thread_id: str) -> bool:
        if self.read(thread_id) is None:
            return False

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        tombstone = json.dumps({"thread_id": thread_id, "deleted": True}, ensure_ascii=False, sort_keys=True)
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(tombstone + "\n")
        return True

    def _read_entries(self) -> list[tuple[Literal["state", "deleted"], DelegationState | str]]:
        if not self.file_path.exists():
            return []

        entries: list[tuple[Literal["state", "deleted"], DelegationState | str]] = []
        for line in self.file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if isinstance(payload, dict) and payload.get("deleted") is True:
                    thread_id = payload.get("thread_id")
                    if isinstance(thread_id, str) and thread_id:
                        entries.append((_ENTRY_KIND_DELETED, thread_id))
                    continue
                entries.append((_ENTRY_KIND_STATE, DelegationState.model_validate(payload)))
            except (json.JSONDecodeError, ValidationError):
                continue
        return entries
