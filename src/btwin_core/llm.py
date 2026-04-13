"""LLM wrapper using LiteLLM."""

import os

from litellm import completion

from btwin_core.config import LLMConfig


SYSTEM_PROMPT = """You are B-TWIN, an AI partner that remembers the user's thoughts and helps them build on past ideas.

You have access to the user's past records. Use them to provide contextual, personalized responses.
Be conversational, warm, and insightful. Help the user see connections between their past and present thoughts.
Respond in the same language the user uses.

When there are multiple similar options or recommendations, always present them as a numbered list:
1번: [Option A] — explanation
2번: [Option B] — explanation
Clearly explain the differences or trade-offs between each option."""


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        if config.api_key:
            key_env = f"{config.provider.upper()}_API_KEY"
            os.environ[key_env] = config.api_key

    @property
    def model_string(self) -> str:
        return f"{self._config.provider}/{self._config.model}"

    def build_messages(
        self,
        system_prompt: str,
        conversation: list[dict[str, str]],
        context: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Build the full message list for LLM call."""
        full_system = system_prompt
        if context:
            context_text = "\n\n".join(context)
            full_system += f"\n\n## Past Context\n\n{context_text}"
        messages = [{"role": "system", "content": full_system}]
        messages.extend(conversation)
        return messages

    def chat(
        self,
        conversation: list[dict[str, str]],
        context: list[str] | None = None,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> str:
        """Send a conversation to the LLM and return the response."""
        messages = self.build_messages(system_prompt, conversation, context)
        response = completion(
            model=self.model_string,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    def summarize(self, conversation: list[dict[str, str]]) -> str:
        """Summarize a conversation into a markdown entry."""
        summary_prompt = """Summarize the following conversation into a concise markdown entry.
Extract key topics, decisions, insights, and action items.
Use bullet points. Write in the same language as the conversation.
Do NOT include a title — just the content."""

        messages = [
            {"role": "system", "content": summary_prompt},
            {"role": "user", "content": self._format_conversation(conversation)},
        ]
        response = completion(
            model=self.model_string,
            messages=messages,
        )
        return response.choices[0].message.content or ""

    def summarize_thread(self, content: str, topic: str, protocol: str) -> str:
        """Summarize a thread discussion into a structured summary."""
        prompt = f"""Summarize this thread discussion into a structured summary.
The thread topic is: {topic}
The protocol used is: {protocol}

Include:
- Key points discussed
- Agreements reached
- Decision (if any)
- Open questions or next steps

Write in the same language as the discussion."""

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content},
        ]
        response = completion(model=self.model_string, messages=messages)
        return response.choices[0].message.content or ""

    def generate_slug(self, conversation: list[dict[str, str]]) -> str:
        """Generate a short filename slug from a conversation."""
        slug_prompt = """Given the following conversation, generate a short kebab-case slug (2-4 words, English only) that captures the main topic.
Return ONLY the slug, nothing else. Example: "unreal-material-study" or "career-ta-transition"."""

        messages = [
            {"role": "system", "content": slug_prompt},
            {"role": "user", "content": self._format_conversation(conversation)},
        ]
        response = completion(
            model=self.model_string,
            messages=messages,
        )
        slug = (response.choices[0].message.content or "").strip().lower()
        slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
        slug = slug.strip("-")
        return slug or "untitled"

    @staticmethod
    def _format_conversation(conversation: list[dict[str, str]]) -> str:
        lines = []
        for msg in conversation:
            role = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"**{role}:** {msg['content']}")
        return "\n\n".join(lines)


__all__ = ["LLMClient", "SYSTEM_PROMPT", "completion"]
