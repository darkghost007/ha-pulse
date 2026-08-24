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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_ALIAS_MAP, CONF_INCLUDE_CONTAINERS, CONF_INCLUDE_GUESTS, CONF_KNOWN_HOSTS
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
    PulsePhysicalDiskEntity,
    PulseStorageEntity,
)

ResourceValueFn = Callable[[PulseResource], Any]
SummaryValueFn = Callable[[PulseData], Any]

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
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
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
        value_fn=lambda resource: resource.temperature,
    ),
    PulseResourceSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:server",
        value_fn=lambda resource: resource.status,
    ),
)

GUEST_SENSOR_DESCRIPTIONS: tuple[PulseResourceSensorDescription, ...] = (
    PulseResourceSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
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
        suggested_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=1,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.storage_total,
    ),
)

PHYSICAL_DISK_SENSOR_DESCRIPTIONS: tuple[PulseResourceSensorDescription, ...] = (
    PulseResourceSensorDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        icon="mdi:harddisk",
        value_fn=lambda resource: resource.status,
    ),
    PulseResourceSensorDescription(
        key="temperature",
        translation_key="temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda resource: resource.temperature,
    ),
)

SUMMARY_SENSOR_DESCRIPTIONS: tuple[PulseSummarySensorDescription, ...] = (
    PulseSummarySensorDescription(
        key="active_alerts",
        translation_key="active_alerts",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:shield-check",
        value_fn=lambda data: data.summary.active_alerts,
    ),
    PulseSummarySensorDescription(
        key="hosts_online",
        translation_key="hosts_online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.summary.hosts_online,
    ),
    PulseSummarySensorDescription(
        key="hosts_offline",
        translation_key="hosts_offline",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.summary.hosts_offline,
    ),
    PulseSummarySensorDescription(
        key="vms_running",
        translation_key="vms_running",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.summary.vms_running,
    ),
    PulseSummarySensorDescription(
        key="vms_stopped",
        translation_key="vms_stopped",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.summary.vms_stopped,
    ),
    PulseSummarySensorDescription(
        key="containers_running",
        translation_key="containers_running",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.summary.containers_running,
    ),
    PulseSummarySensorDescription(
        key="containers_stopped",
        translation_key="containers_stopped",
        state_class=SensorStateClass.MEASUREMENT,
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
                if description.key == "temperature" and (resource is None or resource.temperature is None):
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

        for resource_id, resource in data.physical_disks.items():
            for description in PHYSICAL_DISK_SENSOR_DESCRIPTIONS:
                if description.key == "temperature" and resource.temperature is None:
                    continue
                unique = f"physical_disk_{resource_id}_{description.key}"
                if unique not in known:
                    known.add(unique)
                    entities.append(PulsePhysicalDiskSensor(coordinator, resource_id, description))

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

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.value_fn(data)


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
        if description.key == "status":
            self._attr_options = ["online", "offline", "degraded", "unknown", "active", "running"]


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


class PulsePhysicalDiskSensor(PulsePhysicalDiskEntity, PulseResourceSensor):
    """Sensor für physische Platten."""

    entity_description: PulseResourceSensorDescription

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        resource_id: str,
        description: PulseResourceSensorDescription,
    ) -> None:
        PulsePhysicalDiskEntity.__init__(self, coordinator, resource_id, description.key)
        self.entity_description = description
        if description.key == "status":
            self._attr_options = ["online", "offline", "unknown", "warning", "critical"]
