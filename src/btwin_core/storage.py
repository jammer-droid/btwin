"""Markdown file storage for B-TWIN entries."""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

from btwin.core.orchestration_models import OrchestrationRecord
from btwin_core.common_record_models import CommonRecordMetadata
from btwin_core.document_contracts import validate_document_contract
from btwin_core.frontmatter import build_frontmatter
from btwin_core.models import Entry

_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$")
_RESERVED_PROJECT_NAMES = {"global", "convo", "collab"}
_DEFAULT_PROJECT = "_global"
_RECORD_SUBDIRS = {"convo", "collab", "shared"}


class Storage:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.entries_dir = data_dir / "entries"
        self.promoted_entries_dir = self.entries_dir / "global"
        self.shared_entries_dir = self._project_dir(None) / "shared"

    # -- helpers for project-aware paths --

    def _resolve_project(self, project: str | None) -> str:
        if project is None or project == "":
            return _DEFAULT_PROJECT
        if not _PROJECT_NAME_RE.match(project):
            raise ValueError(
                f"Invalid project name: {project!r}. "
                "Must match [a-zA-Z0-9_][a-zA-Z0-9_.-]*"
            )
        if project in _RESERVED_PROJECT_NAMES:
            raise ValueError(f"'{project}' is a reserved project name")
        return project

    def project_dir(self, project: str | None) -> Path:
        """Return the root directory for a given project (public API)."""
        return self.entries_dir / self._resolve_project(project)

    _project_dir = project_dir

    @property
    def convo_entries_dir(self) -> Path:
        """Unified convo entries dir: entries/convo/."""
        return self.entries_dir / "convo"

    @property
    def orchestration_entries_dir(self) -> Path:
        """Legacy accessor -- points to _global/collab directory for backward compat."""
        return self._project_dir(None) / "collab"

    # =========================================================================
    # save / read / list entries
    # =========================================================================

    def save_entry(self, entry: Entry, *, project: str | None = None) -> Path:
        """Save an entry under unified path: entries/entry/{date}/{record_id}.md"""
        resolved = self._resolve_project(project)

        # Build standard frontmatter
        standard_fm = build_frontmatter(
            record_type="entry",
            source_project=resolved,
            tldr=entry.metadata.get("tldr", ""),
            tags=entry.metadata.get("tags"),
            subject_projects=entry.metadata.get("subject_projects"),
            contributors=entry.metadata.get("contributors"),
        )
        # Override date from entry, not from now()
        standard_fm["date"] = entry.date

        # Merge: user metadata first, then standard fields take precedence
        fm = dict(entry.metadata)
        fm.update(standard_fm)
        self._ensure_contract("entry", fm)
        frontmatter = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()

        # Unified path: entries/entry/{date}/{record_id}.md
        record_id = str(standard_fm["record_id"])
        date_dir = self.entries_dir / "entry" / entry.date
        date_dir.mkdir(parents=True, exist_ok=True)
        file_path = date_dir / f"{record_id}.md"

        file_path.write_text(f"---\n{frontmatter}\n---\n\n{entry.content}")
        return file_path

    def _parse_file(self, raw: str, date: str, slug: str) -> Entry:
        """Parse a markdown file, extracting frontmatter if present."""
        if raw.startswith("---\n"):
            parts = raw.split("---\n", 2)
            if len(parts) >= 3:
                fm_text = parts[1]
                content = parts[2].lstrip("\n")
                metadata = yaml.safe_load(fm_text) or {}
                if "date" in metadata:
                    metadata["date"] = str(metadata["date"])
                if "slug" in metadata:
                    metadata["slug"] = str(metadata["slug"])
                return Entry(date=date, slug=slug, content=content, metadata=metadata)
        return Entry(date=date, slug=slug, content=raw)

    def list_entries(self, *, project: str | None = None) -> list[Entry]:
        """List saved entries from unified path: entries/entry/."""
        entry_type_dir = self.entries_dir / "entry"
        if not entry_type_dir.exists():
            return []

        entries: list[Entry] = []
        for date_dir in sorted(entry_type_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            for md_file in sorted(date_dir.glob("*.md")):
                raw = md_file.read_text()
                parsed = self._parse_file(raw, date_dir.name, md_file.stem)
                # Filter by project if specified
                if project is not None:
                    resolved = self._resolve_project(project)
                    fm_project = parsed.metadata.get("source_project") or parsed.metadata.get("project", "")
                    if fm_project != resolved:
                        continue
                entries.append(parsed)
        return entries

    def read_entry(self, date: str, slug: str, *, project: str | None = None) -> Entry | None:
        """Read an entry by date/slug. Searches unified entry dir by slug in frontmatter."""
        entry_type_dir = self.entries_dir / "entry" / date
        if not entry_type_dir.exists():
            return None
        for md_file in entry_type_dir.glob("*.md"):
            raw = md_file.read_text()
            parsed = self._parse_file(raw, date, md_file.stem)
            fm_slug = parsed.metadata.get("slug", md_file.stem)
            if fm_slug != slug:
                continue
            # Check project filter if specified
            if project is not None:
                resolved = self._resolve_project(project)
                fm_project = parsed.metadata.get("source_project") or parsed.metadata.get("project", "")
                if fm_project != resolved:
                    continue
            return parsed
        return None

    # =========================================================================
    # convo records
    # =========================================================================

    def save_convo_record(
        self,
        *,
        content: str,
        requested_by_user: bool = False,
        topic: str | None = None,
        created_at: datetime | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        subject_projects: list[str] | None = None,
        tldr: str = "",
        contributors: list[str] | None = None,
    ) -> Entry:
        resolved = self._resolve_project(project)
        now = created_at or datetime.now(timezone.utc)
        date = now.strftime("%Y-%m-%d")
        slug = f"convo-{now.strftime('%H%M%S%f')}"

        # Build standard frontmatter
        standard_fm = build_frontmatter(
            record_type="convo",
            source_project=resolved,
            tldr=tldr,
            tags=tags,
            subject_projects=subject_projects,
            contributors=contributors,
        )

        metadata: dict[str, object] = dict(standard_fm)
        metadata["created_at"] = now.isoformat()
        metadata["last_updated_at"] = now.isoformat()
        metadata["date"] = date

        # Unified path: entries/convo/{date}/{record_id}.md
        record_id = str(standard_fm["record_id"])
        date_dir = self.entries_dir / "convo" / date
        date_dir.mkdir(parents=True, exist_ok=True)
        file_path = date_dir / f"{record_id}.md"

        self._ensure_contract("convo", metadata)
        frontmatter = yaml.dump(metadata, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
        file_path.write_text(f"---\n{frontmatter}\n---\n\n{content}\n")
        return Entry(date=date, slug=slug, content=content, metadata=metadata)

    def list_convo_entries(self, *, project: str | None = None) -> list[Entry]:
        """List convo entries from unified path: entries/convo/."""
        convo_type_dir = self.entries_dir / "convo"
        if not convo_type_dir.exists():
            return []

        entries: list[Entry] = []
        for date_dir in sorted(convo_type_dir.iterdir()):
            if not date_dir.is_dir():
                continue
            for md_file in sorted(date_dir.glob("*.md")):
                raw = md_file.read_text()
                parsed = self._parse_file(raw, date_dir.name, md_file.stem)
                # Filter by project if specified
                if project is not None:
                    resolved = self._resolve_project(project)
                    fm_project = parsed.metadata.get("source_project") or parsed.metadata.get("project", "")
                    if fm_project != resolved:
                        continue
                entries.append(parsed)
        return entries

    def save_shared_record(
        self,
        *,
        namespace: str,
        record_id: str,
        content: str,
        metadata: dict[str, object],
        project: str | None = None,
    ) -> Path:
        """Save a shared markdown record under entries/{project}/shared/<namespace>/YYYY-MM-DD/."""
        common = CommonRecordMetadata.model_validate(
            {
                "docVersion": metadata.get("docVersion"),
                "status": metadata.get("status"),
                "createdAt": metadata.get("createdAt"),
                "updatedAt": metadata.get("updatedAt"),
                "recordType": metadata.get("recordType"),
            }
        )
        namespace_slug = self._safe_path_segment(namespace)
        record_slug = self._safe_path_segment(record_id)
        if not namespace_slug:
            raise ValueError("namespace must contain at least one safe character")
        if not record_slug:
            raise ValueError("record_id must contain at least one safe character")

        frontmatter_metadata = dict(metadata)
        frontmatter_record_id = str(frontmatter_metadata.get("recordId") or "").strip()
        if frontmatter_record_id and frontmatter_record_id != record_id:
            raise ValueError("metadata recordId must match record_id")
        frontmatter_metadata["recordId"] = record_id
        frontmatter_metadata["project"] = self._resolve_project(project)

        file_path = self._find_shared_record_file(
            namespace_slug=namespace_slug,
            record_slug=record_slug,
            project=project,
        )
        if file_path is None:
            day = common.created_at.date().isoformat()
            file_path = self._project_dir(project) / "shared" / namespace_slug / day / f"{record_slug}.md"
            canonical_created_at = common.created_at.isoformat()
        else:
            existing_metadata = self._parse_frontmatter_metadata(file_path.read_text()) or {}
            canonical_created_at = str(existing_metadata.get("createdAt") or common.created_at.isoformat())

        file_path.parent.mkdir(parents=True, exist_ok=True)

        frontmatter_metadata["recordType"] = common.record_type
        frontmatter_metadata["createdAt"] = canonical_created_at
        frontmatter_metadata["updatedAt"] = common.updated_at.isoformat()
        frontmatter = yaml.dump(
            frontmatter_metadata,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        file_path.write_text(f"---\n{frontmatter}\n---\n\n{content}\n")
        return file_path

    # =========================================================================
    # orchestration records
    # =========================================================================

    def save_orchestration_record(self, record: OrchestrationRecord, *, project: str | None = None) -> Path:
        file_path = self._orchestration_path(record, project=project)
        file_path.parent.mkdir(parents=True, exist_ok=True)

        metadata = record.model_dump(by_alias=True, mode="json")
        metadata["project"] = self._resolve_project(project)
        metadata["tldr"] = record.summary
        self._ensure_contract("collab", metadata)
        frontmatter = yaml.dump(
            metadata,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        body = self._render_orchestration_body(record)

        file_path.write_text(f"---\n{frontmatter}\n---\n\n{body}\n")
        return file_path

    def read_orchestration_record(self, record_id: str, *, project: str | None = None) -> OrchestrationRecord | None:
        loaded = self._find_orchestration_file(record_id, project=project)
        if loaded is None:
            return None
        return loaded[0]

    def read_orchestration_record_document(self, record_id: str, *, project: str | None = None) -> dict[str, str | dict[str, object]] | None:
        loaded = self._find_orchestration_file(record_id, project=project)
        if loaded is None:
            return None

        record, file_path, body = loaded
        frontmatter = self._parse_frontmatter_metadata(file_path.read_text()) or record.model_dump(
            by_alias=True,
            mode="json",
        )
        return {
            "recordId": record.record_id,
            "path": str(file_path),
            "frontmatter": frontmatter,
            "content": body,
        }

    def orchestration_index_doc_info(self, record_id: str, *, project: str | None = None) -> dict[str, str] | None:
        loaded = self._find_orchestration_file(record_id, project=project)
        if loaded is None:
            return None
        _record, file_path, _body = loaded
        return self._index_doc_info(file_path, record_type="collab")

    def update_orchestration_record(
        self,
        record_id: str,
        *,
        status: str,
        version: int,
        author_agent: str | None = None,
        project: str | None = None,
    ) -> OrchestrationRecord | None:
        loaded = self._find_orchestration_file(record_id, project=project)
        if loaded is None:
            return None

        existing, old_path, _body = loaded
        payload = existing.model_dump(by_alias=True, mode="json")
        payload["status"] = status
        payload["version"] = version
        if author_agent is not None:
            payload["authorAgent"] = author_agent

        updated = OrchestrationRecord.model_validate(payload)
        new_path = self._orchestration_path(updated, project=project)
        self.save_orchestration_record(updated, project=project)
        if old_path != new_path and old_path.exists():
            old_path.unlink()
        return updated

    def list_orchestration_records(self, *, project: str | None = None) -> list[OrchestrationRecord]:
        records: list[OrchestrationRecord] = []
        for file_path in self._iter_orchestration_files(project=project):
            loaded = self._load_orchestration_file(file_path)
            if loaded is None:
                continue
            records.append(loaded[0])
        return records

    # =========================================================================
    # promoted entries (no project partitioning -- always global)
    # =========================================================================

    def save_promoted_entry(self, *, item_id: str, source_record_id: str, content: str, tldr: str = "") -> Path:
        date_dir = self.promoted_entries_dir / "promoted"
        date_dir.mkdir(parents=True, exist_ok=True)

        file_path = date_dir / f"{item_id}.md"
        metadata: dict[str, object] = {
            "promotionItemId": item_id,
            "sourceRecordId": source_record_id,
            "scope": "global",
            "tldr": tldr or content[:120],
        }
        self._ensure_contract("promoted", metadata)
        frontmatter = yaml.dump(
            metadata,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        file_path.write_text(f"---\n{frontmatter}\n---\n\n{content}\n")
        return file_path

    def promoted_entry_exists(self, item_id: str) -> bool:
        file_path = self.promoted_entries_dir / "promoted" / f"{item_id}.md"
        return file_path.exists()

    def count_promoted_entries(self) -> int:
        promoted_dir = self.promoted_entries_dir / "promoted"
        if not promoted_dir.exists():
            return 0
        return len(list(promoted_dir.glob("*.md")))

    def list_promoted_entries(self) -> list[dict[str, str]]:
        promoted_dir = self.promoted_entries_dir / "promoted"
        if not promoted_dir.exists():
            return []

        items: list[dict[str, str]] = []
        for file_path in sorted(promoted_dir.glob("*.md")):
            raw = file_path.read_text()
            metadata = self._parse_frontmatter_metadata(raw)
            if metadata is None:
                continue

            item_id = str(metadata.get("promotionItemId", file_path.stem))
            source_record_id = str(metadata.get("sourceRecordId", ""))
            scope = str(metadata.get("scope", "global"))
            items.append(
                {
                    "itemId": item_id,
                    "sourceRecordId": source_record_id,
                    "scope": scope,
                    "path": str(file_path),
                }
            )

        return items

    # =========================================================================
    # indexable documents
    # =========================================================================

    def list_indexable_documents(self, *, project: str | None = None) -> list[dict[str, str]]:
        docs: list[dict[str, str]] = []

        if not self.entries_dir.exists():
            return docs

        # Unified entry path: entries/entry/**/*.md
        entry_type_dir = self.entries_dir / "entry"
        if entry_type_dir.exists():
            for md_file in sorted(entry_type_dir.glob("*/*.md")):
                info = self._index_doc_info(md_file, record_type="entry")
                info["project"] = self._project_from_frontmatter(md_file)
                if project is not None and info["project"] != self._resolve_project(project):
                    continue
                docs.append(info)

        # Unified convo path: entries/convo/**/*.md
        convo_type_dir = self.entries_dir / "convo"
        if convo_type_dir.exists():
            for md_file in sorted(convo_type_dir.glob("*/*.md")):
                info = self._index_doc_info(md_file, record_type="convo")
                info["project"] = self._project_from_frontmatter(md_file)
                if project is not None and info["project"] != self._resolve_project(project):
                    continue
                docs.append(info)

        # Unified workflow path: entries/workflow/**/*.md
        workflow_type_dir = self.entries_dir / "workflow"
        if workflow_type_dir.exists():
            for md_file in sorted(workflow_type_dir.glob("*/*.md")):
                info = self._index_doc_info(md_file, record_type="workflow")
                info["project"] = self._project_from_frontmatter(md_file)
                if project is not None and info["project"] != self._resolve_project(project):
                    continue
                docs.append(info)

        # Legacy/remaining paths: orchestration (collab), shared under per-project dirs
        project_dirs = self._collect_project_dirs(project)
        for proj_name, proj_dir in project_dirs:
            shared_dir = proj_dir / "shared"
            if shared_dir.exists():
                for md_file in sorted(shared_dir.glob("*/*/*.md")):
                    info = self._index_doc_info(
                        md_file,
                        record_type=self._shared_record_type(md_file, shared_dir=shared_dir),
                    )
                    info["project"] = proj_name
                    docs.append(info)

            orch_dir = proj_dir / "collab"
            if orch_dir.exists():
                for md_file in sorted(orch_dir.glob("*/*.md")):
                    info = self._index_doc_info(md_file, record_type="collab")
                    info["project"] = proj_name
                    docs.append(info)

        if project is None:
            promoted_dir = self.promoted_entries_dir / "promoted"
            if promoted_dir.exists():
                for md_file in sorted(promoted_dir.glob("*.md")):
                    info = self._index_doc_info(md_file, record_type="promoted")
                    info["project"] = "_global"
                    docs.append(info)

        return docs

    def _collect_project_dirs(self, project: str | None) -> list[tuple[str, Path]]:
        if project is not None:
            proj_dir = self._project_dir(project)
            if proj_dir.exists():
                return [(self._resolve_project(project), proj_dir)]
            return []

        result: list[tuple[str, Path]] = []
        if not self.entries_dir.exists():
            return result
        for d in sorted(self.entries_dir.iterdir()):
            if not d.is_dir():
                continue
            if d.name == "global":
                continue
            result.append((d.name, d))
        return result

    def _find_shared_record_file(
        self,
        *,
        namespace_slug: str,
        record_slug: str,
        project: str | None = None,
    ) -> Path | None:
        for _proj_name, proj_dir in self._collect_project_dirs(project):
            namespace_dir = proj_dir / "shared" / namespace_slug
            if not namespace_dir.exists():
                continue
            matches = sorted(namespace_dir.glob(f"*/{record_slug}.md"))
            if matches:
                return matches[0]
        return None

    # =========================================================================
    # orchestration internal helpers
    # =========================================================================

    def _find_orchestration_file(self, record_id: str, *, project: str | None = None) -> tuple[OrchestrationRecord, Path, str] | None:
        best: tuple[OrchestrationRecord, Path, str] | None = None
        for file_path in self._iter_orchestration_files(project=project):
            loaded = self._load_orchestration_file(file_path)
            if loaded is None:
                continue
            record, body = loaded
            if record.record_id == record_id:
                if best is None or record.version > best[0].version:
                    best = (record, file_path, body)
        return best

    def _iter_orchestration_files(self, *, project: str | None = None) -> Iterator[Path]:
        if project is not None:
            orch_dir = self._project_dir(project) / "collab"
            if not orch_dir.exists():
                return iter(())
            return iter(sorted(orch_dir.glob("*/*.md")))

        files: list[Path] = []
        if not self.entries_dir.exists():
            return iter(files)
        for proj_dir in sorted(self.entries_dir.iterdir()):
            if not proj_dir.is_dir() or proj_dir.name == "global":
                continue
            orch_dir = proj_dir / "collab"
            if orch_dir.exists():
                files.extend(sorted(orch_dir.glob("*/*.md")))
        return iter(files)

    @staticmethod
    def _load_orchestration_file(file_path: Path) -> tuple[OrchestrationRecord, str] | None:
        raw = file_path.read_text()
        parsed = Storage._parse_orchestration_frontmatter(raw)
        if parsed is None:
            return None

        parts = raw.split("---\n", 2)
        body = parts[2].lstrip("\n") if len(parts) >= 3 else ""
        return parsed, body

    @staticmethod
    def _parse_frontmatter_metadata(raw: str) -> dict[str, object] | None:
        if not raw.startswith("---\n"):
            return None
        parts = raw.split("---\n", 2)
        if len(parts) < 3:
            return None
        return yaml.safe_load(parts[1]) or {}

    @staticmethod
    def _parse_orchestration_frontmatter(raw: str) -> OrchestrationRecord | None:
        metadata = Storage._parse_frontmatter_metadata(raw)
        if metadata is None:
            return None
        metadata.pop("project", None)
        metadata.pop("tldr", None)
        try:
            return OrchestrationRecord.model_validate(metadata)
        except Exception:
            return None

    @staticmethod
    def _render_orchestration_body(record: OrchestrationRecord) -> str:
        body_lines = [record.summary, "", "## Evidence"]
        body_lines.extend([f"- {item}" for item in record.evidence])
        body_lines.append("")
        body_lines.append("## Next Action")
        body_lines.extend([f"- {item}" for item in record.next_action])
        return "\n".join(body_lines)

    @staticmethod
    def _ensure_contract(record_type: str, metadata: dict[str, object]) -> None:
        ok, reason = validate_document_contract(record_type, metadata)
        if not ok:
            raise ValueError(f"invalid {record_type} contract: {reason}")

    def _orchestration_path(self, record: OrchestrationRecord, *, project: str | None = None) -> Path:
        day = record.created_at.date().isoformat()
        safe_task = re.sub(r'[^a-zA-Z0-9_-]', '-', record.task_id)
        orch_dir = self._project_dir(project) / "collab"
        return orch_dir / day / f"{safe_task}-{record.status}-{record.record_id}.md"

    def _shared_record_type(self, file_path: Path, *, shared_dir: Path | None = None) -> str:
        metadata = self._parse_frontmatter_metadata(file_path.read_text()) or {}
        record_type = str(metadata.get("recordType") or "").strip()
        if record_type:
            return record_type

        base_dir = shared_dir or (self._project_dir(None) / "shared")
        relative = file_path.relative_to(base_dir)
        namespace = relative.parts[0] if relative.parts else "shared"
        return str(namespace)

    # =========================================================================
    # find / update entry by record_id
    # =========================================================================

    def find_by_record_id(self, record_id: str) -> Path | None:
        """Search all entries for a file containing the given record_id in frontmatter."""
        if not self.entries_dir.exists():
            return None
        for md_file in self.entries_dir.rglob("*.md"):
            raw = md_file.read_text()
            metadata = self._parse_frontmatter_metadata(raw)
            if metadata is None:
                continue
            if metadata.get("record_id") == record_id:
                return md_file
        return None

    def update_entry(
        self,
        *,
        record_id: str,
        content: str | None = None,
        tldr: str | None = None,
        tags: list[str] | None = None,
        subject_projects: list[str] | None = None,
        related_records: list[str] | None = None,
        derived_from: str | None = None,
        contributor: str | None = None,
    ) -> Path | None:
        """Update an existing entry by record_id.

        Updates last_updated_at, appends to contributors (deduplicated),
        and overwrites specified fields.
        """
        file_path = self.find_by_record_id(record_id)
        if file_path is None:
            return None

        raw = file_path.read_text()
        parts = raw.split("---\n", 2)
        if len(parts) < 3:
            return None

        fm = yaml.safe_load(parts[1]) or {}
        body = parts[2].lstrip("\n")

        # Update last_updated_at
        fm["last_updated_at"] = datetime.now(timezone.utc).isoformat()

        # Update tldr if provided
        if tldr is not None:
            fm["tldr"] = tldr

        # Append contributor (deduplicated)
        if contributor is not None:
            contributors = fm.get("contributors", [])
            if not isinstance(contributors, list):
                contributors = [contributors] if contributors else []
            if contributor not in contributors:
                contributors.append(contributor)
            fm["contributors"] = contributors

        # Update optional metadata fields
        if tags is not None:
            fm["tags"] = tags
        if subject_projects is not None:
            fm["subject_projects"] = subject_projects
        if related_records is not None:
            fm["related_records"] = related_records
        if derived_from is not None:
            fm["derived_from"] = derived_from

        # Update content body if provided
        if content is not None:
            body = content

        frontmatter = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        file_path.write_text(f"---\n{frontmatter}\n---\n\n{body}")
        return file_path

    @staticmethod
    def _safe_path_segment(value: str) -> str:
        return re.sub(r'[^a-zA-Z0-9_-]', '-', value).strip('-')

    def _project_from_frontmatter(self, file_path: Path) -> str:
        """Extract project name from file frontmatter, defaulting to _global."""
        metadata = self._parse_frontmatter_metadata(file_path.read_text())
        if metadata is None:
            return _DEFAULT_PROJECT
        return str(metadata.get("source_project") or metadata.get("project") or _DEFAULT_PROJECT)

    def _index_doc_info(self, file_path: Path, *, record_type: str) -> dict[str, str]:
        rel = file_path.relative_to(self.data_dir).as_posix()
        return {
            "doc_id": rel,
            "path": rel,
            "record_type": record_type,
            "checksum": self._sha256(file_path),
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"sha256:{digest}"
