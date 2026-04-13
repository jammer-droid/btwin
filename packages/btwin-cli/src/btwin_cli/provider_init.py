"""Provider bootstrap helpers for `btwin init`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


_CODEX_PROVIDER_CONFIG = {
    "providers": [
        {
            "id": "openai",
            "name": "OpenAI",
            "cli": "codex",
            "default_args": [],
            "allow_bypass_permissions": False,
            "reasoning_arg": "--config model_reasoning_effort={level}",
            "default_model": "gpt-5.3-codex",
            "models": [
                {
                    "id": "gpt-5.4",
                    "name": "GPT-5.4",
                    "reasoning_levels": ["none", "low", "medium", "high", "xhigh"],
                },
                {
                    "id": "gpt-5.3-codex",
                    "name": "GPT-5.3 Codex",
                    "reasoning_levels": ["low", "medium", "high", "xhigh"],
                },
                {
                    "id": "gpt-5.2-codex",
                    "name": "GPT-5.2 Codex",
                    "reasoning_levels": ["low", "medium", "high", "xhigh"],
                },
            ],
        }
    ],
    "capabilities": [
        "planning",
        "code-generation",
        "code-review",
        "debugging",
        "testing",
        "documentation",
        "research",
        "refactoring",
        "conductor",
    ],
}


def available_provider_names() -> list[str]:
    return ["codex"]


def provider_display_name(provider_name: str) -> str:
    return {"codex": "Codex"}.get(provider_name, provider_name)


def validate_provider_cli(provider_name: str) -> str:
    command = {"codex": "codex"}[provider_name]
    resolved = shutil.which(command)
    if resolved is None:
        raise RuntimeError(
            f"{provider_display_name(provider_name)} CLI not found in PATH. "
            f"Install `{command}` first, then run `btwin init` again."
        )
    return resolved


def build_provider_config(provider_name: str) -> dict:
    if provider_name != "codex":
        raise ValueError(f"Unsupported provider: {provider_name}")
    return json.loads(json.dumps(_CODEX_PROVIDER_CONFIG))


def write_provider_config(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)
