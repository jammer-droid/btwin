"""Core indexer that keeps markdown entries and vector index in sync."""

from __future__ import annotations

from pathlib import Path
import math
import time

import yaml

from btwin_core.frontmatter import parse_frontmatter_to_metadata
from btwin_core.indexer_manifest import IndexManifest
from btwin_core.indexer_models import IndexEntry, IndexStatus, RecordType
from btwin_core.storage import Storage
from btwin_core.vector import VectorStore


class CoreIndexer:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.storage = Storage(data_dir)
        self.vector_store = VectorStore(persist_dir=data_dir / "index")
        self.manifest = IndexManifest(data_dir / "index_manifest.yaml")
        self._kpi_path = data_dir / "indexer_kpi.yaml"
        self._repair_history_path = data_dir / "indexer_repair_history.jsonl"
        self._kpi = self._load_kpi()

    def mark_pending(self, *, doc_id: str, path: str, record_type: RecordType, checksum: str, project: str | None = None) -> IndexEntry:
        existing = self.manifest.get(doc_id)
        status: IndexStatus = "pending"
        if existing is not None and existing.checksum != checksum:
            status = "stale"
        elif existing is not None and existing.status in {"failed", "deleted"}:
            status = "pending"
        elif existing is not None:
            status = existing.status

        pending_since = time.time() if status in {"pending", "stale"} else None
        return self.manifest.upsert(
            doc_id=doc_id,
            path=path,
            record_type=record_type,
            checksum=checksum,
            status=status,
            project=project,
            pending_since=pending_since,
        )

    def refresh(self, limit: int | None = None) -> dict[str, int]:
        processed = 0
        indexed = 0
        deleted = 0
        failed = 0
        kpi_changed = False

        queue: list[IndexEntry] = []
        queue.extend(self.manifest.list_by_status("pending"))
        queue.extend(self.manifest.list_by_status("stale"))
        queue.extend(self.manifest.list_by_status("failed"))
        queue.extend(self.manifest.list_by_status("deleted"))

        if limit is not None:
            queue = queue[:limit]

        for item in queue:
            processed += 1

            if item.status == "deleted":
                self.vector_store.delete(item.doc_id)
                deleted += 1
                continue

            source_path = self.data_dir / item.path
            if not source_path.exists():
                self.vector_store.delete(item.doc_id)
                self.manifest.mark_status(item.doc_id, "deleted", error="source file missing")
                deleted += 1
                continue

            try:
                current_checksum = self._sha256(source_path)
                if current_checksum != item.checksum:
                    item = self.manifest.upsert(
                        doc_id=item.doc_id,
                        path=item.path,
                        record_type=item.record_type,
                        checksum=current_checksum,
                        status=item.status,
                        pending_since=item.pending_since,
                    )

                content = source_path.read_text(encoding="utf-8")
                extended_meta = parse_frontmatter_to_metadata(content)

                tldr = extended_meta.get("tldr")
                if not tldr:
                    raise ValueError(f"Missing tldr in frontmatter: {item.path}")

                base_metadata = {
                    "record_type": item.record_type,
                    "path": item.path,
                    "doc_version": str(item.doc_version),
                    "project": item.project or "_global",
                    "file_path": str(source_path),
                }
                base_metadata.update(extended_meta)
                self.vector_store.add(
                    doc_id=item.doc_id,
                    content=tldr,
                    metadata=base_metadata,
                )
                self.manifest.mark_status(item.doc_id, "indexed", error=None, clear_pending_since=True)
                if item.pending_since is not None:
                    latency_ms = max(0.0, (time.time() - item.pending_since) * 1000.0)
                    self._kpi["write_to_indexed_samples"] += 1
                    self._kpi["write_to_indexed_total_ms"] += latency_ms
                    kpi_changed = True
                indexed += 1
            except Exception as exc:  # pragma: no cover - defensive
                self.manifest.mark_status(item.doc_id, "failed", error=str(exc))
                failed += 1

        if kpi_changed:
            self._save_kpi()

        return {
            "processed": processed,
            "indexed": indexed,
            "deleted": deleted,
            "failed": failed,
        }

    def reconcile(self) -> dict[str, int]:
        docs = self.storage.list_indexable_documents()
        current_doc_ids = {doc["doc_id"] for doc in docs}

        for doc in docs:
            self.mark_pending(
                doc_id=doc["doc_id"],
                path=doc["path"],
                record_type=doc["record_type"],
                checksum=doc["checksum"],
                project=doc.get("project"),
            )

        for status in ("pending", "indexed", "stale", "failed"):
            for item in self.manifest.list_by_status(status):
                if item.doc_id not in current_doc_ids:
                    self.manifest.mark_status(item.doc_id, "deleted", error="reconcile: source missing")

        manifest_doc_ids = {item.doc_id for item in self.manifest.list_all()}
        vector_ids = self.vector_store.list_ids()
        orphan_ids = sorted(vector_ids - manifest_doc_ids)
        if orphan_ids:
            self.vector_store._collection.delete(ids=orphan_ids)

        result = self.refresh()
        result["orphan_vectors_removed"] = len(orphan_ids)
        return result

    def verify_doc_integrity(self, doc_id: str) -> dict[str, object]:
        item = self.manifest.get(doc_id)
        if item is None:
            return {
                "ok": False,
                "doc_id": doc_id,
                "reason": "manifest_missing",
                "status": None,
                "checksum_match": False,
                "vector_present": False,
            }

        source_path = self.data_dir / item.path
        if not source_path.exists():
            return {
                "ok": False,
                "doc_id": doc_id,
                "reason": "source_missing",
                "status": item.status,
                "checksum_match": False,
                "vector_present": False,
            }

        current_checksum = self._sha256(source_path)
        checksum_match = current_checksum == item.checksum
        vector_present = self.vector_store.has(doc_id)
        status_ok = item.status == "indexed"
        ok = status_ok and checksum_match and vector_present

        reason = "healthy"
        if not ok:
            if not status_ok:
                reason = "status_not_indexed"
            elif not checksum_match:
                reason = "checksum_mismatch"
            elif not vector_present:
                reason = "vector_missing"

        return {
            "ok": ok,
            "doc_id": doc_id,
            "reason": reason,
            "status": item.status,
            "checksum_match": checksum_match,
            "vector_present": vector_present,
        }

    def repair(self, doc_id: str) -> dict[str, object]:
        started = time.perf_counter()
        self._kpi["repair_attempts"] += 1

        item = self.manifest.get(doc_id)
        if item is None:
            self._record_repair_duration(started)
            result = {"ok": False, "error": "not_found", "doc_id": doc_id}
            self._record_repair_result(result)
            return result

        source_path = self.data_dir / item.path
        if not source_path.exists():
            self.vector_store.delete(item.doc_id)
            self.manifest.mark_status(item.doc_id, "deleted", error="repair: source missing")
            self._record_repair_duration(started)
            result = {"ok": False, "error": "source_missing", "doc_id": doc_id, "status": "deleted"}
            self._record_repair_result(result)
            return result

        checksum = self._sha256(source_path)
        updated = self.manifest.upsert(
            doc_id=item.doc_id,
            path=item.path,
            record_type=item.record_type,
            checksum=checksum,
            status="stale",
            pending_since=time.time(),
        )

        try:
            content = source_path.read_text(encoding="utf-8")
            extended_meta = parse_frontmatter_to_metadata(content)

            tldr = extended_meta.get("tldr")
            if not tldr:
                raise ValueError(f"Missing tldr in frontmatter: {updated.path}")

            base_metadata = {
                "record_type": updated.record_type,
                "path": updated.path,
                "doc_version": str(updated.doc_version),
                "project": updated.project or "_global",
                "file_path": str(source_path),
            }
            base_metadata.update(extended_meta)
            self.vector_store.add(
                doc_id=updated.doc_id,
                content=tldr,
                metadata=base_metadata,
            )
            self.manifest.mark_status(updated.doc_id, "indexed", error=None, clear_pending_since=True)
            self._kpi["repair_successes"] += 1
            self._record_repair_duration(started)
            result = {"ok": True, "doc_id": doc_id, "status": "indexed"}
            self._record_repair_result(result)
            return result
        except Exception as exc:  # pragma: no cover - defensive
            failed = self.manifest.mark_status(updated.doc_id, "failed", error=str(exc))
            self._record_repair_duration(started)
            result = {"ok": False, "doc_id": doc_id, "status": failed.status, "error": str(exc)}
            self._record_repair_result(result)
            return result

    def kpi_summary(self) -> dict[str, float | int | None]:
        indexed_doc_ids = {item.doc_id for item in self.manifest.list_by_status("indexed")}
        vector_doc_ids = self.vector_store.list_ids()
        mismatch_count = len(indexed_doc_ids - vector_doc_ids) + len(vector_doc_ids - indexed_doc_ids)

        latency_avg = None
        if self._kpi["write_to_indexed_samples"] > 0:
            latency_avg = self._kpi["write_to_indexed_total_ms"] / self._kpi["write_to_indexed_samples"]

        repair_success_rate = None
        repair_avg_duration = None
        if self._kpi["repair_attempts"] > 0:
            repair_success_rate = self._kpi["repair_successes"] / self._kpi["repair_attempts"]
            repair_avg_duration = self._kpi["repair_total_duration_ms"] / self._kpi["repair_attempts"]

        return {
            "write_to_indexed_latency_ms_avg": latency_avg,
            "manifest_vector_mismatch_count": mismatch_count,
            "repair_success_rate": repair_success_rate,
            "repair_avg_duration_ms": repair_avg_duration,
        }

    def status_summary(self, *, project: str | None = None) -> dict[str, int]:
        if project is None:
            return self.manifest.summary()

        counts: dict[str, int] = {
            "total": 0,
            "pending": 0,
            "indexed": 0,
            "stale": 0,
            "failed": 0,
            "deleted": 0,
        }
        for item in self.manifest.list_all():
            if item.project != project:
                continue
            counts["total"] += 1
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def failure_queue(self, limit: int = 50, *, project: str | None = None) -> list[dict[str, object]]:
        if limit <= 0:
            return []
        items = self.manifest.list_by_status("failed") + self.manifest.list_by_status("stale")
        if project is not None:
            items = [item for item in items if item.project == project]
        items = items[:limit]
        return [
            {
                "doc_id": item.doc_id,
                "path": item.path,
                "status": item.status,
                "error": item.error,
                "doc_version": item.doc_version,
            }
            for item in items
        ]

    def repair_history(self, limit: int = 20) -> list[dict[str, object]]:
        if limit <= 0 or not self._repair_history_path.exists():
            return []
        import json

        lines = self._repair_history_path.read_text(encoding="utf-8").splitlines()
        rows = lines[-limit:]
        return [json.loads(line) for line in rows if line.strip()]

    def _load_kpi(self) -> dict[str, float | int]:
        defaults: dict[str, float | int] = {
            "write_to_indexed_samples": 0,
            "write_to_indexed_total_ms": 0.0,
            "repair_attempts": 0,
            "repair_successes": 0,
            "repair_total_duration_ms": 0.0,
        }
        if not self._kpi_path.exists():
            return defaults

        raw = yaml.safe_load(self._kpi_path.read_text()) or {}
        if not isinstance(raw, dict):
            return defaults

        merged = defaults.copy()
        for key, default_value in defaults.items():
            if key not in raw:
                continue

            value = raw[key]
            try:
                if isinstance(default_value, int):
                    merged[key] = int(value)
                else:
                    parsed = float(value)
                    merged[key] = parsed if math.isfinite(parsed) else default_value
            except (TypeError, ValueError, OverflowError):
                merged[key] = default_value
        return merged

    def _save_kpi(self) -> None:
        payload = yaml.dump(self._kpi, allow_unicode=True, sort_keys=False)
        tmp_path = self._kpi_path.with_suffix(self._kpi_path.suffix + ".tmp")
        tmp_path.write_text(payload)
        tmp_path.replace(self._kpi_path)

    def _record_repair_duration(self, started: float) -> None:
        duration_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        self._kpi["repair_total_duration_ms"] += duration_ms
        self._save_kpi()

    def _record_repair_result(self, result: dict[str, object]) -> None:
        import json

        row = {"timestamp": time.time(), **result}
        with self._repair_history_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _sha256(path: Path) -> str:
        import hashlib

        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"sha256:{digest}"


__all__ = ["CoreIndexer"]
