"""Compatibility wrapper until LLMClient moves into btwin-core."""

from btwin.core.llm import LLMClient, SYSTEM_PROMPT, completion

__all__ = ["LLMClient", "SYSTEM_PROMPT", "completion"]
