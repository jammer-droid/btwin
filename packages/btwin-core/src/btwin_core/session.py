"""Session management for B-TWIN conversations."""

import logging

from btwin_core.models import Session

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self) -> None:
        self._session: Session | None = None

    @property
    def current_session(self) -> Session | None:
        return self._session

    def has_active_session(self) -> bool:
        return self._session is not None

    def start_session(
        self,
        topic: str | None = None,
        *,
        locale: dict[str, str] | None = None,
    ) -> Session:
        """Start a new session, ending any existing one."""
        if self._session is not None:
            logger.warning("Overwriting active session (had %d messages)", len(self._session.messages))
        self._session = Session(topic=topic, locale=dict(locale or {}))
        return self._session

    def add_message(self, role: str, content: str, *, locale: dict[str, str] | None = None) -> None:
        """Add a message to the current session. Creates session if none exists."""
        if self._session is None:
            self.start_session(locale=locale)
        self._session.add_message(role, content)

    def end_session(self) -> Session | None:
        """End the current session and return it."""
        session = self._session
        self._session = None
        return session

    def get_conversation(self) -> list[dict[str, str]]:
        """Get the current session's conversation as LLM-compatible messages."""
        if self._session is None:
            return []
        return self._session.to_llm_messages()
