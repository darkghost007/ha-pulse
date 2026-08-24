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
from homeassistant.const import PERCENTAGE, UnitOfDataRate, UnitOfInformation, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ALERT_TYPE_LABELS,
    STATUS_LABELS,
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
    PulseAlert,
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
ALERT_ATTRIBUTE_LIMIT = 8
OPTIONAL_HOST_VALUE_KEYS = {
    "network_rx_rate",
    "network_tx_rate",
    "disk_read_rate",
    "disk_write_rate",
    "agent_version",
    "agent_last_report",
}


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
        key="disk_problems",
        translation_key="disk_problems",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:harddisk-alert",
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="disk_life_remaining",
        translation_key="disk_life_remaining",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        icon="mdi:heart-pulse",
        value_fn=lambda resource: None,
    ),
    PulseResourceSensorDescription(
        key="network_rx_rate",
        translation_key="network_rx_rate",
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:download-network",
        value_fn=lambda resource: resource.network_rx_rate,
    ),
    PulseResourceSensorDescription(
        key="network_tx_rate",
        translation_key="network_tx_rate",
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:upload-network",
        value_fn=lambda resource: resource.network_tx_rate,
    ),
    PulseResourceSensorDescription(
        key="disk_read_rate",
        translation_key="disk_read_rate",
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.disk_read_rate,
    ),
    PulseResourceSensorDescription(
        key="disk_write_rate",
        translation_key="disk_write_rate",
        native_unit_of_measurement=UnitOfDataRate.BYTES_PER_SECOND,
        suggested_unit_of_measurement=UnitOfDataRate.MEGABYTES_PER_SECOND,
        suggested_display_precision=2,
        device_class=SensorDeviceClass.DATA_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.disk_write_rate,
    ),
    PulseResourceSensorDescription(
        key="agent_version",
        translation_key="agent_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:package-variant",
        value_fn=lambda resource: resource.agent_version,
    ),
    PulseResourceSensorDescription(
        key="agent_last_report",
        translation_key="agent_last_report",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:clock-check",
        value_fn=lambda resource: resource.agent_last_report_at,
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
            resource = data.hosts.get(resource_id)
            for description in HOST_SENSOR_DESCRIPTIONS:
                if description.key == "temperature" and _host_temperature_value(data, resource_id) is None:
                    continue
                if description.key == "disk_temperature" and _host_disk_temperature_value(data, resource_id) is None:
                    continue
                if description.key == "disk_life_remaining" and _host_disk_life_remaining(data, resource_id) is None:
                    continue
                if (
                    description.key in OPTIONAL_HOST_VALUE_KEYS
                    and (resource is None or description.value_fn(resource) is None)
                ):
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
        data = self.coordinator.data
        if data is None:
            return None
        if self.entity_description.key == "warnings":
            # Der Zähler summiert Warnalarme UND degradierte Hosts. Ohne die
            # Hosts erklärt die Detailliste weniger Einträge, als der Wert zeigt.
            attrs = _alert_list_attributes(data, _warning_alerts(data))
            attrs["alerts"] = [
                *attrs["alerts"],
                *(
                    _resource_attribute(host, host.status or "degraded")
                    for host in _warning_hosts(data)
                ),
            ]
            return attrs
        if self.entity_description.key == "critical_alerts":
            return _alert_list_attributes(data, _critical_alerts(data))
        if self.entity_description.key != "overall_status":
            return None
        alert_attrs = _alert_list_attributes(
            data,
            sorted(
                (alert for alert in data.alerts if alert.level in {"warning", "critical"}),
                # Kritische zuerst: die Liste wird gekappt, und dann darf nicht
                # das Dringendste wegfallen.
                key=lambda alert: 0 if alert.level == "critical" else 1,
            ),
        )
        return {
            "triggering_hosts": _triggering_hosts(data, self._entry),
            "triggering_alerts": alert_attrs["alerts"],
            "triggering_alerts_truncated": alert_attrs["truncated"],
            "unassigned_alerts": alert_attrs["unassigned"],
            "infrastructure_issues": _infrastructure_issue_labels(data),
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
        if self.entity_description.key == "disk_problems":
            data = self.coordinator.data
            if data is None or "resources" in data.stale:
                return None
            return len(_host_disk_problem_details(data, self.current_resource_id))
        if self.entity_description.key == "disk_life_remaining":
            data = self.coordinator.data
            if data is None or "resources" in data.stale:
                return None
            return _host_disk_life_remaining(data, self.current_resource_id)
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
        if self.entity_description.key == "container_problems":
            if "alerts" in data.stale:
                return None
            return {"containers": _container_problem_details(data, self.current_resource_id)}
        if self.entity_description.key == "disk_problems":
            return {"disks": _host_disk_problem_details(data, self.current_resource_id)}
        if self.entity_description.key == "disk_life_remaining":
            return {"disks": _host_disk_life_details(data, self.current_resource_id)}
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
    if any(_disk_problem_severity(disk) == OVERALL_STATUS_PROBLEM for disk in _host_disks(data, host_id)):
        return OVERALL_STATUS_PROBLEM
    if not host.is_host_healthy:
        return OVERALL_STATUS_WARNING
    if any(alert.level == "warning" for alert in _host_alerts(data, host_id)):
        return OVERALL_STATUS_WARNING
    if any(storage.status != "online" for storage in _host_storages(data, host_id)):
        return OVERALL_STATUS_WARNING
    if any(_disk_problem_severity(disk) == OVERALL_STATUS_WARNING for disk in _host_disks(data, host_id)):
        return OVERALL_STATUS_WARNING
    return OVERALL_STATUS_OK


def _host_health_attributes(data: PulseData, host_id: str) -> dict[str, Any]:
    alert_attrs = _alert_list_attributes(
        data,
        [alert for alert in _host_alerts(data, host_id) if alert.level in {"warning", "critical"}],
    )
    triggering_resources: list[str] = []
    host = data.hosts.get(host_id)
    if "resources" not in data.stale:
        if host is None:
            triggering_resources.append(f"{host_id} · {STATUS_LABELS['missing']}")
        elif not host.is_host_online:
            triggering_resources.append(_resource_attribute(host, "offline"))
        elif not host.is_host_healthy:
            triggering_resources.append(_resource_attribute(host, "degraded"))
        triggering_resources.extend(
            f"Pool {storage.name} · {STATUS_LABELS.get(storage.status or 'unknown', storage.status)}"
            for storage in _host_storages(data, host_id)
            if storage.status != "online"
        )
        triggering_resources.extend(
            f"Platte {disk.name} · {disk.disk_health or STATUS_LABELS['unknown']}"
            for disk in _host_disks(data, host_id)
            if _disk_problem_severity(disk) is not None
        )
    return {
        "alerts": alert_attrs["alerts"],
        "triggering_alerts": alert_attrs["alerts"],
        "truncated": alert_attrs["truncated"],
        "unassigned": alert_attrs["unassigned"],
        "triggering_resources": triggering_resources,
    }


def _infrastructure_issue_labels(data: PulseData) -> list[str]:
    """Verbindungsprobleme als kurze Zeilen.

    Die interne Struktur bleibt unangetastet — die Statuslogik wertet dort
    `problem` aus. Formatiert wird erst an der Attributgrenze.
    """
    output: list[str] = []
    for issue in data.infrastructure_issues:
        status = issue.get("status") or "unknown"
        label = STATUS_LABELS.get(status, status)
        marker = "Problem: " if issue.get("problem") else ""
        output.append(f"{marker}{issue.get('name') or 'unbekannt'} · {label}")
    return output


def _resource_attribute(resource: PulseResource, reason: str) -> str:
    """Eine auslösende Ressource als kurze Zeile.

    Wie bei den Alarmen bewusst eine Zeichenkette: native Clients rendern
    Attributlisten flach, verschachtelte Strukturen werden dort zur Textwand.
    Die kanonische ID entfällt — sie ist für den Nutzer bedeutungslos und
    steht bei Bedarf in den Diagnosedaten.
    """
    label = STATUS_LABELS.get(reason, reason)
    return f"{resource.name or resource.canonical_id} · {label}"


def _problem_hosts(data: PulseData, entry: ConfigEntry) -> list[str]:
    if "resources" in data.stale:
        return []
    output: list[str] = []
    for host_id in sorted(_critical_host_ids(data, entry)):
        host = data.hosts.get(host_id)
        if host is None:
            # Bekannter Host, der im Payload fehlt — nicht dasselbe wie offline.
            output.append(f"{host_id} · {STATUS_LABELS['missing']}")
        elif not host.is_host_online:
            output.append(_resource_attribute(host, host.status or "offline"))
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
    if any(issue["problem"] for issue in data.infrastructure_issues):
        return OVERALL_STATUS_PROBLEM
    if _warning_hosts(data) or _warning_alerts(data):
        return OVERALL_STATUS_WARNING
    if data.infrastructure_issues:
        return OVERALL_STATUS_WARNING
    return OVERALL_STATUS_OK


def _triggering_hosts(data: PulseData, entry: ConfigEntry) -> list[str]:
    hosts = _problem_hosts(data, entry)
    hosts.extend(
        _resource_attribute(host, host.status or "unknown")
        for host in _warning_hosts(data)
    )
    return hosts


def _triggering_alerts(data: PulseData) -> list[dict[str, Any]]:
    return _alert_list_attributes(
        data,
        [alert for alert in data.alerts if alert.level in {"warning", "critical"}],
    )["alerts"]


def _alert_list_attributes(data: PulseData, alerts: list[PulseAlert]) -> dict[str, Any]:
    return {
        "alerts": [_alert_attribute(data, alert) for alert in alerts[:ALERT_ATTRIBUTE_LIMIT]],
        "truncated": max(0, len(alerts) - ALERT_ATTRIBUTE_LIMIT),
        "unassigned": sum(1 for alert in alerts if alert.resolved_resource_id is None),
    }


def _alert_attribute(data: PulseData, alert: PulseAlert) -> str:
    """Ein Alarm als kurze, lesbare Zeile.

    Bewusst eine Zeichenkette statt eines Dictionaries: native Clients wie Vulpo
    rendern Attributlisten flach als `Key: Wert, Key: Wert, ...` ohne
    Zeilenumbruch. Bei mehreren Alarmen wird daraus eine unlesbare Textwand.
    """
    parts: list[str] = []
    if alert.resolved_host_id is not None:
        host = data.hosts.get(alert.resolved_host_id)
        parts.append(host.name if host is not None else alert.resolved_host_id)
    if alert.resource_name:
        parts.append(alert.resource_name)
    parts.append(ALERT_TYPE_LABELS.get(alert.type, alert.type))
    line = " · ".join(parts)
    # Host-Sensoren mischen Warnungen und kritische Alarme in einer Liste —
    # ohne Kennzeichnung wäre nicht erkennbar, was davon dringend ist.
    return f"kritisch: {line}" if alert.level == "critical" else line


def _host_alerts(data: PulseData, host_id: str) -> list[PulseAlert]:
    return [alert for alert in data.alerts if alert.resolved_host_id == host_id]


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
    """Wärmste gemessene Platte des Hosts.

    Rechnet bewusst auf den Rohwerten, nicht auf der Anzeigeliste — sonst hinge
    die Messgröße an der Formatierung.
    """
    values = [
        disk.temperature
        for disk in _host_disks(data, host_id)
        if not disk.disk_spun_down and _valid_temperature(disk.temperature)
    ]
    if not values:
        return None
    return round(max(values), 1)


def _host_disk_temperatures(data: PulseData, host_id: str) -> list[str]:
    output: list[str] = []
    for disk in sorted(_host_disks(data, host_id), key=lambda item: item.name):
        if disk.disk_spun_down:
            output.append(f"{disk.name} · schläft")
        elif _valid_temperature(disk.temperature):
            output.append(f"{disk.name} · {round(disk.temperature, 1)} °C")
    return output


def _host_disks(data: PulseData, host_id: str) -> list[PulseResource]:
    return [disk for disk in data.physical_disks.values() if disk.host_canonical_id == host_id]


def _host_disk_problem_details(data: PulseData, host_id: str) -> list[str]:
    return [
        f"{disk.name} · {disk.disk_health or STATUS_LABELS['unknown']}"
        f" · {STATUS_LABELS.get(disk.disk_storage_state or 'unknown', disk.disk_storage_state)}"
        for disk in sorted(_host_disks(data, host_id), key=lambda item: item.name)
        if _disk_problem_severity(disk) is not None
    ]


def _disk_problem_severity(disk: PulseResource) -> str | None:
    health = (disk.disk_health or "").upper()
    storage_state = (disk.disk_storage_state or "").lower()
    if health and health not in {"PASSED", "UNKNOWN"}:
        return OVERALL_STATUS_PROBLEM
    if storage_state and storage_state != "online":
        return OVERALL_STATUS_WARNING
    return None


def _host_disk_life_remaining(data: PulseData, host_id: str) -> float | None:
    # Pulse v6.3.1 unified-resources contract beschreibt `wearout` als
    # Ausgangswert für "remaining life"; negative Werte sind der Sentinel für
    # keine Angabe und werden bereits in der Normalisierung entfernt.
    values = [disk.disk_wearout for disk in _host_disks(data, host_id) if disk.disk_wearout is not None]
    if not values:
        return None
    return min(values)


def _host_disk_life_details(data: PulseData, host_id: str) -> list[str]:
    return [
        f"{disk.name} · {disk.disk_wearout} % Restlebensdauer"
        for disk in sorted(_host_disks(data, host_id), key=lambda item: item.name)
        if disk.disk_wearout is not None
    ]


def _valid_temperature(value: float | None) -> bool:
    return value is not None and value > 0


def _count_children(children, host_id: str, *, running: bool) -> int:
    return sum(1 for child in children if child.host_canonical_id == host_id and child.is_running is running)


def _problem_containers(data: PulseData, host_id: str) -> list[PulseResource]:
    """Auffällige Container eines Hosts — eigener Zustand oder offener Alarm."""
    problem_ids = {
        container.canonical_id
        for container in data.containers.values()
        if container.host_canonical_id == host_id and _container_has_problem(container)
    }
    for alert in data.alerts:
        for container in data.containers.values():
            if container.host_canonical_id != host_id:
                continue
            if alert.resource_id in {container.resource_id, container.canonical_id}:
                problem_ids.add(container.canonical_id)
    return sorted(
        (
            container
            for container in data.containers.values()
            if container.canonical_id in problem_ids
        ),
        key=lambda container: container.name or "",
    )


def _count_container_problems(data: PulseData, host_id: str) -> int:
    return len(_problem_containers(data, host_id))


def _container_problem_details(data: PulseData, host_id: str) -> list[str]:
    """Namen der auffälligen Container.

    Ein reiner Zähler ist in einer Übersicht nicht handlungsfähig: Der Nutzer
    sieht 5 und muss zu Pulse wechseln, um zu erfahren, welche fünf.
    """
    return [
        f"{container.name or container.canonical_id}"
        f" · {STATUS_LABELS.get(container.status or 'unknown', container.status)}"
        for container in _problem_containers(data, host_id)
    ]


def _container_has_problem(container: PulseResource) -> bool:
    health = (container.docker_health or "").lower()
    if health and health not in {"healthy", "none", "unknown"}:
        return True
    if container.docker_oom_killed:
        return True
    return container.status not in {"running", "stopped", "online"}
