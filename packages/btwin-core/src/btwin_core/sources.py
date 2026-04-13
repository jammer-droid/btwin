"""Data source registry and scanning for B-TWIN dashboard."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path

import yaml


@dataclass
class DataSource:
    name: str
    path: str
    enabled: bool = True
    last_scanned_at: str | None = None
    entry_count: int = 0


class SourceRegistry:
    """Registry for global/project .btwin data sources."""

    def __init__(self, registry_path: Path) -> None:
        self.registry_path = registry_path

    @staticmethod
    def canonical_path(path: Path | str) -> Path:
        return Path(path).expanduser().resolve()

    @staticmethod
    def suggested_name(path: Path | str) -> str:
        canonical = SourceRegistry.canonical_path(path)
        if canonical.name == ".btwin" and canonical.parent.name:
            return canonical.parent.name
        return canonical.name or canonical.parent.name or "source"

    @staticmethod
    def source_id(source: DataSource | Path | str) -> str:
        raw_path = source.path if isinstance(source, DataSource) else source
        canonical = SourceRegistry.canonical_path(raw_path)
        return hashlib.sha1(str(canonical).encode("utf-8")).hexdigest()

    def load(self) -> list[DataSource]:
        if not self.registry_path.exists():
            return []
        data = yaml.safe_load(self.registry_path.read_text()) or {}
        items = data.get("sources", [])
        return [DataSource(**item) for item in items]

    def save(self, sources: list[DataSource]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"sources": [asdict(s) for s in sources]}
        self.registry_path.write_text(yaml.dump(payload, default_flow_style=False, allow_unicode=True, sort_keys=False))

    def ensure_global_default(self) -> list[DataSource]:
        sources = self.load()
        global_path = str((Path.home() / ".btwin").resolve())
        if any(self.canonical_path(s.path) == Path(global_path) for s in sources):
            return sources
        sources.append(DataSource(name="global", path=global_path, enabled=True))
        self.save(sources)
        return sources

    def add_source(self, path: Path | str, name: str | None = None, enabled: bool = True) -> DataSource:
        sources = self.load()
        canonical = self.canonical_path(path)

        for existing in sources:
            if self.canonical_path(existing.path) == canonical:
                return existing

        source = DataSource(name=name or self.suggested_name(canonical), path=str(canonical), enabled=enabled)
        sources.append(source)
        self.save(sources)
        return source

    def update_source(
        self,
        source_id: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
    ) -> DataSource | None:
        sources = self.load()

        for source in sources:
            if self.source_id(source) != source_id:
                continue
            if name is not None:
                source.name = name
            if enabled is not None:
                source.enabled = enabled
            self.save(sources)
            return source

        return None

    def enabled_sources(self) -> list[DataSource]:
        return [s for s in self.load() if s.enabled]

    @staticmethod
    def scan_for_btwin_dirs(
        roots: list[Path],
        max_depth: int = 4,
        exclude_dirs: set[str] | None = None,
    ) -> list[Path]:
        excludes = exclude_dirs or {".git", "node_modules", ".venv", "Library", "Downloads"}
        found: set[Path] = set()

        for root in roots:
            root = root.expanduser().resolve()
            if not root.exists() or not root.is_dir():
                continue

            root_depth = len(root.parts)
            for current, dirs, _files in os.walk(root):
                current_path = Path(current)
                depth = len(current_path.parts) - root_depth

                if depth >= max_depth:
                    dirs[:] = []
                    continue

                dirs[:] = [d for d in dirs if d not in excludes]

                if ".btwin" in dirs:
                    found.add((current_path / ".btwin").resolve())

        return sorted(found)

    def refresh_entry_counts(self) -> list[DataSource]:
        sources = self.load()
        now = datetime.now(timezone.utc).isoformat()

        for s in sources:
            entries_dir = Path(s.path) / "entries"
            count = 0
            if entries_dir.exists() and entries_dir.is_dir():
                top_level_dirs = [d for d in entries_dir.iterdir() if d.is_dir()]
                old_style_dirs = [d for d in top_level_dirs if d.name[:4].isdigit()]
                if old_style_dirs:
                    for date_dir in old_style_dirs:
                        count += len(list(date_dir.glob("*.md")))
                else:
                    for project_dir in top_level_dirs:
                        if project_dir.name == "global":
                            continue
                        for date_dir in project_dir.iterdir():
                            if not date_dir.is_dir():
                                continue
                            if date_dir.name in {"convo", "collab", "shared"}:
                                continue
                            count += len(list(date_dir.glob("*.md")))
            s.entry_count = count
            s.last_scanned_at = now

        self.save(sources)
        return sources
