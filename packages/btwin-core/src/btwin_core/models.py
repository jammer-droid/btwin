"""Data models for B-TWIN."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Session(BaseModel):
    topic: str | None = None
    messages: list[Message] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    locale: dict[str, str] = Field(default_factory=dict)

    def add_message(self, role: str, content: str) -> None:
        self.messages.append(Message(role=role, content=content))

    def to_llm_messages(self) -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in self.messages]


class Entry(BaseModel):
    date: str  # YYYY-MM-DD
    slug: str
    content: str
    metadata: dict[str, object] = Field(default_factory=dict)
