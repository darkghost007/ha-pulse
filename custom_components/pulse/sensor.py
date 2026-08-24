"""Sensor-Entities für Pulse."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_ALIAS_MAP,
    CONF_CRITICAL_HOSTS,
    CONF_CRITICAL_HOSTS_MODE,
    CONF_INCLUDE_CONTAINERS,
    CONF_INCLUDE_GUESTS,
    CONF_KNOWN_HOSTS,
    CRITICAL_MODE_ALL,
    CRITICAL_MODE_SELECTED,
)
from .coordinator import (
    PulseData,
    PulseDataUpdateCoordinator,
    PulseResource,
    boot_time_from_uptime,
    remap_alias_ids,
)
from .entity import (
    PulseEntity,
    PulseGuestEntity,
    PulseHostEntity,
    PulseStorageEntity,
)

ResourceValueFn = Callable[[PulseResource], Any]
SummaryValueFn = Callable[[PulseData], Any]

OVERALL_STATUS_OK = "ok"
OVERALL_STATUS_WARNING = "warning"
OVERALL_STATUS_PROBLEM = "problem"


@dataclass(frozen=True, kw_only=True)
class PulseResourceSensorDescription(SensorEntityDescription):
    """Beschreibung eines ressourcenbezogenen Sensors."""

    value_fn: ResourceValueFn


@dataclass(frozen=True, kw_only=True)
class PulseSummarySensorDescription(SensorEntityDescription):
    """Beschreibung eines Hub-Sensors."""

    value_fn: SummaryValueFn


HOST_SENSOR_DESCRIPTIONS: tuple[PulseResourceSensorDescription, ...] = (
    PulseResourceSensorDescription(
        key="health",
        translation_key="health",
        device_class=SensorDeviceClass.ENUM,
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:cpu-64-bit",
        value_fn=lambda resource: resource.cpu_usage,
    ),
    PulseResourceSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:memory",
        value_fn=lambda resource: resource.memory_usage,
    ),
    PulseResourceSensorDescription(
        key="storage_usage",
        translation_key="storage_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.storage_usage,
    ),
    PulseResourceSensorDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda resource: resource.temperature,
    ),
    PulseResourceSensorDescription(
        key="disk_temperature",
        translation_key="disk_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:server",
        value_fn=lambda resource: resource.status,
    ),
    PulseResourceSensorDescription(
        key="containers_running",
        translation_key="host_containers_running",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:docker",
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="containers_stopped",
        translation_key="host_containers_stopped",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:docker",
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="container_problems",
        translation_key="host_container_problems",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert",
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="guests_running",
        translation_key="host_guests_running",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:desktop-tower-monitor",
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="guests_stopped",
        translation_key="host_guests_stopped",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:desktop-tower-monitor",
        value_fn=lambda resource: None,
    ),
)

GUEST_SENSOR_DESCRIPTIONS: tuple[PulseResourceSensorDescription, ...] = (
    PulseResourceSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:cpu-64-bit",
        value_fn=lambda resource: resource.cpu_usage,
    ),
    PulseResourceSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:memory",
        value_fn=lambda resource: resource.memory_usage,
    ),
    PulseResourceSensorDescription(
        key="disk_usage",
        translation_key="disk_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.storage_usage,
    ),
)

STORAGE_SENSOR_DESCRIPTIONS: tuple[PulseResourceSensorDescription, ...] = (
    PulseResourceSensorDescription(
        key="usage",
        translation_key="usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.storage_usage,
    ),
    PulseResourceSensorDescription(
        key="used",
        translation_key="used",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.storage_used,
    ),
    PulseResourceSensorDescription(
        key="total",
        translation_key="total",
        native_unit_of_measurement=UnitOfInformation.BYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.storage_total,
    ),
)

SUMMARY_SENSOR_DESCRIPTIONS: tuple[PulseSummarySensorDescription, ...] = (
    PulseSummarySensorDescription(
        key="overall_status",
        translation_key="overall_status",
        device_class=SensorDeviceClass.ENUM,
        value_fn=lambda data: None,
    ),
    PulseSummarySensorDescription(
        key="warnings",
        translation_key="warnings",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-outline",
        value_fn=lambda data: None,
    ),
    PulseSummarySensorDescription(
        key="critical_alerts",
        translation_key="critical_alerts",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-octagon",
        value_fn=lambda data: None,
    ),
    PulseSummarySensorDescription(
        key="active_alerts",
        translation_key="active_alerts",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert",
        value_fn=lambda data: data.summary.active_alerts,
    ),
    PulseSummarySensorDescription(
        key="hosts_online",
        translation_key="hosts_online",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:server",
        value_fn=lambda data: data.summary.hosts_online,
    ),
    PulseSummarySensorDescription(
        key="hosts_offline",
        translation_key="hosts_offline",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:server-off",
        value_fn=lambda data: data.summary.hosts_offline,
    ),
    PulseSummarySensorDescription(
        key="vms_running",
        translation_key="vms_running",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:desktop-tower-monitor",
        value_fn=lambda data: data.summary.vms_running,
    ),
    PulseSummarySensorDescription(
        key="vms_stopped",
        translation_key="vms_stopped",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:desktop-tower-monitor",
        value_fn=lambda data: data.summary.vms_stopped,
    ),
    PulseSummarySensorDescription(
        key="containers_running",
        translation_key="containers_running",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:docker",
        value_fn=lambda data: data.summary.containers_running,
    ),
    PulseSummarySensorDescription(
        key="containers_stopped",
        translation_key="containers_stopped",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:docker",
        value_fn=lambda data: data.summary.containers_stopped,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Richtet Sensoren für eine Pulse-Instanz ein."""

    coordinator: PulseDataUpdateCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_entities() -> None:
        data = coordinator.data
        if data is None:
            return
        entities: list[SensorEntity] = []

        for description in SUMMARY_SENSOR_DESCRIPTIONS:
            unique = f"summary_{description.key}"
            if unique not in known:
                known.add(unique)
                entities.append(PulseSummarySensor(coordinator, description))

        host_ids = remap_alias_ids(
            set(entry.options.get(CONF_KNOWN_HOSTS, [])) | set(data.hosts),
            dict(entry.options.get(CONF_ALIAS_MAP, {})),
        )
        for resource_id in sorted(host_ids):
            for description in HOST_SENSOR_DESCRIPTIONS:
                if description.key == "temperature" and _host_temperature_value(data, resource_id) is None:
                    continue
                if description.key == "disk_temperature" and _host_disk_temperature_value(data, resource_id) is None:
                    continue
                unique = f"host_{resource_id}_{description.key}"
                if unique not in known:
                    known.add(unique)
                    entities.append(PulseHostSensor(coordinator, resource_id, description))
            unique = f"host_{resource_id}_uptime"
            if unique not in known:
                known.add(unique)
                entities.append(PulseHostUptimeSensor(coordinator, resource_id))

        if entry.options.get(CONF_INCLUDE_GUESTS, True):
            for resource_id in data.guests:
                for description in GUEST_SENSOR_DESCRIPTIONS:
                    unique = f"guest_{resource_id}_{description.key}"
                    if unique not in known:
                        known.add(unique)
                        entities.append(PulseGuestSensor(coordinator, resource_id, description))

        if entry.options.get(CONF_INCLUDE_CONTAINERS, False):
            for resource_id in data.containers:
                for description in GUEST_SENSOR_DESCRIPTIONS:
                    unique = f"container_{resource_id}_{description.key}"
                    if unique not in known:
                        known.add(unique)
                        entities.append(PulseGuestSensor(coordinator, resource_id, description, containers=True))

        for resource_id in data.storages:
            for description in STORAGE_SENSOR_DESCRIPTIONS:
                unique = f"storage_{resource_id}_{description.key}"
                if unique not in known:
                    known.add(unique)
                    entities.append(PulseStorageSensor(coordinator, resource_id, description))

        if entities:
            async_add_entities(entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class PulseSummarySensor(PulseEntity, SensorEntity):
    """Hub-Sensor."""

    entity_description: PulseSummarySensorDescription

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        description: PulseSummarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description
        if description.key == "overall_status":
            self._attr_options = [OVERALL_STATUS_OK, OVERALL_STATUS_WARNING, OVERALL_STATUS_PROBLEM]

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        if data is None:
            return None
        if "resources" in data.stale:
            return None
        if self.entity_description.key == "overall_status":
            return _overall_status(data, self._entry)
        if self.entity_description.key == "warnings":
            if "alerts" in data.stale:
                return None
            return len(_warning_alerts(data)) + len(_warning_hosts(data))
        if self.entity_description.key == "critical_alerts":
            if "alerts" in data.stale:
                return None
            return len(_critical_alerts(data))
        if self.entity_description.key == "active_alerts" and "alerts" in data.stale:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "overall_status":
            return None
        data = self.coordinator.data
        if data is None:
            return None
        return {
            "triggering_hosts": _triggering_hosts(data, self._entry),
            "triggering_alerts": _triggering_alerts(data),
        }


class PulseResourceSensor(SensorEntity):
    """Mixin für ressourcenbezogene Sensorwerte."""

    entity_description: PulseResourceSensorDescription

    @property
    def native_value(self) -> Any:
        resource = self.resource
        if resource is None:
            return None
        return self.entity_description.value_fn(resource)


class PulseHostSensor(PulseHostEntity, PulseResourceSensor):
    """Host-Sensor."""

    entity_description: PulseResourceSensorDescription

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        resource_id: str,
        description: PulseResourceSensorDescription,
    ) -> None:
        PulseHostEntity.__init__(self, coordinator, resource_id, description.key)
        self.entity_description = description
        if description.key == "health":
            self._attr_options = [OVERALL_STATUS_OK, OVERALL_STATUS_WARNING, OVERALL_STATUS_PROBLEM]
        if description.key == "status":
            self._attr_options = ["online", "offline", "degraded", "unknown", "active", "running"]

    @property
    def available(self) -> bool:
        if self.entity_description.key == "health":
            return self.coordinator.data is not None and self.coordinator.last_update_success
        return super().available

    @property
    def native_value(self) -> Any:
        if self.entity_description.key == "health":
            data = self.coordinator.data
            if data is None:
                return None
            return _host_health_value(data, self.current_resource_id)
        if self.entity_description.key == "temperature":
            data = self.coordinator.data
            if data is None or "resources" in data.stale:
                return None
            return _host_temperature_value(data, self.current_resource_id)
        if self.entity_description.key == "disk_temperature":
            data = self.coordinator.data
            if data is None or "resources" in data.stale:
                return None
            return _host_disk_temperature_value(data, self.current_resource_id)
        if self.entity_description.key in {
            "containers_running",
            "containers_stopped",
            "container_problems",
            "guests_running",
            "guests_stopped",
        }:
            data = self.coordinator.data
            resource = self.resource
            if data is None or resource is None or "resources" in data.stale:
                return None
            if self.entity_description.key == "containers_running":
                return _count_children(data.containers.values(), resource.canonical_id, running=True)
            if self.entity_description.key == "containers_stopped":
                return _count_children(data.containers.values(), resource.canonical_id, running=False)
            if self.entity_description.key == "container_problems":
                if "alerts" in data.stale:
                    return None
                return _count_container_problems(data, resource.canonical_id)
            if self.entity_description.key == "guests_running":
                return _count_children(data.guests.values(), resource.canonical_id, running=True)
            if self.entity_description.key == "guests_stopped":
                return _count_children(data.guests.values(), resource.canonical_id, running=False)
        return PulseResourceSensor.native_value.fget(self)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None:
            return None
        if self.entity_description.key == "health":
            return _host_health_attributes(data, self.current_resource_id)
        if self.entity_description.key == "disk_temperature":
            return {"disks": _host_disk_temperatures(data, self.current_resource_id)}
        return None


