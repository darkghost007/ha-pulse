"""Tests für Config-Flow-Hilfslogik."""

from __future__ import annotations

import pytest
import voluptuous as vol

from homeassistant.const import CONF_API_TOKEN, CONF_HOST

from custom_components.pulse.api import PulseAuthError, PulseConnectionError
from custom_components.pulse.config_flow import PulseConfigFlow, ValidationResult, entry_unique_id, normalize_url
from custom_components.pulse.const import CONF_SCAN_INTERVAL, CONF_VERIFY_SSL, DEFAULT_SCAN_INTERVAL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://pulse.example:7655/", "https://pulse.example:7655"),
        ("http://pulse.example:7655/base/", "http://pulse.example:7655/base"),
        ("https://[2001:db8::1]:7655/", "https://[2001:db8::1]:7655"),
    ],
)
def test_normalize_url_accepts_http_https_without_extra_parts(raw: str, expected: str) -> None:
    assert normalize_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://pulse.example",
        "https://user:pass@pulse.example",
        "https://pulse.example?token=secret",
        "https://pulse.example/#fragment",
        "https:///missing-host",
        "https://pulse.example:not-a-port",
        "http://h:70000",
    ],
)
def test_normalize_url_rejects_unsafe_forms(raw: str) -> None:
    with pytest.raises(vol.Invalid):
        normalize_url(raw)


def test_entry_unique_id_includes_default_port() -> None:
    assert entry_unique_id("https://pulse.example") == "https://pulse.example:443"
    assert entry_unique_id("http://pulse.example/base") == "http://pulse.example:80/base"


@pytest.mark.asyncio
async def test_verify_ssl_false_requires_confirmation(hass, monkeypatch: pytest.MonkeyPatch) -> None:
    flow = PulseConfigFlow()
    flow.hass = hass

    async def _validate(_hass, data):
        return ValidationResult(title="Pulse", unique_id="https://pulse.example:443")

    monkeypatch.setattr("custom_components.pulse.config_flow.validate_input", _validate)

    result = await flow.async_step_user(
        {
            CONF_HOST: "https://pulse.example",
            CONF_API_TOKEN: "secret-token",
            CONF_VERIFY_SSL: False,
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
        }
    )

    assert result["type"] == "form"
    assert result["step_id"] == "confirm_insecure_tls"


@pytest.mark.asyncio
async def test_validate_input_rejects_bad_reauth_url(hass) -> None:
    flow = PulseConfigFlow()
    flow.hass = hass
    flow._reauth_entry = type(
        "Entry",
        (),
        {"data": {CONF_HOST: "https://pulse.example:not-a-port", CONF_API_TOKEN: "old-token"}},
    )()

    result = await flow.async_step_reauth_confirm({CONF_API_TOKEN: "new-token"})

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_url"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PulseAuthError("abgelehnt"), "invalid_auth"),
        (PulseAuthError("scope", insufficient_scope=True), "insufficient_scope"),
        (PulseConnectionError("offline"), "cannot_connect"),
    ],
)
async def test_reauth_error_mapping(
    hass,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected: str,
) -> None:
    flow = PulseConfigFlow()
    flow.hass = hass
    flow._reauth_entry = type(
        "Entry",
        (),
        {"data": {CONF_HOST: "https://pulse.example", CONF_API_TOKEN: "old-token"}},
    )()

    async def _validate(_hass, _data):
        raise error

    monkeypatch.setattr("custom_components.pulse.config_flow.validate_input", _validate)

    result = await flow.async_step_reauth_confirm({CONF_API_TOKEN: "new-token"})

    assert result["type"] == "form"
    assert result["errors"] == {"base": expected}
