"""Tests für Config-Flow-Hilfslogik."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import voluptuous as vol

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_API_TOKEN, CONF_HOST

from custom_components.pulse.api import PulseAuthError, PulseConnectionError
from custom_components.pulse.config_flow import (
    PulseConfigFlow,
    PulseOptionsFlow,
    ValidationResult,
    entry_unique_id,
    normalize_url,
)
from custom_components.pulse.const import (
    CONF_IGNORED_RISK_CODES,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from custom_components.pulse.coordinator import PulseDataUpdateCoordinator, normalize_state


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


def _options_flow(options: dict, data=None) -> PulseOptionsFlow:
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={CONF_HOST: "https://pulse.example", CONF_API_TOKEN: "secret-token"},
        options=options,
        runtime_data=SimpleNamespace(data=data),
    )
    return PulseOptionsFlow(entry)


@pytest.mark.asyncio
async def test_risk_code_options_use_pulse_summaries(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_HOST: "https://pulse.example",
            CONF_API_TOKEN: "secret-token",
            CONF_VERIFY_SSL: True,
            CONF_SCAN_INTERVAL: 60,
        },
        options={CONF_IGNORED_RISK_CODES: ["altes_risiko"]},
    )
    entry.add_to_hass(hass)
    coordinator = PulseDataUpdateCoordinator(hass, entry)
    coordinator.data = normalize_state(
        {
            "resources": [
                {
                    "id": "res-host",
                    "type": "agent",
                    "status": "degraded",
                    "canonicalIdentity": {"primaryId": "host-1", "aliases": []},
                },
                {
                    "id": "res-pool",
                    "type": "storage",
                    "status": "degraded",
                    "parentId": "res-host",
                    "canonicalIdentity": {"primaryId": "pool-1", "aliases": []},
                    "storage": {
                        "type": "unraid-array",
                        "risk": {
                            "reasons": [
                                {
                                    "code": "unraid_no_parity",
                                    "severity": "warning",
                                    "summary": "Unraid array is running without parity protection",
                                }
                            ]
                        },
                    },
                },
            ],
            "activeAlerts": [],
        }
    )
    entry.runtime_data = coordinator

    # Der Array-Schatten bekommt keine Entity, trägt aber genau das Risiko.
    assert "pool-1" in coordinator.data.hidden_storages
    assert PulseOptionsFlow(entry)._risk_code_options() == [
        {"value": "altes_risiko", "label": "altes_risiko"},
        {"value": "unraid_no_parity", "label": "Unraid array is running without parity protection"},
    ]


@pytest.mark.asyncio
async def test_options_keep_ignored_risk_codes_when_the_field_is_absent() -> None:
    """Ohne Risiko-Gründe im Payload fehlt das Feld — die Abwahl muss bleiben."""

    flow = _options_flow({CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]})
    flow._risk_code_options = lambda: []

    result = await flow.async_step_init({CONF_SCAN_INTERVAL: 60})

    assert result["data"][CONF_IGNORED_RISK_CODES] == ["unraid_no_parity"]