class PulseHostUptimeSensor(PulseHostEntity, SensorEntity):
    """Host-Uptime als Boot-Zeitpunkt."""

    _attr_translation_key = "uptime"
    _attr_device_class = SensorDeviceClass.UPTIME
    _last_boot_time: datetime | None = None

    def __init__(self, coordinator: PulseDataUpdateCoordinator, resource_id: str) -> None:
        super().__init__(coordinator, resource_id, "uptime")

    @property
    def native_value(self) -> datetime | None:
        resource = self.resource
        data = self.coordinator.data
        if resource is None or data is None:
            return None
        boot_time = boot_time_from_uptime(data.last_update, resource.uptime_seconds)
        if boot_time is None:
            return None
        if self._last_boot_time is None or abs((boot_time - self._last_boot_time).total_seconds()) >= 60:
            self._last_boot_time = boot_time
        return self._last_boot_time


class PulseGuestSensor(PulseGuestEntity, PulseResourceSensor):
    """VM-/Container-Sensor."""

    entity_description: PulseResourceSensorDescription

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        resource_id: str,
        description: PulseResourceSensorDescription,
        *,
        containers: bool = False,
    ) -> None:
        PulseGuestEntity.__init__(self, coordinator, resource_id, description.key, containers=containers)
        self.entity_description = description


