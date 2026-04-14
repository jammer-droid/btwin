"""Helpers for the lightweight thread enter chat console."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class ThreadChatDecision:
    kind: Literal["message", "command", "empty"]
    mode: Literal["broadcast", "direct", "auto"] | None = None
    targets: list[str] = field(default_factory=list)
    content: str = ""
    command: str | None = None


def parse_thread_chat_input(raw: str) -> ThreadChatDecision:
    text = raw.strip()
    if not text:
        return ThreadChatDecision(kind="empty")

    if text.startswith("/"):
        command = text[1:].strip().lower()
        return ThreadChatDecision(kind="command", command=command or "help")

    if text.startswith("!"):
        content = text[1:].strip()
        return ThreadChatDecision(kind="message", mode="broadcast", content=content)

    if text.startswith("@"):
        remainder = text[1:].strip()
        if not remainder:
            return ThreadChatDecision(kind="empty")
        if " " in remainder:
            target, content = remainder.split(" ", 1)
        else:
            target, content = remainder, ""
        return ThreadChatDecision(
            kind="message",
            mode="direct",
            targets=[target],
            content=content.strip(),
        )

    return ThreadChatDecision(kind="message", mode="broadcast", content=text)
