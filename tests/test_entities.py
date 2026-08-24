"""Akzeptanztests für Pulse-Entities und Coordinator-Verhalten."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr, entity_registry as er
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
from custom_components.pulse.sensor import PulseGuestSensor, PulseHostUptimeSensor


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
        if entity.config_entry_id == entry.entry_id
        and entity.platform == "pulse"
        and entity.domain == "binary_sensor"
        and entity.unique_id.endswith("_online")
    ]
    devices = list(device_registry.devices.values())

    assert len(entities) == 1
    assert entities[0].entity_id == old_entity_id
    assert entities[0].unique_id == "entry-real_new-id_online"
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

    assert len(added) == 49
    assert len({entity.unique_id for entity in added}) == 49

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

    assert len(added) == 61
    assert sum(1 for entity in added if "_canon-10_" in entity.unique_id) == 4


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
