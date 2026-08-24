"""Tests für sichere Diagnostics."""

from __future__ import annotations

from datetime import timedelta
import json
from types import SimpleNamespace

import pytest

from custom_components.pulse.coordinator import normalize_state
from custom_components.pulse.diagnostics import async_get_config_entry_diagnostics


@pytest.mark.asyncio
async def test_diagnostics_do_not_expose_fixture_identifiers(fixture_state: dict) -> None:
    fixture_state = _with_sensitive_values(fixture_state)
    data = normalize_state(fixture_state)
    coordinator = SimpleNamespace(data=data, update_interval=timedelta(seconds=60))
    entry = SimpleNamespace(runtime_data=coordinator, data={"api_token": "diagnostic-secret-token"})

    diagnostics = await async_get_config_entry_diagnostics(SimpleNamespace(), entry)
    serialized = json.dumps(diagnostics, sort_keys=True)

    assert "secret-token" not in serialized
    for sensitive in _sensitive_fixture_strings(fixture_state):
        assert sensitive not in serialized


def _sensitive_fixture_strings(fixture_state: dict) -> set[str]:
    values: set[str] = set()
    for resource in fixture_state["resources"]:
        for key in ("id", "name", "displayName", "parentId", "parentName"):
            value = resource.get(key)
            if isinstance(value, str):
                values.add(value)
        canonical = resource.get("canonicalIdentity", {})
        primary_id = canonical.get("primaryId")
        if isinstance(primary_id, str):
            values.add(primary_id)
        for alias in canonical.get("aliases", []):
            if isinstance(alias, str):
                values.add(alias)
    for alert in fixture_state["activeAlerts"]:
        for key in ("id", "resourceId", "resourceName", "message"):
            value = alert.get(key)
            if isinstance(value, str):
                values.add(value)
    values.update({"diagnostic-secret-token", "real-hostname", "192.0.2.44", "/mnt/user/private", "secret-tag"})
    return values


def _with_sensitive_values(fixture_state: dict) -> dict:
    payload = json.loads(json.dumps(fixture_state))
    payload["resources"][0]["identity"] = {"hostname": "real-hostname", "ips": ["192.0.2.44"]}
    payload["resources"][0]["path"] = "/mnt/user/private"
    payload["resources"][0]["tags"] = ["secret-tag"]
    payload["activeAlerts"][0]["message"] = "private alert text"
    return payload