class PulseStorageSensor(PulseStorageEntity, PulseResourceSensor):
    """Storage-Sensor."""

    entity_description: PulseResourceSensorDescription

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        resource_id: str,
        description: PulseResourceSensorDescription,
    ) -> None:
        PulseStorageEntity.__init__(self, coordinator, resource_id, description.key)
        self.entity_description = description


def _critical_host_ids(data: PulseData, entry: ConfigEntry) -> set[str]:
    alias_map = dict(entry.options.get(CONF_ALIAS_MAP, {}))
    mode = entry.options.get(CONF_CRITICAL_HOSTS_MODE, CRITICAL_MODE_ALL)
    if mode == CRITICAL_MODE_SELECTED:
        return remap_alias_ids(entry.options.get(CONF_CRITICAL_HOSTS, []), alias_map)
    return remap_alias_ids(set(entry.options.get(CONF_KNOWN_HOSTS, [])) | set(data.hosts), alias_map)


def _host_health_value(data: PulseData, host_id: str) -> str | None:
    if "resources" in data.stale or "alerts" in data.stale:
        return None
    host = data.hosts.get(host_id)
    if host is None or not host.is_host_online:
        return OVERALL_STATUS_PROBLEM
    if any(alert.level == "critical" for alert in _host_alerts(data, host_id)):
        return OVERALL_STATUS_PROBLEM
    if not host.is_host_healthy:
        return OVERALL_STATUS_WARNING
    if any(alert.level == "warning" for alert in _host_alerts(data, host_id)):
        return OVERALL_STATUS_WARNING
    if any(storage.status != "online" for storage in _host_storages(data, host_id)):
        return OVERALL_STATUS_WARNING
    return OVERALL_STATUS_OK


