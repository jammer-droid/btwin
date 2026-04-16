from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.provider_smoke


def test_provider_smoke_fixture_exports_isolated_env(provider_smoke_env) -> None:
    assert provider_smoke_env["BTWIN_API_URL"].startswith("http://127.0.0.1:")
    assert provider_smoke_env["BTWIN_DATA_DIR"]
    assert Path(provider_smoke_env["BTWIN_TEST_ROOT"]).exists()

    response = httpx.get(
        f'{provider_smoke_env["BTWIN_API_URL"]}/api/sessions/status',
        timeout=5.0,
    )
    response.raise_for_status()
    payload = response.json()
    assert "active" in payload
    assert "locale" in payload


def test_provider_smoke_fixture_uses_default_provider_profile(provider_smoke_env) -> None:
    assert provider_smoke_env["provider_surface"] == "app-server"
    assert provider_smoke_env["provider_continuity"] == "long-term"
    assert provider_smoke_env["provider_model"] == "gpt-5.4-mini"
