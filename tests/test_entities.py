"""Akzeptanztests für Pulse-Entities und Coordinator-Verhalten."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pulse import binary_sensor, sensor
from custom_components.pulse.const import (
    CONF_ALIAS_MAP,
    CONF_INCLUDE_CONTAINERS,
    CONF_KNOWN_HOSTS,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.pulse.coordinator import (
    PulseDataUpdateCoordinator,
    _migrate_resource_unique_ids,
    normalize_state,
)
from custom_components.pulse.sensor import (
    OVERALL_STATUS_PROBLEM,
    OVERALL_STATUS_WARNING,
    PulseGuestSensor,
    PulseHostSensor,
    PulseHostUptimeSensor,
    PulsePhysicalDiskSensor,
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

    assert len(added) == 72
    assert len({entity.unique_id for entity in added}) == 72

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

    assert not any(entity.unique_id.endswith("_used") for entity in added if "canon-37" in entity.unique_id)
    assert not any(entity.unique_id.endswith("_total") for entity in added if "canon-41" in entity.unique_id)


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

    assert len(added) == 84
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
                _resource("disk-1", "physical_disk", "res-disk", "online"),
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
    disk = PulsePhysicalDiskSensor(
        coordinator,
        "disk-1",
        next(description for description in sensor.PHYSICAL_DISK_SENSOR_DESCRIPTIONS if description.key == "status"),
    )

    assert "resources" in data.stale
    assert host.available is False
    assert guest.available is False
    assert storage.available is False
    assert disk.available is False


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
    assert overall.extra_state_attributes["triggering_hosts"] == [
        {"id": "canon-57", "name": "host-1", "status": "degraded"}
    ]


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


def test_diagnostic_entity_categories_for_secondary_values(fixture_state: dict) -> None:
    data = normalize_state(fixture_state)
    entry = _entry()
    coordinator = _coordinator(entry, data)
    host_id = next(iter(data.hosts))
    storage_id = next(iter(data.storages))
    disk_id = next(iter(data.physical_disks))

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
    disk_status = PulsePhysicalDiskSensor(
        coordinator,
        disk_id,
        next(item for item in sensor.PHYSICAL_DISK_SENSOR_DESCRIPTIONS if item.key == "status"),
    )

    assert host_status.entity_category is EntityCategory.DIAGNOSTIC
    assert storage_used.entity_category is EntityCategory.DIAGNOSTIC
    assert storage_total.entity_category is EntityCategory.DIAGNOSTIC
    assert disk_status.entity_category is EntityCategory.DIAGNOSTIC


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


def test_german_translations_use_requested_labels_and_enum_states() -> None:
    translations = json.loads(
        (Path(__file__).parents[1] / "custom_components/pulse/translations/de.json").read_text()
    )
    sensors = translations["entity"]["sensor"]

    assert sensors["memory_usage"]["name"] == "Arbeitsspeicherauslastung"
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


def test_manifest_version_is_020() -> None:
    manifest = json.loads((Path(__file__).parents[1] / "custom_components/pulse/manifest.json").read_text())

    assert manifest["version"] == "0.2.0"


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