def _host_health_attributes(data: PulseData, host_id: str) -> dict[str, Any]:
    triggering_alerts = [
        {
            "id": alert.alert_id,
            "level": alert.level,
            "type": alert.type,
            "resource_id": alert.resource_id,
        }
        for alert in _host_alerts(data, host_id)
        if alert.level in {"warning", "critical"}
    ]
    triggering_resources: list[dict[str, Any]] = []
    host = data.hosts.get(host_id)
    if "resources" not in data.stale:
        if host is None:
            triggering_resources.append(
                {"id": host_id, "name": host_id, "type": "agent", "status": "missing", "reason": "offline"}
            )
        elif not host.is_host_online:
            triggering_resources.append(_resource_attribute(host, "offline"))
        elif not host.is_host_healthy:
            triggering_resources.append(_resource_attribute(host, "degraded"))
        triggering_resources.extend(
            _resource_attribute(storage, "pool_not_online")
            for storage in _host_storages(data, host_id)
            if storage.status != "online"
        )
    return {
        "triggering_alerts": triggering_alerts,
        "triggering_resources": triggering_resources,
    }


def _resource_attribute(resource: PulseResource, reason: str) -> dict[str, Any]:
    return {
        "id": resource.canonical_id,
        "name": resource.name,
        "type": resource.type,
        "status": resource.status,
        "reason": reason,
    }


def _problem_hosts(data: PulseData, entry: ConfigEntry) -> list[dict[str, str]]:
    if "resources" in data.stale:
        return []
    output = []
    for host_id in sorted(_critical_host_ids(data, entry)):
        host = data.hosts.get(host_id)
        if host is None or not host.is_host_online:
            output.append({"id": host_id, "name": host.name if host is not None else host_id})
    return output


