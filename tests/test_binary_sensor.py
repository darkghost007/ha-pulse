"""Tests für Binary-Sensor-Logik."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.pulse.binary_sensor import PulseInfrastructureProblemBinarySensor
from custom_components.pulse.const import (
    CONF_ALIAS_MAP,
    CONF_CRITICAL_HOSTS,
    CONF_CRITICAL_HOSTS_MODE,
    CONF_KNOWN_HOSTS,
    CRITICAL_MODE_SELECTED,
)
from custom_components.pulse.coordinator import normalize_state


def test_infrastructure_problem_empty_is_unknown() -> None:
    sensor = _problem_sensor(normalize_state({"resources": [], "activeAlerts": []}))
    assert sensor.is_on is None


def test_infrastructure_problem_all_online_is_off(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload["activeAlerts"] = []
    payload["resources"] = [
        {**resource, "status": "online"} if resource.get("type") == "agent" else resource
        for resource in fixture_state["resources"]
    ]
    sensor = _problem_sensor(normalize_state(payload))

    assert sensor.is_on is False


def test_infrastructure_problem_degraded_host_is_off(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload["activeAlerts"] = []
    sensor = _problem_sensor(normalize_state(payload))

    assert sensor.is_on is False
    assert sensor.extra_state_attributes["triggering_hosts"] == []


def test_infrastructure_problem_critical_alert_is_on(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload["resources"] = [
        {**resource, "status": "online"} if resource.get("type") == "agent" else resource
        for resource in fixture_state["resources"]
    ]
    sensor = _problem_sensor(normalize_state(payload))

    assert sensor.is_on is True
    assert sensor.extra_state_attributes["triggering_alerts"]


def test_infrastructure_problem_missing_alert_section_is_unknown(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload.pop("activeAlerts")
    data = normalize_state(payload)
    sensor = _problem_sensor(data)

    assert sensor.is_on is None


def test_infrastructure_problem_unparseable_offline_host_is_unknown() -> None:
    data = normalize_state(
        {
            "resources": [
                {
                    "id": "res-1",
                    "type": "agent",
                    "status": "online",
                    "canonicalIdentity": {"primaryId": "host:1", "aliases": []},
                },
                {
                    "id": "res-2",
                    "type": "agent",
                    "status": "offline",
                    "canonicalIdentity": {"aliases": []},
                },
            ],
            "activeAlerts": [],
        }
    )
    sensor = _problem_sensor(data)

    assert "resources" in data.stale
    assert sensor.is_on is None


def test_infrastructure_problem_uses_persisted_known_hosts_after_restart() -> None:
    data = normalize_state({"resources": [], "activeAlerts": []})
    sensor = _problem_sensor(data, options={CONF_KNOWN_HOSTS: ["agent:missing"]})

    assert sensor.is_on is True
    assert sensor.extra_state_attributes["triggering_hosts"] == ["agent:missing · nicht gemeldet"]


def test_infrastructure_problem_remaps_persisted_known_hosts() -> None:
    data = normalize_state({"resources": [], "activeAlerts": []})
    sensor = _problem_sensor(
        data,
        options={
            CONF_ALIAS_MAP: {"old-id": "new-id"},
            CONF_KNOWN_HOSTS: ["old-id"],
        },
    )

    assert sensor.is_on is True
    assert sensor.extra_state_attributes["triggering_hosts"] == ["new-id · nicht gemeldet"]


def test_acknowledged_critical_alert_still_counts(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload["resources"] = [
        {**resource, "status": "online"} if resource.get("type") == "agent" else resource
        for resource in fixture_state["resources"]
    ]
    payload["activeAlerts"] = [{**fixture_state["activeAlerts"][0], "acknowledged": True}]
    sensor = _problem_sensor(normalize_state(payload))

    assert sensor.is_on is True


def test_critical_alert_counts_with_empty_selected_hosts(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload["resources"] = [
        {**resource, "status": "online"} if resource.get("type") == "agent" else resource
        for resource in fixture_state["resources"]
    ]
    sensor = _problem_sensor(
        normalize_state(payload),
        options={CONF_CRITICAL_HOSTS_MODE: CRITICAL_MODE_SELECTED, CONF_CRITICAL_HOSTS: []},
    )

    assert sensor.is_on is True


def _problem_sensor(data, options=None):
    entry = SimpleNamespace(entry_id="entry-1", data={}, options=options or {})
    coordinator = SimpleNamespace(
        config_entry=entry,
        data=data,
        last_update_success=True,
        async_add_listener=lambda _listener: (lambda: None),
    )
    return PulseInfrastructureProblemBinarySensor(coordinator)
