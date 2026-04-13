"""B-TWIN core integration class."""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from btwin_core.config import BTwinConfig
from btwin_core.indexer import CoreIndexer
from btwin_core.indexer_models import RecordType
from btwin_core.llm import LLMClient
from btwin_core.models import Entry
from btwin_core.session import SessionManager
from btwin_core.locale_settings import LocaleSettingsStore

logger = logging.getLogger(__name__)


class BTwin:
    def __init__(self, config: BTwinConfig, *, indexer: CoreIndexer | None = None) -> None:
        self.config = config
        self.indexer = indexer or CoreIndexer(data_dir=config.data_dir)
        self.storage = self.indexer.storage
        self.vector_store = self.indexer.vector_store
        self.session_manager = SessionManager()
        self._locale_settings_store = LocaleSettingsStore(config.data_dir)

        # LLM is optional — only needed for CLI standalone mode
        self._llm: LLMClient | None = None
        if config.llm.api_key:
            self._llm = LLMClient(config.llm)

    def start_session(self, topic: str | None = None) -> dict:
        """Start a new session with an optional topic."""
        locale = self._current_locale_settings()
        session = self.session_manager.start_session(topic=topic, locale=locale)
        return {
            "active": True,
            "topic": session.topic,
            "created_at": session.created_at.isoformat(),
            "locale": dict(session.locale),
        }

    def end_session(
        self,
        summary: str | None = None,
        slug: str | None = None,
        *,
        project: str | None = None,
        tags: list[str] | None = None,
        subject_projects: list[str] | None = None,
        tldr: str = "",
        contributors: list[str] | None = None,
    ) -> dict | None:
        """End the current session and save as an entry.

        Args:
            summary: Session summary text. If not provided, uses LLM (requires API key)
                     or falls back to raw message log.
            slug: Filename slug. If not provided, uses LLM or defaults to topic/timestamp.
        """
        session = self.session_manager.current_session
        if session is None:
            return None

        # Generate summary if not provided
        if summary is None:
            conversation = session.to_llm_messages()
            if self._llm:
                try:
                    summary = self._llm.summarize(conversation)
                except Exception:
                    logger.warning("LLM summarize failed, using raw messages")
                    summary = self._raw_summary(session)
            else:
                summary = self._raw_summary(session)

        # Generate slug if not provided
        if slug is None:
            if self._llm:
                try:
                    conversation = session.to_llm_messages()
                    slug = self._llm.generate_slug(conversation)
                except Exception:
                    slug = self._fallback_slug(session)
            else:
                slug = self._fallback_slug(session)

        self.session_manager.end_session()
        now = datetime.now(timezone.utc)
        date = now.strftime("%Y-%m-%d")

        title = slug.replace("-", " ").title()
        content = f"# {title}\n\n{summary}"

        metadata: dict[str, object] = {
            "topic": session.topic or "",
            "created_at": now.isoformat(),
        }
        if tags:
            metadata["tags"] = tags
        if subject_projects:
            metadata["subject_projects"] = subject_projects
        if tldr:
            metadata["tldr"] = tldr
        if contributors:
            metadata["contributors"] = contributors

        entry = Entry(
            date=date,
            slug=slug,
            content=content,
            metadata=metadata,
        )
        saved_path = self.storage.save_entry(entry, project=project)
        self._index_file(saved_path, record_type="entry", project=project)

        try:
            self._update_summary(date, slug, content)
        except Exception:
            logger.warning("Failed to update summary.md", exc_info=True)
        return {"date": date, "slug": slug, "summary": summary}

    def chat(self, message: str) -> str:
        """Send a message and get a response. Requires LLM API key."""
        if not self._llm:
            raise RuntimeError("LLM API key required for chat. Use MCP client instead.")

        self.session_manager.add_message("user", message, locale=self._current_locale_settings())

        context = []
        search_results = self.vector_store.search(message, n_results=3)
        for result in search_results:
            context.append(result["content"])

        conversation = self.session_manager.get_conversation()
        response = self._llm.chat(conversation, context=context if context else None)

        self.session_manager.add_message("assistant", response)
        return response

    def search(
        self,
        query: str,
        n_results: int = 5,
        filters: dict[str, str] | None = None,
        *,
        hybrid: bool = True,
        lexical_weight: float = 0.4,
        recency_half_life_days: float = 30.0,
        mmr_lambda: float = 0.75,
        project: str | None = None,
    ) -> list[dict]:
        """Search past entries by semantic similarity with optional metadata filters."""
        metadata_filters = filters
        if project is not None:
            metadata_filters = dict(metadata_filters) if metadata_filters else {}
            metadata_filters["project"] = project
        return self.vector_store.search(
            query,
            n_results=n_results,
            metadata_filters=metadata_filters,
            hybrid=hybrid,
            lexical_weight=lexical_weight,
            recency_half_life_days=recency_half_life_days,
            mmr_lambda=mmr_lambda,
        )

    def record_convo(
        self,
        content: str,
        requested_by_user: bool = False,
        topic: str | None = None,
        *,
        project: str | None = None,
        tags: list[str] | None = None,
        subject_projects: list[str] | None = None,
        tldr: str = "",
        contributors: list[str] | None = None,
    ) -> dict:
        """Record explicit user conversation memory under convo namespace."""
        entry = self.storage.save_convo_record(
            content=content,
            requested_by_user=requested_by_user,
            topic=topic,
            project=project,
            tags=tags,
            subject_projects=subject_projects,
            tldr=tldr,
            contributors=contributors,
        )
        # Find the saved file using record_id from metadata
        record_id = str(entry.metadata.get("record_id", ""))
        path = self.storage.entries_dir / "convo" / entry.date / f"{record_id}.md"
        self._index_file(path, record_type="convo", project=project)

        return {"date": entry.date, "slug": entry.slug, "path": str(path)}

    def record(
        self,
        content: str,
        topic: str | None = None,
        *,
        project: str | None = None,
        tags: list[str] | None = None,
        subject_projects: list[str] | None = None,
        tldr: str = "",
        contributors: list[str] | None = None,
    ) -> dict:
        """Manually record a note."""
        now = datetime.now(timezone.utc)
        date = now.strftime("%Y-%m-%d")
        base_slug = topic or "note"
        slug = f"{base_slug}-{now.strftime('%H%M%S%f')}"

        metadata: dict[str, object] = {
            "created_at": now.isoformat(),
        }
        if contributors:
            metadata["contributors"] = contributors
        if tags:
            metadata["tags"] = tags
        if subject_projects:
            metadata["subject_projects"] = subject_projects
        if tldr:
            metadata["tldr"] = tldr

        entry = Entry(
            date=date,
            slug=slug,
            content=content,
            metadata=metadata,
        )
        saved_path = self.storage.save_entry(entry, project=project)

        self._index_file(saved_path, record_type="entry", project=project)

        try:
            self._update_summary(date, slug, content)
        except Exception:
            logger.warning("Failed to update summary.md", exc_info=True)
        return {"date": date, "slug": slug, "path": str(saved_path)}

    def import_entry(
        self,
        content: str,
        date: str,
        slug: str,
        tags: list[str] | None = None,
        source_path: str | None = None,
        *,
        project: str | None = None,
        tldr: str = "",
    ) -> dict:
        """Import a single entry with explicit date, slug, and tags."""
        metadata: dict[str, object] = {}
        if tags:
            metadata["tags"] = tags
        if source_path:
            metadata["source_path"] = source_path
        if tldr:
            metadata["tldr"] = tldr
        metadata["imported_at"] = datetime.now(timezone.utc).isoformat()

        entry = Entry(
            date=date,
            slug=slug,
            content=content,
            metadata=metadata,
        )
        saved_path = self.storage.save_entry(entry, project=project)

        self._index_file(saved_path, record_type="entry", project=project)

        try:
            self._update_summary(date, slug, content)
        except Exception:
            logger.warning("Failed to update summary for %s/%s", date, slug)

        return {"date": date, "slug": slug, "path": str(saved_path)}

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
    ) -> dict:
        """Update an existing entry by record_id."""
        path = self.storage.update_entry(
            record_id=record_id,
            content=content,
            tldr=tldr,
            tags=tags,
            subject_projects=subject_projects,
            related_records=related_records,
            derived_from=derived_from,
            contributor=contributor,
        )
        if path is None:
            return {"ok": False, "error": "record_not_found", "record_id": record_id}

        # Re-index after update — read record_type from frontmatter
        rel = path.relative_to(self.config.data_dir).as_posix()
        checksum = self._checksum(path)
        fm = self.storage._parse_frontmatter_metadata(path.read_text())
        rt = (fm or {}).get("record_type") or (fm or {}).get("recordType") or "entry"
        self.indexer.mark_pending(
            doc_id=rel,
            path=rel,
            record_type=rt,
            checksum=checksum,
        )
        result = self.indexer.repair(rel)
        if not result.get("ok"):
            logger.warning("Failed to re-index document %s: %s", rel, result)

        return {"ok": True, "record_id": record_id, "path": str(path)}

    def get_entry(self, record_id: str) -> dict | None:
        """Retrieve a full entry by record_id.

        Returns dict with record_id, content (full markdown body),
        frontmatter metadata, and file_path. Returns None if not found.
        """
        path = self.storage.find_by_record_id(record_id)
        if path is None:
            return None

        raw = path.read_text(encoding="utf-8")
        fm = self.storage._parse_frontmatter_metadata(raw)
        parts = raw.split("---\n", 2)
        body = parts[2].lstrip("\n") if len(parts) >= 3 else raw

        return {
            "record_id": record_id,
            "content": body,
            "frontmatter": fm or {},
            "file_path": str(path),
        }

    def session_status(self) -> dict:
        """Get the current session status."""
        session = self.session_manager.current_session
        if session is None:
            return {
                "active": False,
                "locale": self._current_locale_settings(),
            }
        return {
            "active": True,
            "topic": session.topic,
            "message_count": len(session.messages),
            "created_at": session.created_at.isoformat(),
            "locale": dict(session.locale) if session.locale else self._current_locale_settings(),
        }

    def _index_file(self, path: Path, *, record_type: RecordType, project: str | None = None) -> None:
        """Mark file for indexing and perform best-effort targeted indexing."""
        rel = path.relative_to(self.config.data_dir).as_posix()
        checksum = self._checksum(path)
        self.indexer.mark_pending(
            doc_id=rel,
            path=rel,
            record_type=record_type,
            checksum=checksum,
            project=project,
        )
        result = self.indexer.repair(rel)
        if not result.get("ok"):
            logger.warning("Failed to index document %s: %s", rel, result)

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return f"sha256:{digest}"

    def _update_summary(self, date: str, slug: str, content: str) -> None:
        """Append an entry summary to the cumulative summary.md file."""
        summary_path = self.config.data_dir / "summary.md"

        # Extract first line of content as preview (strip markdown heading)
        preview = content.strip().split("\n")[0].lstrip("# ").strip()
        if len(preview) > 80:
            preview = preview[:77] + "..."

        new_line = f"- **{slug}**: {preview}"

        if summary_path.exists():
            existing = summary_path.read_text()
        else:
            existing = "# B-TWIN Summary\n"

        # Check if today's date section exists
        date_header = f"## {date}"
        if date_header in existing:
            # Insert new line after the date header
            parts = existing.split(date_header, 1)
            parts[1] = f"\n\n{new_line}" + parts[1]
            updated = date_header.join(parts)
        else:
            # Add new date section after the title
            nl_pos = existing.find("\n")
            header_end = nl_pos + 1 if nl_pos != -1 else len(existing)
            date_section = f"\n{date_header}\n\n{new_line}\n\n---\n"
            updated = existing[:header_end] + date_section + existing[header_end:]

        summary_path.write_text(updated)

    def _current_locale_settings(self) -> dict[str, str]:
        return self._locale_settings_store.read().model_dump()

    @staticmethod
    def _raw_summary(session) -> str:
        return "\n".join(f"- {m.content[:80]}" for m in session.messages)

    @staticmethod
    def _fallback_slug(session) -> str:
        if session.topic:
            return session.topic
        now = datetime.now(timezone.utc)
        return f"session-{now.strftime('%H%M%S')}"