def _warning_hosts(data: PulseData) -> list[PulseResource]:
    if "resources" in data.stale:
        return []
    return [host for host in data.hosts.values() if host.is_host_online and not host.is_host_healthy]


def _warning_alerts(data: PulseData) -> list:
    if "alerts" in data.stale:
        return []
    return [alert for alert in data.alerts if alert.level == "warning"]


def _critical_alerts(data: PulseData) -> list:
    if "alerts" in data.stale:
        return []
    return [alert for alert in data.alerts if alert.level == "critical"]


def _overall_status(data: PulseData, entry: ConfigEntry) -> str | None:
    if "resources" in data.stale or "alerts" in data.stale:
        return None
    if not data.hosts and not entry.options.get(CONF_KNOWN_HOSTS) and not data.alerts:
        return None
    if _problem_hosts(data, entry) or _critical_alerts(data):
        return OVERALL_STATUS_PROBLEM
    if _warning_hosts(data) or _warning_alerts(data):
        return OVERALL_STATUS_WARNING
    return OVERALL_STATUS_OK


def _triggering_hosts(data: PulseData, entry: ConfigEntry) -> list[dict[str, str]]:
    hosts = _problem_hosts(data, entry)
    hosts.extend({"id": host.canonical_id, "name": host.name, "status": host.status or "unknown"} for host in _warning_hosts(data))
    return hosts


def _triggering_alerts(data: PulseData) -> list[dict[str, Any]]:
    return [
        {
            "id": alert.alert_id,
            "level": alert.level,
            "type": alert.type,
            "resource_id": alert.resource_id,
        }
        for alert in data.alerts
        if alert.level in {"warning", "critical"}
    ]


def _host_alerts(data: PulseData, host_id: str):
    resource_ids = _host_related_resource_ids(data, host_id)
    return [alert for alert in data.alerts if alert.resource_id in resource_ids]


def _host_related_resource_ids(data: PulseData, host_id: str) -> set[str]:
    resource_ids: set[str] = {host_id}
    host = data.hosts.get(host_id)
    if host is not None:
        resource_ids.update({host.resource_id, host.canonical_id})
    for resource in _host_child_resources(data, host_id):
        resource_ids.update({resource.resource_id, resource.canonical_id})
    return resource_ids


def _host_child_resources(data: PulseData, host_id: str) -> list[PulseResource]:
    return [
        resource
        for resources in (data.guests, data.containers, data.storages, data.physical_disks)
        for resource in resources.values()
        if resource.host_canonical_id == host_id
    ]


def _host_storages(data: PulseData, host_id: str) -> list[PulseResource]:
    return [resource for resource in data.storages.values() if resource.host_canonical_id == host_id]


def _host_temperature_value(data: PulseData, host_id: str) -> float | None:
    host = data.hosts.get(host_id)
    if host is not None and _valid_temperature(host.temperature):
        return round(host.temperature, 1)
    return None


def _host_disk_temperature_value(data: PulseData, host_id: str) -> float | None:
    values = []
    values.extend(item["temperature"] for item in _host_disk_temperatures(data, host_id))
    if not values:
        return None
    return round(max(values), 1)


def _host_disk_temperatures(data: PulseData, host_id: str) -> list[dict[str, Any]]:
    return [
        {"name": disk.name, "temperature": round(disk.temperature, 1)}
        for disk in sorted(data.physical_disks.values(), key=lambda item: item.name)
        if disk.host_canonical_id == host_id and _valid_temperature(disk.temperature)
    ]


def _valid_temperature(value: float | None) -> bool:
    return value is not None and value > 0


def _count_children(children, host_id: str, *, running: bool) -> int:
    return sum(1 for child in children if child.host_canonical_id == host_id and child.is_running is running)


def _count_container_problems(data: PulseData, host_id: str) -> int:
    problem_ids = {
        container.canonical_id
        for container in data.containers.values()
        if container.host_canonical_id == host_id
        and container.status not in {"running", "stopped", "online"}
    }
    for alert in data.alerts:
        for container in data.containers.values():
            if container.host_canonical_id != host_id:
                continue
            if alert.resource_id in {container.resource_id, container.canonical_id}:
                problem_ids.add(container.canonical_id)
    return len(problem_ids)
