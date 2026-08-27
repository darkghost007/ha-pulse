"""Akzeptanztests für Pulse-Entities und Coordinator-Verhalten."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE, UnitOfDataRate
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pulse import binary_sensor, sensor
from custom_components.pulse.const import (
    CONF_ALIAS_MAP,
    CONF_IGNORED_RISK_CODES,
    CONF_INCLUDE_CONTAINERS,
    CONF_KNOWN_HOSTS,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.pulse.coordinator import (
    PulseDataUpdateCoordinator,
    async_cleanup_removed_resources,
    _migrate_resource_unique_ids,
    normalize_state,
)
from custom_components.pulse.sensor import (
    OVERALL_STATUS_PROBLEM,
    OVERALL_STATUS_WARNING,
    PulseGuestSensor,
    PulseHostSensor,
    PulseHostUptimeSensor,
    PulseStorageSensor,
)


@pytest.mark.asyncio
async def test_primary_id_change_removes_old_host_with_real_ha_setup(
    hass,
    enable_custom_integrations,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = [_host_payload("old-id", aliases=[]), _host_payload("new-id", aliases=["old-id"])]
    latest_payload = payloads[-1]

    async def _get_state(_client):
        if payloads:
            return payloads.pop(0)
        return latest_payload

    monkeypatch.setattr("custom_components.pulse.api.PulseApiClient.async_get_state", _get_state)
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-real",
        data={
            CONF_HOST: "https://pulse.example",
            "api_token": "secret-token",
            CONF_VERIFY_SSL: True,
            CONF_SCAN_INTERVAL: 60,
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    old_entity_id = "binary_sensor.host_old_online"
    assert hass.states.get(old_entity_id) is not None
    assert hass.states.get(old_entity_id).state != STATE_UNAVAILABLE

    await entry.runtime_data.async_request_refresh()
    await hass.async_block_till_done()
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    entities = [
        entity
        for entity in entity_registry.entities.values()
        if entity.config_entry_id == entry.entry_id and entity.platform == "pulse"
    ]
    online_entities = [
        entity
        for entity in entities
        if entity.domain == "binary_sensor" and entity.unique_id.endswith("_online")
    ]
    devices = list(device_registry.devices.values())

    assert len(online_entities) == 1
    assert online_entities[0].entity_id == old_entity_id
    assert online_entities[0].unique_id == "entry-real_new-id_online"
    assert not any("old-id" in entity.unique_id for entity in entities)
    assert entity_registry.async_get_entity_id("binary_sensor", DOMAIN, "entry-real_old-id_online") is None
    assert device_registry.async_get_device({(DOMAIN, "entry-real_old-id")}) is None
    assert device_registry.async_get_device({(DOMAIN, "entry-real_new-id")}) is not None
    assert "old-id" not in entry.options.get(CONF_KNOWN_HOSTS, [])
    assert entry.options.get(CONF_KNOWN_HOSTS) == ["new-id"]
    assert entry.options.get(CONF_ALIAS_MAP) == {"old-id": "new-id"}
    assert not any((DOMAIN, "entry-real_old-id") in device.identifiers for device in devices)


@pytest.mark.asyncio
async def test_entity_counts_and_metadata_from_fixture(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry(options={CONF_KNOWN_HOSTS: sorted(data.hosts)})
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)
    await binary_sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)

    assert len(added) == 78
    assert len({entity.unique_id for entity in added}) == 78

    by_unique = {entity.unique_id: entity for entity in added}
    host_id = next(iter(data.hosts))
    cpu = by_unique[f"entry-1_{host_id}_cpu_usage"]
    assert cpu.native_unit_of_measurement == "%"
    assert cpu.state_class is SensorStateClass.MEASUREMENT
    assert cpu.device_class is None

    uptime = by_unique[f"entry-1_{host_id}_uptime"]
    assert uptime.device_class is SensorDeviceClass.UPTIME
    assert uptime.native_value.tzinfo is UTC

    online = by_unique[f"entry-1_{host_id}_online"]
    assert online.device_class is BinarySensorDeviceClass.CONNECTIVITY
    assert online.available is True

    status = by_unique[f"entry-1_{host_id}_status"]
    assert status.entity_category is EntityCategory.DIAGNOSTIC
    health = by_unique[f"entry-1_{host_id}_health"]
    assert health.device_class is SensorDeviceClass.ENUM
    assert health.entity_category is None

    assert not any(entity.unique_id.endswith("_used") for entity in added if "canon-37" in entity.unique_id)
    assert not any(entity.unique_id.endswith("_total") for entity in added if "canon-41" in entity.unique_id)
    assert not any("_canon-37_" in entity.unique_id for entity in added)
    assert not any("_canon-41_" in entity.unique_id for entity in added)


@pytest.mark.asyncio
async def test_restart_while_known_host_missing_creates_online_off_entity(fixture_state: dict) -> None:
    data = normalize_state({"resources": [], "activeAlerts": []})
    entry = _entry(options={CONF_KNOWN_HOSTS: ["agent:missing"]})
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await binary_sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)

    online = next(entity for entity in added if entity.unique_id == "entry-1_agent:missing_online")
    assert online.available is True
    assert online.is_on is False


@pytest.mark.asyncio
async def test_include_docker_containers_changes_entity_count(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry(options={CONF_KNOWN_HOSTS: sorted(data.hosts), CONF_INCLUDE_CONTAINERS: True})
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)
    await binary_sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)

    assert len(added) == 90
    assert sum(1 for entity in added if "_canon-10_" in entity.unique_id) == 4


def test_stale_resources_make_resource_entities_unavailable_and_summaries_unknown() -> None:
    data = normalize_state({"activeAlerts": []})
    entry = _entry(options={CONF_KNOWN_HOSTS: ["agent:missing"]})
    coordinator = _coordinator(entry, data)

    online = binary_sensor.PulseHostOnlineBinarySensor(coordinator, "agent:missing")
    host_summary = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "hosts_offline"),
    )
    active_alerts = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "active_alerts"),
    )

    assert "resources" in data.stale
    assert online.available is False
    assert online.is_on is None
    assert host_summary.native_value is None
    assert active_alerts.native_value is None


def test_stale_resources_make_all_resource_entity_types_unavailable() -> None:
    data = normalize_state(
        {
            "resources": [
                _resource("host-1", "agent", "res-host", "online"),
                _resource("guest-1", "vm", "res-guest", "running", parent_id="res-host"),
                _resource("storage-1", "storage", "res-storage", "online"),
                {"id": "broken-host", "type": "agent", "status": "offline"},
            ],
            "activeAlerts": [],
        }
    )
    entry = _entry()
    coordinator = _coordinator(entry, data)

    host = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "cpu_usage"),
    )
    guest = PulseGuestSensor(
        coordinator,
        "guest-1",
        next(description for description in sensor.GUEST_SENSOR_DESCRIPTIONS if description.key == "cpu_usage"),
    )
    storage = PulseStorageSensor(
        coordinator,
        "storage-1",
        next(description for description in sensor.STORAGE_SENSOR_DESCRIPTIONS if description.key == "usage"),
    )
    assert "resources" in data.stale
    assert host.available is False
    assert guest.available is False
    assert storage.available is False


def test_stale_alerts_make_alert_counters_unknown(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload.pop("activeAlerts")
    data = normalize_state(payload)
    entry = _entry()
    coordinator = _coordinator(entry, data)

    active_alerts = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "active_alerts"),
    )
    warnings = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "warnings"),
    )
    critical = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "critical_alerts"),
    )

    assert "alerts" in data.stale
    assert active_alerts.native_value is None
    assert warnings.native_value is None
    assert critical.native_value is None


def test_overall_status_and_warning_counters_use_degraded_as_warning(fixture_state: dict) -> None:
    payload = dict(fixture_state)
    payload["activeAlerts"] = []
    data = normalize_state(payload)
    entry = _entry(options={CONF_KNOWN_HOSTS: sorted(data.hosts)})
    coordinator = _coordinator(entry, data)
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )
    warnings = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "warnings"),
    )
    critical = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "critical_alerts"),
    )

    assert overall.native_value == OVERALL_STATUS_WARNING
    assert warnings.native_value == 1
    assert critical.native_value == 0
    assert overall.extra_state_attributes["triggering_hosts"] == ["host-1 · beeinträchtigt"]


def test_overall_status_problem_attributes_include_critical_alerts(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry(options={CONF_KNOWN_HOSTS: sorted(data.hosts)})
    coordinator = _coordinator(entry, data)
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )
    warnings = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "warnings"),
    )
    critical = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "critical_alerts"),
    )

    assert overall.native_value == OVERALL_STATUS_PROBLEM
    assert warnings.native_value == 1
    assert critical.native_value == 4
    assert len(overall.extra_state_attributes["triggering_alerts"]) == 4


def test_host_counters_exist_without_container_entities(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry(options={CONF_KNOWN_HOSTS: sorted(data.hosts), CONF_INCLUDE_CONTAINERS: False})
    coordinator = _coordinator(entry, data)
    host_id = "canon-57"

    values = {}
    for key in (
        "containers_running",
        "containers_stopped",
        "container_problems",
        "guests_running",
        "guests_stopped",
    ):
        description = next(item for item in sensor.HOST_SENSOR_DESCRIPTIONS if item.key == key)
        values[key] = PulseHostSensor(coordinator, host_id, description).native_value

    assert values == {
        "containers_running": 2,
        "containers_stopped": 1,
        "container_problems": 0,
        "guests_running": 2,
        "guests_stopped": 0,
    }


def test_host_health_reports_ok_warning_problem_and_unknown() -> None:
    base = _health_payload("online")
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    assert _host_health(base, "host-1", entry).native_value == "ok"

    degraded = _health_payload("degraded")
    warning = _host_health(degraded, "host-1", entry)
    assert warning.native_value == "warning"
    assert warning.extra_state_attributes["triggering_resources"] == ["host-1 · beeinträchtigt"]

    pool_warning = _health_payload("online", storage_status="warning")
    pool = _host_health(pool_warning, "host-1", entry)
    assert pool.native_value == "warning"
    assert pool.extra_state_attributes["triggering_resources"][0].startswith("Pool ")

    critical = _health_payload("online", alert_level="critical", alert_resource_id="res-vm")
    problem = _host_health(critical, "host-1", entry)
    assert problem.native_value == "problem"
    assert problem.extra_state_attributes["alerts"][0].startswith("kritisch: ")

    missing = _host_health({"resources": [], "activeAlerts": []}, "host-1", entry)
    assert missing.available is True
    assert missing.native_value == "problem"
    assert missing.extra_state_attributes["triggering_resources"] == ["host-1 · nicht gemeldet"]

    stale = _host_health({"activeAlerts": []}, "host-1", entry)
    assert stale.available is True
    assert stale.native_value is None


def test_host_temperature_is_not_influenced_by_disk_values() -> None:
    data = normalize_state(
        {
            "resources": [
                _resource("host-1", "agent", "res-host", "online") | {"temperature": 60},
                _resource("disk-1", "physical_disk", "res-disk-1", "online", parent_id="res-host")
                | {"physicalDisk": {"temperature": 27}},
                _resource("disk-2", "physical_disk", "res-disk-2", "online", parent_id="res-host")
                | {"physicalDisk": {"temperature": 0}},
                _resource("disk-3", "physical_disk", "res-disk-3", "online", parent_id="res-host")
                | {"physicalDisk": {"temperature": 62.4}, "displayName": "disk-hot"},
            ],
            "activeAlerts": [],
        }
    )
    entry = _entry()
    coordinator = _coordinator(entry, data)
    entity = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "temperature"),
    )
    disk_entity = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "disk_temperature"),
    )

    assert entity.native_value == 60.0
    assert entity.extra_state_attributes is None
    assert disk_entity.native_value == 62.4
    assert disk_entity.extra_state_attributes == {
        "disks": ["disk-1 · 27.0 °C", "disk-hot · 62.4 °C"]
    }


def test_disk_temperature_uses_disks_below_skipped_storage_member() -> None:
    data = normalize_state(_nested_disk_payload())
    entry = _entry()
    coordinator = _coordinator(entry, data)
    entity = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "disk_temperature"),
    )

    assert data.storages == {}
    assert data.removed_resource_ids == {"member-1", "disk-cool", "disk-hot", "disk-zero", "disk-missing"}
    assert entity.native_value == 37.0
    assert entity.extra_state_attributes == {
        "disks": ["disk-cool · 28.0 °C", "disk-hot · 37.0 °C"]
    }


@pytest.mark.asyncio
async def test_host_and_disk_temperature_sensors_are_created_independently() -> None:
    data = normalize_state(_nested_disk_payload())
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)

    unique_ids = {entity.unique_id for entity in added}
    assert "entry-1_host-1_temperature" not in unique_ids
    assert "entry-1_host-1_disk_temperature" in unique_ids

    payload = _nested_disk_payload()
    payload["resources"] = [
        resource
        for resource in payload["resources"]
        if resource["type"] != "physical_disk"
    ]
    payload["resources"][0]["temperature"] = 42
    data = normalize_state(payload)
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)

    unique_ids = {entity.unique_id for entity in added}
    assert "entry-1_host-1_temperature" in unique_ids
    assert "entry-1_host-1_disk_temperature" not in unique_ids


@pytest.mark.asyncio
async def test_nested_skipped_storage_member_creates_no_entity_or_device(hass, enable_custom_integrations) -> None:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-nested", data={CONF_HOST: "https://pulse.example"})
    entry.add_to_hass(hass)
    data = normalize_state(_nested_disk_payload())
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await sensor.async_setup_entry(hass, entry, added.extend)
    async_cleanup_removed_resources(hass, entry, data)

    unique_ids = {entity.unique_id for entity in added}
    device_registry = dr.async_get(hass)
    assert not any("_member-1_" in unique_id for unique_id in unique_ids)
    assert not any("_disk-hot_" in unique_id for unique_id in unique_ids)
    assert device_registry.async_get_device({(DOMAIN, f"{entry.entry_id}_member-1")}) is None
    assert device_registry.async_get_device({(DOMAIN, f"{entry.entry_id}_disk-hot")}) is None


def test_host_health_and_counts_use_transitive_host_parent() -> None:
    payload = _nested_disk_payload()
    payload["resources"].extend(
        [
            _resource("container-1", "app-container", "res-container", "running", parent_id="res-member"),
            _resource("vm-1", "vm", "res-vm", "stopped", parent_id="res-member"),
        ]
    )
    payload["activeAlerts"] = [
        {
            "id": "alert-1",
            "level": "critical",
            "type": "resource",
            "resourceId": "res-container",
        }
    ]
    data = normalize_state(payload)
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)

    health = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    containers_running = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "containers_running"),
    )
    guests_stopped = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "guests_stopped"),
    )

    assert health.native_value == "problem"
    assert health.extra_state_attributes["alerts"] == ["kritisch: host-1 · resource"]
    assert containers_running.native_value == 1
    assert guests_stopped.native_value == 1


def test_disk_health_flows_into_host_health_and_disk_problem_sensor() -> None:
    data = normalize_state(_disk_health_payload())
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)
    health = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    disk_problems = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "disk_problems"),
    )

    assert health.native_value == "problem"
    assert health.extra_state_attributes["triggering_resources"] == [
        "Platte disk-failed · FAILED",
        "Platte disk-warning · PASSED",
    ]
    assert disk_problems.native_value == 2
    assert disk_problems.extra_state_attributes == {
        "disks": [
            "disk-failed · FAILED · online",
            "disk-warning · PASSED · beeinträchtigt",
        ]
    }
    assert disk_problems.entity_category is EntityCategory.DIAGNOSTIC


def test_disk_temperature_marks_spun_down_disks_without_counting_them() -> None:
    data = normalize_state(_disk_health_payload())
    entry = _entry()
    coordinator = _coordinator(entry, data)
    disk_temperature = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "disk_temperature"),
    )

    assert disk_temperature.native_value == 37.0
    assert disk_temperature.extra_state_attributes == {
        "disks": [
            "disk-failed · 31.0 °C",
            "disk-sleeping · schläft",
            "disk-warning · 37.0 °C",
        ]
    }


def test_disk_life_remaining_uses_lowest_reported_remaining_life() -> None:
    data = normalize_state(_disk_health_payload())
    entry = _entry()
    coordinator = _coordinator(entry, data)
    life = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "disk_life_remaining"),
    )

    assert life.native_value == 73.0
    assert life.extra_state_attributes == {
        "disks": [
            "disk-failed · 96.0 % Restlebensdauer",
            "disk-warning · 73.0 % Restlebensdauer",
        ]
    }


@pytest.mark.asyncio
async def test_host_diagnostic_rate_and_agent_sensors_are_created_only_with_values() -> None:
    data = normalize_state(_host_diagnostics_payload())
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1", "host-2"]})
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)

    by_unique = {entity.unique_id: entity for entity in added}
    assert by_unique["entry-1_host-1_network_rx_rate"].native_value == 2414
    assert by_unique["entry-1_host-1_network_rx_rate"].device_class is SensorDeviceClass.DATA_RATE
    assert by_unique["entry-1_host-1_network_rx_rate"].native_unit_of_measurement == UnitOfDataRate.BYTES_PER_SECOND
    assert by_unique["entry-1_host-1_network_rx_rate"].suggested_unit_of_measurement == UnitOfDataRate.MEGABYTES_PER_SECOND
    assert by_unique["entry-1_host-1_network_rx_rate"].suggested_display_precision == 2
    assert by_unique["entry-1_host-1_network_rx_rate"].state_class is SensorStateClass.MEASUREMENT
    assert by_unique["entry-1_host-1_network_rx_rate"].entity_category is EntityCategory.DIAGNOSTIC
    assert by_unique["entry-1_host-1_disk_write_rate"].native_value == 456
    assert by_unique["entry-1_host-1_agent_version"].native_value == "6.3.1"
    assert by_unique["entry-1_host-1_agent_last_report"].device_class is SensorDeviceClass.TIMESTAMP
    assert "entry-1_host-2_network_rx_rate" not in by_unique
    assert "entry-1_host-2_agent_version" not in by_unique


def test_container_problems_use_docker_health_and_oom_fields() -> None:
    payload = _nested_disk_payload()
    payload["resources"].extend(
        [
            _resource("container-1", "app-container", "res-container-1", "running", parent_id="res-member")
            | {"docker": {"health": "unhealthy"}},
            _resource("container-2", "app-container", "res-container-2", "running", parent_id="res-member")
            | {"platformData": {"oomKilled": True}},
            _resource("container-3", "app-container", "res-container-3", "running", parent_id="res-member")
            | {"docker": {"health": "healthy"}},
            # Docker friert `health` beim Stoppen ein — ein seit Wochen
            # gestoppter Container bliebe sonst dauerhaft ein Problem.
            _resource("container-4", "app-container", "res-container-4", "stopped", parent_id="res-member")
            | {"docker": {"health": "unhealthy", "containerState": "exited"}, "displayName": "container-4"},
            # Ein gestoppter Container, den der Kernel abgeschossen hat, zählt weiter.
            _resource("container-5", "app-container", "res-container-5", "stopped", parent_id="res-member")
            | {"docker": {"health": "unhealthy", "oomKilled": True}, "displayName": "container-5"},
        ]
    )
    data = normalize_state(payload)
    entry = _entry()
    coordinator = _coordinator(entry, data)
    problems = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "container_problems"),
    )

    assert problems.native_value == 3
    assert "container-4" not in str(problems.extra_state_attributes)
    assert "container-5" in str(problems.extra_state_attributes)


def test_pulse_infrastructure_health_flows_into_overall_status() -> None:
    data = normalize_state(
        {
            "resources": [_resource("host-1", "agent", "res-host", "online")],
            "activeAlerts": [],
            "connectedInfrastructure": [
                {"name": "infra-ok", "healthStatus": "online", "lastSeen": "2026-08-24T10:00:00Z", "version": "6.3.1"},
                {"name": "infra-warn", "healthStatus": "degraded", "lastSeen": "2026-08-24T10:00:00Z", "version": "6.3.0"},
            ],
        }
    )
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )

    assert overall.native_value == "warning"
    assert overall.extra_state_attributes["infrastructure_issues"] == [
        "infra-warn · beeinträchtigt"
    ]

    data = normalize_state(
        {
            "resources": [_resource("host-1", "agent", "res-host", "online")],
            "activeAlerts": [],
            "connectionHealth": {"infra-down": False},
        }
    )
    coordinator = _coordinator(entry, data)
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )
    assert overall.native_value == "problem"
    assert overall.extra_state_attributes["infrastructure_issues"] == ["Problem: infra-down · offline"]


def test_docker_formatted_alert_maps_to_container_and_host_health() -> None:
    data = normalize_state(_container_alert_payload())
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)
    health = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )

    assert data.alerts[0].resolved_resource_id == "app-container:containerhash"
    assert data.alerts[0].resolved_host_id == "host-1"
    assert health.native_value == "problem"
    assert health.extra_state_attributes["alerts"] == ["kritisch: host-a · container-a · ungesund"]


def test_unknown_alert_affects_overall_but_not_host_health() -> None:
    payload = _container_alert_payload()
    payload["activeAlerts"][0]["resourceId"] = "docker:agent-real/unknownhash"
    data = normalize_state(payload)
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)
    health = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )
    critical = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "critical_alerts"),
    )

    assert data.alerts[0].resolved_resource_id is None
    assert data.alerts[0].resolved_host_id is None
    assert health.native_value == "ok"
    assert health.extra_state_attributes["alerts"] == []
    assert overall.native_value == "problem"
    assert critical.native_value == 1
    assert critical.extra_state_attributes["unassigned"] == 1
    assert "host" not in critical.extra_state_attributes["alerts"][0]


def test_ambiguous_hash_alert_is_not_assigned_to_a_host() -> None:
    data = normalize_state(
        {
            "resources": [
                _resource("host-1", "agent", "res-host-1", "online") | {"displayName": "host-a"},
                _resource("host-2", "agent", "res-host-2", "online") | {"displayName": "host-b"},
                _resource("app-container:sharedhash", "app-container", "res-container-1", "running", parent_id="res-host-1"),
                _resource("vm:sharedhash", "vm", "res-vm-1", "running", parent_id="res-host-2"),
            ],
            "activeAlerts": [
                {
                    "id": "alert-ambiguous",
                    "level": "critical",
                    "type": "docker-container-health",
                    "resourceId": "docker:agent-real/sharedhash",
                    "resourceName": "ambiguous",
                    "message": "Mehrdeutige Ressource",
                    "startTime": "2026-08-24T10:00:00Z",
                }
            ],
        }
    )
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1", "host-2"]})
    coordinator = _coordinator(entry, data)
    host_1 = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    host_2 = PulseHostSensor(
        coordinator,
        "host-2",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    critical = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "critical_alerts"),
    )

    assert data.alerts[0].resolved_resource_id is None
    assert host_1.native_value == "ok"
    assert host_2.native_value == "ok"
    assert critical.extra_state_attributes["unassigned"] == 1


def test_alert_counter_attributes_are_limited_and_readable() -> None:
    payload = _container_alert_payload()
    payload["activeAlerts"] = [
        {
            **payload["activeAlerts"][0],
            "id": f"alert-{index}",
            "level": "warning",
            "message": f"Warnung {index}",
        }
        for index in range(27)
    ]
    data = normalize_state(payload)
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)
    warnings = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "warnings"),
    )

    attrs = warnings.extra_state_attributes
    assert warnings.native_value == 27
    # Die Liste wird bewusst kurz gehalten: native Clients hängen alle Einträge
    # zu einer einzigen Zeile aneinander.
    assert attrs["truncated"] == 27 - sensor.ALERT_ATTRIBUTE_LIMIT
    assert attrs["unassigned"] == 0
    assert len(attrs["alerts"]) == sensor.ALERT_ATTRIBUTE_LIMIT
    assert sensor.ALERT_ATTRIBUTE_LIMIT <= 10
    # Kurze Zeichenkette statt Dictionary: native Clients rendern Attributlisten
    # flach, verschachtelte Strukturen werden dort zur Textwand.
    assert attrs["alerts"][0] == "host-a · container-a · ungesund"
    assert all(isinstance(line, str) for line in attrs["alerts"])
    assert "id" not in attrs["alerts"][0]
    assert "resource_id" not in attrs["alerts"][0]


@pytest.mark.asyncio
async def test_host_temperature_sensor_is_not_created_without_valid_values() -> None:
    data = normalize_state(
        {
            "resources": [
                _resource("host-1", "agent", "res-host", "online"),
                _resource("disk-1", "physical_disk", "res-disk-1", "online", parent_id="res-host")
                | {"physicalDisk": {"temperature": 0}},
            ],
            "activeAlerts": [],
        }
    )
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, data)
    entry.runtime_data = coordinator
    added = []

    await sensor.async_setup_entry(SimpleNamespace(), entry, added.extend)

    assert "entry-1_host-1_temperature" not in {entity.unique_id for entity in added}
    assert "entry-1_host-1_disk_temperature" not in {entity.unique_id for entity in added}


@pytest.mark.asyncio
async def test_removed_physical_disk_devices_are_cleaned_from_registries(hass, enable_custom_integrations) -> None:
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-cleanup", data={CONF_HOST: "https://pulse.example"})
    entry.add_to_hass(hass)
    payload = {
        "resources": [
            _resource("host-1", "agent", "res-host", "online"),
            _resource("disk-1", "physical_disk", "res-disk", "online", parent_id="res-host")
            | {"physicalDisk": {"temperature": 36}},
            _resource("member-1", "storage", "res-member", "online", parent_id="res-host")
            | {
                "storage": {"type": "unraid-cache-pool"},
                "tags": ["none"],
                "disk": {"current": 95, "used": 950, "total": 1000, "free": 50},
            },
        ],
        "activeAlerts": [],
    }
    data = normalize_state(payload)
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_disk-1")},
        name="disk-1",
    )
    disk_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_disk-1_status",
        suggested_object_id="disk_1_status",
        config_entry=entry,
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_member-1")},
        name="member-1",
    )
    storage_entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_member-1_usage",
        suggested_object_id="member_1_usage",
        config_entry=entry,
    )

    async_cleanup_removed_resources(hass, entry, data)

    assert data.removed_resource_ids == {"disk-1", "member-1"}
    assert entity_registry.async_get(disk_entity.entity_id) is None
    assert entity_registry.async_get(storage_entity.entity_id) is None
    assert device_registry.async_get_device({(DOMAIN, f"{entry.entry_id}_disk-1")}) is None
    assert device_registry.async_get_device({(DOMAIN, f"{entry.entry_id}_member-1")}) is None


def test_diagnostic_entity_categories_for_secondary_values(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry()
    coordinator = _coordinator(entry, data)
    host_id = next(iter(data.hosts))
    storage_id = next(iter(data.storages))

    host_status = PulseHostSensor(
        coordinator,
        host_id,
        next(item for item in sensor.HOST_SENSOR_DESCRIPTIONS if item.key == "status"),
    )
    storage_used = PulseStorageSensor(
        coordinator,
        storage_id,
        next(item for item in sensor.STORAGE_SENSOR_DESCRIPTIONS if item.key == "used"),
    )
    storage_total = PulseStorageSensor(
        coordinator,
        storage_id,
        next(item for item in sensor.STORAGE_SENSOR_DESCRIPTIONS if item.key == "total"),
    )
    host_counter = PulseHostSensor(
        coordinator,
        host_id,
        next(item for item in sensor.HOST_SENSOR_DESCRIPTIONS if item.key == "containers_running"),
    )
    summary_counter = sensor.PulseSummarySensor(
        coordinator,
        next(item for item in sensor.SUMMARY_SENSOR_DESCRIPTIONS if item.key == "active_alerts"),
    )

    assert host_status.entity_category is EntityCategory.DIAGNOSTIC
    assert storage_used.entity_category is EntityCategory.DIAGNOSTIC
    assert storage_total.entity_category is EntityCategory.DIAGNOSTIC
    assert host_counter.entity_category is EntityCategory.DIAGNOSTIC
    assert summary_counter.entity_category is EntityCategory.DIAGNOSTIC


def test_icon_translations_cover_problem_and_overall_status() -> None:
    icons = json.loads((Path(__file__).parents[1] / "custom_components/pulse/icons.json").read_text())

    assert icons["entity"]["binary_sensor"]["infrastructure_problem"]["state"] == {
        "off": "mdi:shield-check",
        "on": "mdi:alert-octagon",
    }
    assert icons["entity"]["sensor"]["overall_status"]["state"] == {
        "ok": "mdi:shield-check",
        "problem": "mdi:alert-octagon",
        "warning": "mdi:alert",
    }
    assert icons["entity"]["sensor"]["health"]["state"] == {
        "ok": "mdi:check-circle",
        "problem": "mdi:alert-octagon",
        "warning": "mdi:alert",
    }


def test_german_translations_use_requested_labels_and_enum_states() -> None:
    translations = json.loads(
        (Path(__file__).parents[1] / "custom_components/pulse/translations/de.json").read_text()
    )
    sensors = translations["entity"]["sensor"]

    assert sensors["memory_usage"]["name"] == "Arbeitsspeicherauslastung"
    assert sensors["health"]["name"] == "Gerätestatus"
    assert sensors["host_containers_running"]["name"] == "Laufende Container"
    assert translations["entity"]["binary_sensor"]["running"]["name"] == "Betriebsstatus"
    assert sensors["status"]["state"] == {
        "online": "Online",
        "degraded": "Eingeschränkt",
        "offline": "Offline",
        "running": "Läuft",
        "stopped": "Gestoppt",
        "unknown": "Unbekannt",
    }


def test_manifest_version_is_semver() -> None:
    """Version muss semver sein — HACS sortiert Releases danach.

    Bewusst kein fester Wert: ein an die Version genagelter Test schlägt bei
    jedem Release fehl und sagt nichts über die Korrektheit aus.
    """
    manifest = json.loads((Path(__file__).parents[1] / "custom_components/pulse/manifest.json").read_text())

    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), manifest["version"]


def test_guest_without_disk_block_reports_unknown_disk(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry()
    coordinator = _coordinator(entry, data)
    guest_id = next(resource_id for resource_id, guest in data.guests.items() if guest.storage_usage is None)
    description = next(item for item in sensor.GUEST_SENSOR_DESCRIPTIONS if item.key == "disk_usage")
    entity = PulseGuestSensor(coordinator, guest_id, description)

    assert entity.native_value is None


def test_uptime_is_timezone_aware_and_filters_jitter(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    host_id = next(iter(data.hosts))
    entry = _entry()
    coordinator = _coordinator(entry, data)
    entity = PulseHostUptimeSensor(coordinator, host_id)

    first = entity.native_value
    assert first is not None
    assert first.tzinfo is UTC

    data.last_update = data.last_update + timedelta(seconds=30)
    data.hosts[host_id].uptime_seconds = data.hosts[host_id].uptime_seconds + 20
    assert entity.native_value == first

    data.last_update = data.last_update + timedelta(minutes=10)
    data.hosts[host_id].uptime_seconds = 30
    assert entity.native_value != first


def test_alias_migration_updates_existing_registry_unique_id() -> None:
    registry = FakeRegistry(
        {
            ("sensor", DOMAIN, "entry-1_old-id_cpu_usage"): "sensor.host_cpu_usage",
        }
    )

    _migrate_resource_unique_ids(registry, "entry-1", "old-id", "new-id")

    assert registry.updated == [("sensor.host_cpu_usage", "entry-1_new-id_cpu_usage")]


def test_coordinator_alias_migration_runs_for_current_data_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    data = normalize_state(
        {
            "resources": [
                {
                    "id": "res-new",
                    "type": "agent",
                    "status": "online",
                    "canonicalIdentity": {"primaryId": "new-id", "aliases": ["old-id"]},
                }
            ],
            "activeAlerts": [],
        }
    )
    registry = FakeRegistry(
        {
            ("binary_sensor", DOMAIN, "entry-1_old-id_online"): "binary_sensor.host_online",
        }
    )
    device_registry = FakeDeviceRegistry()
    monkeypatch.setattr("custom_components.pulse.coordinator.er.async_get", lambda _hass: registry)
    monkeypatch.setattr("custom_components.pulse.coordinator.dr.async_get", lambda _hass: device_registry)
    coordinator = object.__new__(PulseDataUpdateCoordinator)
    coordinator.config_entry = _entry()
    coordinator._hass = SimpleNamespace()

    coordinator._async_migrate_alias_unique_ids(data)

    assert registry.updated == [("binary_sensor.host_online", "entry-1_new-id_online")]


def test_coordinator_persists_known_hosts_and_aliases(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry()
    hass = SimpleNamespace(config_entries=FakeConfigEntries())
    coordinator = object.__new__(PulseDataUpdateCoordinator)
    coordinator.config_entry = entry
    coordinator._hass = hass

    coordinator._async_persist_identity_data(data)

    assert hass.config_entries.updated_options is not None
    assert sorted(hass.config_entries.updated_options[CONF_KNOWN_HOSTS]) == sorted(data.hosts)
    assert hass.config_entries.updated_options["alias_map"]


def test_coordinator_persist_removes_alias_replaced_known_host() -> None:
    data = normalize_state(_host_payload("new-id", aliases=["old-id"]))
    entry = _entry(options={CONF_KNOWN_HOSTS: ["old-id"], CONF_ALIAS_MAP: {"older-id": "old-id"}})
    hass = SimpleNamespace(config_entries=FakeConfigEntries())
    coordinator = object.__new__(PulseDataUpdateCoordinator)
    coordinator.config_entry = entry
    coordinator._hass = hass

    coordinator._async_persist_identity_data(data)

    assert hass.config_entries.updated_options[CONF_KNOWN_HOSTS] == ["new-id"]
    assert hass.config_entries.updated_options[CONF_ALIAS_MAP]["old-id"] == "new-id"


@pytest.mark.asyncio
async def test_coordinator_refresh_uses_one_state_request(fixture_state: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    coordinator = object.__new__(PulseDataUpdateCoordinator)
    coordinator.api = FakeApi(fixture_state)
    coordinator.config_entry = _entry()
    coordinator._hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=lambda *args, **kwargs: None))
    coordinator._async_persist_identity_data = lambda data: None
    coordinator._async_migrate_alias_unique_ids = lambda data: None

    data = await coordinator._async_update_data()

    assert coordinator.api.calls == ["state"]
    assert len(data.hosts) == 4


class FakeApi:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def async_get_state(self) -> dict:
        self.calls.append("state")
        return self.payload


class FakeRegistry:
    def __init__(self, entities: dict[tuple[str, str, str], str]) -> None:
        self.entities = entities
        self.updated: list[tuple[str, str]] = []

    def async_get_entity_id(self, platform: str, domain: str, unique_id: str):
        return self.entities.get((platform, domain, unique_id))

    def async_update_entity(self, entity_id: str, *, new_unique_id: str) -> None:
        self.updated.append((entity_id, new_unique_id))


class FakeDeviceRegistry:
    def async_get_device(self, _identifiers):
        return None

    def async_update_device(self, *_args, **_kwargs):
        raise AssertionError("async_update_device sollte ohne altes Device nicht aufgerufen werden")


class FakeConfigEntries:
    def __init__(self) -> None:
        self.updated_options = None

    def async_update_entry(self, _entry, *, options) -> None:
        self.updated_options = options


def _entry(options=None):
    return SimpleNamespace(
        entry_id="entry-1",
        data={"host": "https://pulse.example", "api_token": "secret-token"},
        options=options or {},
        async_on_unload=lambda _unsub: None,
    )


def _coordinator(entry, data):
    return SimpleNamespace(
        config_entry=entry,
        data=data,
        last_update_success=True,
        async_add_listener=lambda _listener: (lambda: None),
    )


def _host_health(payload: dict, host_id: str, entry):
    data = normalize_state(payload)
    coordinator = _coordinator(entry, data)
    return PulseHostSensor(
        coordinator,
        host_id,
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )


def _health_payload(
    host_status: str,
    *,
    storage_status: str = "online",
    alert_level: str | None = None,
    alert_resource_id: str = "res-host",
) -> dict:
    alerts = []
    if alert_level is not None:
        alerts.append(
            {
                "id": f"alert-{alert_level}",
                "level": alert_level,
                "type": "resource",
                "resourceId": alert_resource_id,
            }
        )
    return {
        "resources": [
            _resource("host-1", "agent", "res-host", host_status),
            _resource("vm-1", "vm", "res-vm", "running", parent_id="res-host"),
            _resource("pool-1", "storage", "res-pool", storage_status, parent_id="res-host"),
        ],
        "activeAlerts": alerts,
    }


def _nested_disk_payload() -> dict:
    return {
        "resources": [
            _resource("host-1", "agent", "res-host", "online"),
            _resource("member-1", "storage", "res-member", "online", parent_id="res-host")
            | {
                "displayName": "member-1",
                "storage": {"type": "unraid-cache-pool"},
                "tags": ["none"],
                "disk": {"current": 95, "used": 950, "total": 1000, "free": 50},
            },
            _resource("disk-cool", "physical_disk", "res-disk-cool", "online", parent_id="res-member")
            | {"displayName": "disk-cool", "physicalDisk": {"temperature": 28}},
            _resource("disk-hot", "physical_disk", "res-disk-hot", "online", parent_id="res-member")
            | {"displayName": "disk-hot", "temperature": 0, "physicalDisk": {"temperature": 37}},
            _resource("disk-zero", "physical_disk", "res-disk-zero", "online", parent_id="res-member")
            | {"displayName": "disk-zero", "physicalDisk": {"temperature": 0}},
            _resource("disk-missing", "physical_disk", "res-disk-missing", "online", parent_id="res-member")
            | {"displayName": "disk-missing"},
        ],
        "activeAlerts": [],
    }


def _disk_health_payload() -> dict:
    return {
        "resources": [
            _resource("host-1", "agent", "res-host", "online"),
            _resource("member-1", "storage", "res-member", "online", parent_id="res-host")
            | {
                "displayName": "member-1",
                "storage": {"type": "unraid-cache-pool"},
                "tags": ["none"],
                "disk": {"current": 95, "used": 950, "total": 1000, "free": 50},
            },
            _resource("disk-failed", "physical_disk", "res-disk-failed", "online", parent_id="res-member")
            | {
                "displayName": "disk-failed",
                "physicalDisk": {
                    "health": "FAILED",
                    "storageState": "online",
                    "temperature": 31,
                    "spunDown": False,
                    "wearout": 96,
                },
            },
            _resource("disk-warning", "physical_disk", "res-disk-warning", "online", parent_id="res-member")
            | {
                "displayName": "disk-warning",
                "physicalDisk": {
                    "health": "PASSED",
                    "storageState": "degraded",
                    "temperature": 37,
                    "spunDown": False,
                    "wearout": 73,
                },
            },
            _resource("disk-unknown", "physical_disk", "res-disk-unknown", "online", parent_id="res-member")
            | {
                "displayName": "disk-unknown",
                "physicalDisk": {
                    "health": "UNKNOWN",
                    "storageState": "online",
                    "temperature": 0,
                    "spunDown": False,
                    "wearout": -1,
                },
            },
            _resource("disk-sleeping", "physical_disk", "res-disk-sleeping", "online", parent_id="res-member")
            | {
                "displayName": "disk-sleeping",
                "physicalDisk": {
                    "health": "PASSED",
                    "storageState": "online",
                    "temperature": 0,
                    "spunDown": True,
                    "wearout": -1,
                },
            },
        ],
        "activeAlerts": [],
    }


def _host_diagnostics_payload() -> dict:
    return {
        "resources": [
            _resource("host-1", "agent", "res-host-1", "online")
            | {
                "agent": {"agentVersion": "6.3.1", "lastReportAt": "2026-08-24T10:00:00Z"},
                "network": {"rxBytes": 2414, "txBytes": 1200},
                "diskIO": {"readRate": 123, "writeRate": 456},
            },
            _resource("host-2", "agent", "res-host-2", "online"),
        ],
        "activeAlerts": [],
    }


def _container_alert_payload() -> dict:
    return {
        "resources": [
            _resource("host-1", "agent", "res-host", "online") | {"displayName": "host-a"},
            _resource(
                "app-container:containerhash",
                "app-container",
                "res-container",
                "running",
                parent_id="res-host",
            )
            | {
                "displayName": "container-a",
                "docker": {
                    "agentId": "agent-real",
                    "containerId": "containerhash",
                    "health": "unhealthy",
                },
                "metricsTarget": {"resourceId": "metrics-container-a"},
            },
        ],
        "activeAlerts": [
            {
                "id": "alert-1",
                "level": "critical",
                "type": "docker-container-health",
                "resourceId": "docker:agent-real/containerhash",
                "resourceName": "container-a",
                "message": "Container ist ungesund",
                "startTime": "2026-08-24T10:00:00Z",
                "acknowledged": False,
            }
        ],
    }


def _resource(primary_id: str, resource_type: str, resource_id: str, status: str, *, parent_id: str | None = None) -> dict:
    return {
        "id": resource_id,
        "type": resource_type,
        "status": status,
        "parentId": parent_id,
        "canonicalIdentity": {"primaryId": primary_id, "aliases": []},
        "cpu": {"current": 12},
        "memory": {"current": 34, "used": 34, "total": 100, "free": 66},
        "disk": {"current": 56, "used": 56, "total": 100, "free": 44},
    }


def _host_payload(primary_id: str, *, aliases: list[str]) -> dict:
    suffix = primary_id.replace("-id", "")
    return {
        "resources": [
            {
                "id": f"res-{suffix}",
                "type": "agent",
                "status": "online",
                "name": f"host-{suffix}",
                "displayName": f"host-{suffix}",
                "canonicalIdentity": {"primaryId": primary_id, "aliases": aliases},
                "cpu": {"current": 12},
                "memory": {"current": 34, "used": 34, "total": 100, "free": 66},
                "disk": {"current": 56, "used": 56, "total": 100, "free": 44},
                "uptime": 120,
            }
        ],
        "activeAlerts": [],
        "lastUpdate": "2026-08-24T10:00:00Z",
    }


def test_warning_counter_attributes_explain_every_counted_item(fixture_state: dict) -> None:
    """Zähler und Detailliste müssen dieselbe Menge beschreiben.

    Der Warnungszähler summiert Warnalarme und degradierte Hosts. Erklärt die
    Detailliste nur die Alarme, kann der Nutzer die Differenz nicht zuordnen.
    """
    data = normalize_state(fixture_state)
    entry = _entry()
    coordinator = _coordinator(entry, data)
    description = next(item for item in sensor.SUMMARY_SENSOR_DESCRIPTIONS if item.key == "warnings")
    warnings = sensor.PulseSummarySensor(coordinator, description)

    assert warnings.native_value == len(warnings.extra_state_attributes["alerts"])


def test_rendered_list_attributes_contain_only_strings(fixture_state: dict) -> None:
    """Listenattribute dürfen keine Dictionaries enthalten.

    Native Clients wie Vulpo rendern Attributlisten flach aneinandergehängt;
    verschachtelte Strukturen werden dort zu einer unlesbaren Textwand.
    """
    # Die Fixture enthält keine Verbindungsdaten — ohne sie bliebe
    # infrastructure_issues leer und der Test würde die Lücke nicht sehen.
    payload = {
        **fixture_state,
        "connectedInfrastructure": [
            {
                "name": "infra-1",
                "healthStatus": "degraded",
                "lastSeen": 1787611413668,
                "version": "v6.3.1",
            }
        ],
    }
    data = normalize_state(payload)
    assert data.infrastructure_issues, "Testaufbau liefert keine Infrastruktur-Einträge"
    entry = _entry(options={CONF_KNOWN_HOSTS: list(data.hosts)})
    coordinator = _coordinator(entry, data)

    entities: list[Any] = [
        sensor.PulseSummarySensor(coordinator, description)
        for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS
    ]
    for host_id in data.hosts:
        entities.extend(
            sensor.PulseHostSensor(coordinator, host_id, description)
            for description in sensor.HOST_SENSOR_DESCRIPTIONS
        )

    checked = 0
    for entity in entities:
        for key, value in (entity.extra_state_attributes or {}).items():
            if not isinstance(value, list):
                continue
            checked += 1
            assert all(isinstance(item, str) for item in value), (
                f"{type(entity).__name__}.{key} enthält Nicht-Strings: {value[:2]}"
            )
    assert checked > 0, "kein Listenattribut geprüft — Test wäre wirkungslos"


def test_container_problem_sensor_names_the_containers() -> None:
    """Ein Zähler ohne Namen ist nicht handlungsfähig.

    Der Nutzer sieht „5" und müsste zu Pulse wechseln, um zu erfahren, welche
    fünf Container gemeint sind.
    """
    data = normalize_state(_container_alert_payload())
    entry = _entry(options={CONF_INCLUDE_CONTAINERS: True})
    coordinator = _coordinator(entry, data)
    host_id = next(iter(data.hosts))
    assert sensor._count_container_problems(data, host_id) > 0
    description = next(
        item for item in sensor.HOST_SENSOR_DESCRIPTIONS if item.key == "container_problems"
    )
    entity = sensor.PulseHostSensor(coordinator, host_id, description)

    containers = entity.extra_state_attributes["containers"]
    assert len(containers) == entity.native_value
    assert all(isinstance(line, str) for line in containers)


def _risk_payload(
    *,
    host_status: str = "degraded",
    risk_codes: tuple[str, ...] = ("unraid_no_parity",),
    extra_storage_status: str | None = None,
) -> dict:
    """Host, der nur über ein Speicher-Risiko beeinträchtigt ist.

    Pulse rollt den schlechtesten Kindstatus auf den Agenten hoch, nennt am
    Agenten aber keinen Grund — genau diese Konstellation bildet der Payload ab.
    """
    reasons = [
        {"code": code, "severity": "warning", "summary": f"Grund {code}"}
        for code in risk_codes
    ]
    resources = [
        _resource("host-1", "agent", "res-host", host_status)
        | {"agent": {"storageRisk": {"level": "warning", "reasons": reasons}}},
        _resource("pool-1", "storage", "res-pool", "degraded", parent_id="res-host")
        | {
            "displayName": "pool-1",
            "storage": {
                "type": "unraid-array",
                "risk": {"level": "warning", "reasons": reasons},
            },
        },
    ]
    if extra_storage_status is not None:
        resources.append(
            _resource("pool-2", "storage", "res-pool-2", extra_storage_status, parent_id="res-host")
            | {"displayName": "pool-2"}
        )
    return {"resources": resources, "activeAlerts": []}


def test_ignored_risk_code_clears_host_status_and_health() -> None:
    payload = _risk_payload()
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )

    health = _host_health(payload, "host-1", entry)

    assert health.native_value == "ok"
    assert health.extra_state_attributes["triggering_resources"] == []
    assert health.extra_state_attributes["ignored_risks"] == ["pool-1 · Grund unraid_no_parity"]

    coordinator = _coordinator(entry, normalize_state(payload))
    status = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "status"),
    )
    assert status.native_value == "online"
    assert status.extra_state_attributes == {
        "ignored_risks": ["pool-1 · Grund unraid_no_parity"]
    }


def test_ignored_risk_code_clears_overall_status_and_warning_counter() -> None:
    payload = _risk_payload()
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )
    coordinator = _coordinator(entry, normalize_state(payload))
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )
    warnings = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "warnings"),
    )

    assert overall.native_value == "ok"
    assert warnings.native_value == 0
    assert overall.extra_state_attributes["triggering_hosts"] == []


def test_without_the_option_the_risk_still_counts_as_warning() -> None:
    payload = _risk_payload()
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})

    health = _host_health(payload, "host-1", entry)

    assert health.native_value == OVERALL_STATUS_WARNING
    assert health.extra_state_attributes["ignored_risks"] == []


def test_unignored_risk_reason_keeps_the_warning() -> None:
    """Ein zweiter, nicht abgewählter Grund darf die Ressource nicht stumm schalten."""

    payload = _risk_payload(risk_codes=("unraid_no_parity", "unraid_disk_missing"))
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )

    health = _host_health(payload, "host-1", entry)

    assert health.native_value == OVERALL_STATUS_WARNING
    assert health.extra_state_attributes["triggering_resources"] == [
        "host-1 · beeinträchtigt",
        "Pool pool-1 · beeinträchtigt",
    ]


def test_second_problem_resource_keeps_the_host_warning() -> None:
    """Der Host darf nur stumm werden, wenn kein anderes Kind auffällig ist."""

    payload = _risk_payload(extra_storage_status="degraded")
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )

    health = _host_health(payload, "host-1", entry)

    assert health.native_value == OVERALL_STATUS_WARNING
    assert health.extra_state_attributes["triggering_resources"] == [
        "host-1 · beeinträchtigt",
        "Pool pool-2 · beeinträchtigt",
    ]


def _shadow_risk_payload(*, infrastructure_status: str = "warning") -> dict:
    """Der reale Fall: ein Array-Schatten ohne Kapazität, den Pulse hochrollt.

    Solche Pools bekommen bewusst keine Entity — für die Ursachenfrage müssen
    sie trotzdem zählen, sonst bliebe der Host ohne erkennbaren Grund degraded.
    """

    return {
        "resources": [
            _resource("host-1", "agent", "res-host", "degraded")
            | {
                "displayName": "Tower",
                "agent": {
                    "storageRisk": {
                        "level": "warning",
                        "reasons": [
                            {
                                "code": "unraid_no_parity",
                                "severity": "warning",
                                "summary": "Unraid array is running without parity protection",
                            }
                        ],
                    }
                },
            },
            {
                "id": "res-array",
                "type": "storage",
                "status": "degraded",
                "parentId": "res-host",
                "displayName": "Tower Array",
                "canonicalIdentity": {"primaryId": "array-1", "aliases": []},
                "storage": {
                    "type": "unraid-array",
                    "risk": {
                        "level": "warning",
                        "reasons": [
                            {
                                "code": "unraid_no_parity",
                                "severity": "warning",
                                "summary": "Unraid array is running without parity protection",
                            }
                        ],
                    },
                },
            },
        ],
        "activeAlerts": [],
        "connectedInfrastructure": [
            {"name": "Tower", "healthStatus": infrastructure_status, "version": "v6.3.2"}
        ],
    }


def test_ignored_risk_of_a_storage_without_entity_clears_host_and_overall_status() -> None:
    payload = _shadow_risk_payload()
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )
    data = normalize_state(payload)
    coordinator = _coordinator(entry, data)

    assert "array-1" not in data.storages
    health = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )

    assert health.native_value == "ok"
    assert health.extra_state_attributes["ignored_risks"] == [
        "Tower Array · Unraid array is running without parity protection"
    ]
    assert overall.native_value == "ok"
    assert overall.extra_state_attributes["infrastructure_issues"] == []


def test_offline_infrastructure_entry_survives_the_abwahl() -> None:
    """Abgewählt wird ein Risiko, kein Ausfall."""

    payload = _shadow_risk_payload(infrastructure_status="offline")
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )
    coordinator = _coordinator(entry, normalize_state(payload))
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )

    assert overall.native_value == OVERALL_STATUS_PROBLEM
    assert overall.extra_state_attributes["infrastructure_issues"] == ["Problem: Tower · offline"]


def test_status_stays_degraded_without_the_option() -> None:
    """Ohne Abwahl bleibt der Statussensor bei dem, was Pulse meldet."""

    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, normalize_state(_risk_payload()))
    status = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "status"),
    )

    assert status.native_value == "degraded"
    assert status.extra_state_attributes is None


def test_status_stays_degraded_when_another_resource_is_affected() -> None:
    """Ein zweites auffälliges Kind darf den Status nicht schönfärben."""

    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )
    coordinator = _coordinator(entry, normalize_state(_risk_payload(extra_storage_status="degraded")))
    status = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "status"),
    )

    assert status.native_value == "degraded"


def test_offline_host_is_never_reported_as_online() -> None:
    """Die Abwahl gilt für Risiken, nicht für Erreichbarkeit."""

    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )
    coordinator = _coordinator(entry, normalize_state(_risk_payload(host_status="offline")))
    status = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "status"),
    )

    assert status.native_value == "offline"


def test_host_risk_without_a_pool_resource_blocks_the_abwahl() -> None:
    """Ein Host-RAID-Risiko hat keine eigene Pool-Ressource.

    Pulse fasst in `agent.storageRisk` alle Speicherbefunde des Hosts zusammen,
    auch mdadm-RAID ohne eigene Ressource. Steht dort ein nicht abgewählter
    Grund, darf die Abwahl des Array-Risikos ihn nicht mit stumm schalten.
    """

    payload = {
        "resources": [
            _resource("host-1", "agent", "res-host", "degraded")
            | {
                "agent": {
                    "storageRisk": {
                        "level": "warning",
                        "reasons": [
                            {"code": "unraid_no_parity", "severity": "warning", "summary": "ohne Parität"},
                            {"code": "raid_degraded", "severity": "warning", "summary": "RAID beeinträchtigt"},
                        ],
                    }
                }
            },
        ],
        "activeAlerts": [],
    }
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )
    coordinator = _coordinator(entry, normalize_state(payload))
    health = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    status = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "status"),
    )

    assert health.native_value == OVERALL_STATUS_WARNING
    assert status.native_value == "degraded"


def test_degraded_host_without_any_risk_reason_is_never_muted() -> None:
    """Ohne erkennbaren Grund am Agenten bleibt es bei dem, was Pulse meldet."""

    payload = {
        "resources": [_resource("host-1", "agent", "res-host", "degraded")],
        "activeAlerts": [],
    }
    entry = _entry(
        options={CONF_KNOWN_HOSTS: ["host-1"], CONF_IGNORED_RISK_CODES: ["unraid_no_parity"]}
    )
    coordinator = _coordinator(entry, normalize_state(payload))
    status = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "status"),
    )

    assert status.native_value == "degraded"


def test_infrastructure_entries_are_matched_by_agent_id_not_by_name() -> None:
    """Zwei Hosts dürfen denselben Anzeigenamen tragen.

    Der Spiegeleintrag wird über `scopeAgentId` zugeordnet; sonst würde die
    Abwahl beim einen Host die Warnung des anderen mit verschlucken.
    """

    payload = {
        "resources": [
            _resource("host-1", "agent", "res-host-1", "degraded")
            | {
                "displayName": "Tower",
                "identity": {"machineId": "machine-1"},
                "agent": {
                    "agentId": "machine-1",
                    "storageRisk": {
                        "level": "warning",
                        "reasons": [{"code": "unraid_no_parity", "severity": "warning", "summary": "ohne Parität"}],
                    },
                },
            },
            _resource("host-2", "agent", "res-host-2", "degraded")
            | {
                "displayName": "Tower",
                "identity": {"machineId": "machine-2"},
                "agent": {"agentId": "machine-2"},
            },
        ],
        "activeAlerts": [],
        "connectedInfrastructure": [
            {"name": "Tower", "scopeAgentId": "machine-1", "healthStatus": "warning"},
            {"name": "Tower", "scopeAgentId": "machine-2", "healthStatus": "warning"},
        ],
    }
    entry = _entry(
        options={
            CONF_KNOWN_HOSTS: ["host-1", "host-2"],
            CONF_IGNORED_RISK_CODES: ["unraid_no_parity"],
        }
    )
    coordinator = _coordinator(entry, normalize_state(payload))
    overall = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "overall_status"),
    )

    # host-2 ist unverändert auffällig — sein Spiegeleintrag muss stehen bleiben.
    assert overall.native_value == OVERALL_STATUS_WARNING
    assert overall.extra_state_attributes["infrastructure_issues"] == ["Tower · Warnung"]


def test_stopped_monitored_container_reaches_host_health_and_warning_count() -> None:
    """Ein überwachter Container, der ausgeht, muss eine Meldung erzeugen.

    Pulse meldet das als Alarm `docker-container-state` mit der in
    `dockerDefaults.statePoweredOffSeverity` konfigurierten Stufe. In Pulse
    abgeschaltete Container erzeugen diesen Alarm nicht — die Auswahl, was
    überwacht wird, bleibt damit vollständig in Pulse.
    """

    payload = {
        "resources": [
            _resource("host-1", "agent", "res-host", "online") | {"displayName": "Tower"},
            _resource("container-1", "app-container", "res-container-1", "stopped", parent_id="res-host")
            | {
                "displayName": "vaultwarden",
                "docker": {"agentId": "agent-1", "containerId": "container-hash", "containerState": "exited"},
            },
        ],
        "activeAlerts": [
            {
                "id": "alert-1",
                "level": "warning",
                "type": "docker-container-state",
                "resourceId": "docker:agent-1/container-hash",
                "resourceName": "vaultwarden",
            }
        ],
    }
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    data = normalize_state(payload)
    coordinator = _coordinator(entry, data)
    health = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "health"),
    )
    warnings = sensor.PulseSummarySensor(
        coordinator,
        next(description for description in sensor.SUMMARY_SENSOR_DESCRIPTIONS if description.key == "warnings"),
    )

    assert data.alerts[0].resolved_host_id == "host-1"
    assert health.native_value == OVERALL_STATUS_WARNING
    assert health.extra_state_attributes["alerts"] == ["Tower · vaultwarden · gestoppt"]
    assert warnings.native_value == 1


def test_container_alert_counts_via_resolved_identity() -> None:
    """Der Alarm nennt die Docker-Kennung, nicht die Ressourcen-ID.

    Pulse adressiert Container-Alarme als `docker:<agent>/<hash>`. Wird nur die
    rohe ID verglichen, zählt ein gestoppter überwachter Container nicht als
    Container-Problem — und die Meldung nennt ihn nicht.
    """

    payload = {
        "resources": [
            _resource("host-1", "agent", "res-host", "online") | {"displayName": "Tower"},
            _resource("container-1", "app-container", "res-container-1", "stopped", parent_id="res-host")
            | {
                "displayName": "vaultwarden",
                "docker": {"agentId": "agent-1", "containerId": "container-hash"},
            },
        ],
        "activeAlerts": [
            {
                "id": "alert-1",
                "level": "warning",
                "type": "docker-container-state",
                "resourceId": "docker:agent-1/container-hash",
                "resourceName": "vaultwarden",
            }
        ],
    }
    entry = _entry(options={CONF_KNOWN_HOSTS: ["host-1"]})
    coordinator = _coordinator(entry, normalize_state(payload))
    problems = PulseHostSensor(
        coordinator,
        "host-1",
        next(description for description in sensor.HOST_SENSOR_DESCRIPTIONS if description.key == "container_problems"),
    )

    assert problems.native_value == 1
    assert problems.extra_state_attributes == {"containers": ["vaultwarden · gestoppt"]}
