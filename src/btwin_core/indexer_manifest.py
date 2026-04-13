"""YAML-backed index manifest store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from btwin_core.indexer_models import IndexEntry, IndexStatus, RecordType


class IndexManifest:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, IndexEntry] = self._load_entries()

    def get(self, doc_id: str) -> IndexEntry | None:
        entry = self._entries.get(doc_id)
        if entry is None:
            return None
        return entry.model_copy(deep=True)

    def upsert(
        self,
        *,
        doc_id: str,
        path: str,
        record_type: RecordType,
        checksum: str,
        status: IndexStatus,
        project: str | None = None,
        doc_version: int | None = None,
        error: str | None = None,
        pending_since: float | None = None,
    ) -> IndexEntry:
        existing = self._entries.get(doc_id)

        if doc_version is None:
            if existing is None:
                resolved_version = 1
            elif existing.checksum != checksum:
                resolved_version = existing.doc_version + 1
            else:
                resolved_version = existing.doc_version
        else:
            resolved_version = doc_version

        resolved_project = project if project is not None else (existing.project if existing else None)

        entry = IndexEntry(
            doc_id=doc_id,
            path=path,
            record_type=record_type,
            checksum=checksum,
            status=status,
            project=resolved_project,
            doc_version=resolved_version,
            error=error,
            pending_since=pending_since if pending_since is not None else (existing.pending_since if existing else None),
        )
        self._entries[doc_id] = entry
        self._save_entries()
        return entry.model_copy(deep=True)

    def mark_status(
        self,
        doc_id: str,
        status: IndexStatus,
        error: str | None = None,
        *,
        clear_pending_since: bool = False,
    ) -> IndexEntry:
        if doc_id not in self._entries:
            raise ValueError(f"doc_id '{doc_id}' not found in index manifest")
        entry = self._entries[doc_id]
        payload: dict[str, Any] = {"status": status, "error": error}
        if clear_pending_since:
            payload["pending_since"] = None
        updated = entry.model_copy(update=payload)
        self._entries[doc_id] = updated
        self._save_entries()
        return updated.model_copy(deep=True)

    def list_all(self) -> list[IndexEntry]:
        return [item.model_copy(deep=True) for item in self._entries.values()]

    def list_by_status(self, status: IndexStatus) -> list[IndexEntry]:
        return [item.model_copy(deep=True) for item in self._entries.values() if item.status == status]

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "total": len(self._entries),
            "pending": 0,
            "indexed": 0,
            "stale": 0,
            "failed": 0,
            "deleted": 0,
        }
        for item in self._entries.values():
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def _load_entries(self) -> dict[str, IndexEntry]:
        if not self.manifest_path.exists():
            return {}

        raw = yaml.safe_load(self.manifest_path.read_text()) or []
        if not isinstance(raw, list):
            raise ValueError("index manifest file must contain a list")

        items = [IndexEntry.model_validate(row) for row in raw]
        return {item.doc_id: item for item in items}

    def _save_entries(self) -> None:
        payload = yaml.dump(
            [item.model_dump(mode="json") for item in self._entries.values()],
            allow_unicode=True,
            sort_keys=False,
        )

        tmp_path = self.manifest_path.with_suffix(self.manifest_path.suffix + ".tmp")
        tmp_path.write_text(payload)
        tmp_path.replace(self.manifest_path)
