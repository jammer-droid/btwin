"""Repo-local helper overlay bootstrap for B-TWIN-managed Codex sessions."""

from __future__ import annotations

import json
import shlex
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


class HelperOverlayBootstrapError(RuntimeError):
    """Raised when helper overlay semantics cannot be guaranteed."""


@dataclass(frozen=True)
class HelperOverlayPaths:
    repo_root: Path
    overlay_root: Path
    launch_cwd: Path

    @property
    def agents_path(self) -> Path:
        return self.overlay_root / "AGENTS.md"

    @property
    def hooks_path(self) -> Path:
        return self.overlay_root / ".codex" / "hooks.json"


def discover_git_repo_root(workspace_root: Path) -> Path | None:
    """Return the enclosing git repo root for a workspace, if any."""
    resolved_workspace = workspace_root.expanduser().resolve()
    for candidate in (resolved_workspace, *resolved_workspace.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def codex_global_config_path() -> Path:
    """Return the global Codex config path used for project trust lookups."""
    return Path.home() / ".codex" / "config.toml"


def discover_codex_project_trust_level(
    repo_root: Path,
    *,
    config_path: Path | None = None,
) -> str | None:
    """Return the most-specific configured Codex trust level for a repo path."""
    resolved_repo_root = repo_root.expanduser().resolve()
    active_config_path = config_path or codex_global_config_path()
    if not active_config_path.exists():
        return None

    try:
        config = tomllib.loads(active_config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None

    projects = config.get("projects")
    if not isinstance(projects, dict):
        return None

    best_match_length = -1
    best_trust_level: str | None = None
    for raw_path, project_config in projects.items():
        if not isinstance(raw_path, str) or not isinstance(project_config, dict):
            continue
        try:
            configured_path = Path(raw_path).expanduser().resolve()
        except OSError:
            continue
        if resolved_repo_root != configured_path and configured_path not in resolved_repo_root.parents:
            continue
        trust_level = project_config.get("trust_level")
        if not isinstance(trust_level, str):
            continue
        match_length = len(configured_path.parts)
        if match_length > best_match_length:
            best_match_length = match_length
            best_trust_level = trust_level
    return best_trust_level


def helper_overlay_agent_dirname(agent_name: str) -> str:
    """Sanitize agent names so overlay paths stay filesystem-safe."""
    sanitized = "".join(
        character if character.isalnum() or character in {"-", "_", "."} else "_"
        for character in agent_name
    ).strip("._")
    return sanitized or "agent"


def derive_helper_overlay_paths(*, agent_name: str, workspace_root: Path) -> HelperOverlayPaths:
    """Build the stable helper overlay paths for a managed agent launch."""
    requested_workspace = workspace_root.expanduser().resolve()
    repo_root = discover_git_repo_root(requested_workspace)
    if repo_root is None:
        raise HelperOverlayBootstrapError(
            "Helper overlay preflight requires a workspace inside a git repo: "
            f"{requested_workspace} is not inside a git repo."
        )
    trust_level = discover_codex_project_trust_level(repo_root)
    if trust_level != "trusted":
        raise HelperOverlayBootstrapError(
            "Helper overlay preflight requires a trusted Codex project: "
            f"{repo_root} is not trusted in {codex_global_config_path()}."
        )

    overlay_root = repo_root / ".btwin" / "helpers" / helper_overlay_agent_dirname(agent_name)
    return HelperOverlayPaths(
        repo_root=repo_root,
        overlay_root=overlay_root,
        launch_cwd=overlay_root / "workspace",
    )


def materialize_helper_overlay(paths: HelperOverlayPaths) -> Path:
    """Create the helper overlay files without touching user repo files."""
    paths.launch_cwd.mkdir(parents=True, exist_ok=True)
    _write_helper_agents(paths.agents_path)
    _write_helper_hooks(paths.hooks_path)
    return paths.launch_cwd


def _write_helper_agents(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# B-TWIN Helper Overlay",
                "",
                "This is a B-TWIN-managed helper workspace layered inside the user repository.",
                "Keep existing repository guidance from ancestor AGENTS.md files in effect.",
                "Do not modify user hook or AGENTS files from this overlay.",
                "Thread-specific context is injected dynamically by the B-TWIN runtime.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_helper_hooks(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    hook_command = f"{shlex.quote(sys.executable)} -m btwin_cli.main workflow hook"
    hook_names = ("SessionStart", "UserPromptSubmit", "Stop")
    payload = {
        "hooks": {
            hook_name: [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": hook_command, "timeout": 10}],
                }
            ]
            for hook_name in hook_names
        }
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
