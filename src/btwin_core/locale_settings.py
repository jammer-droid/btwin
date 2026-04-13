"""Locale settings storage for B-TWIN."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class LocaleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)

    ui_locale: StrictStr = Field(default="en")
    time_locale: StrictStr = Field(default="en-US")
    agent_response_locale: StrictStr = Field(default="en")
    timezone: StrictStr = Field(default="UTC")

    @field_validator("ui_locale", "time_locale", "agent_response_locale", "timezone")
    @classmethod
    def _strip_and_require_value(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("locale settings must not be empty")
        return cleaned


class LocaleSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ui_locale: StrictStr | None = None
    time_locale: StrictStr | None = None
    agent_response_locale: StrictStr | None = None
    timezone: StrictStr | None = None

    @field_validator("ui_locale", "time_locale", "agent_response_locale", "timezone")
    @classmethod
    def _strip_optional_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("locale settings must not be empty")
        return cleaned


class LocaleSettingsStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.file_path = data_dir / "settings" / "locale.json"

    def read(self) -> LocaleSettings:
        if not self.file_path.exists():
            return LocaleSettings()

        raw = self.file_path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return LocaleSettings.model_validate(data)

    def write(self, settings: LocaleSettings) -> LocaleSettings:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(settings.model_dump(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        tmp_path = self.file_path.with_suffix(self.file_path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.file_path)
        return settings

    def update(self, patch: Mapping[str, object] | LocaleSettingsPatch) -> LocaleSettings:
        patch_model = patch if isinstance(patch, LocaleSettingsPatch) else LocaleSettingsPatch.model_validate(patch)
        current = self.read().model_dump()
        current.update(patch_model.model_dump(exclude_unset=True))
        updated = LocaleSettings.model_validate(current)
        return self.write(updated)
