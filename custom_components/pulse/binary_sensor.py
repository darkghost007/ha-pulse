"""Binary-Sensor-Entities für Pulse."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_CRITICAL_HOSTS,
    CONF_CRITICAL_HOSTS_MODE,
    CONF_ALIAS_MAP,
    CONF_INCLUDE_CONTAINERS,
    CONF_INCLUDE_GUESTS,
    CONF_KNOWN_HOSTS,
    CRITICAL_MODE_ALL,
    CRITICAL_MODE_SELECTED,
)
from .coordinator import PulseDataUpdateCoordinator, remap_alias_ids
from .entity import PulseEntity, PulseGuestEntity, PulseHostEntity


@dataclass(frozen=True, kw_only=True)
class PulseBinarySensorDescription(BinarySensorEntityDescription):
    """Beschreibung eines Pulse-Binary-Sensors."""


HOST_ONLINE_DESCRIPTION = PulseBinarySensorDescription(
    key="online",
    translation_key="online",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
)

GUEST_RUNNING_DESCRIPTION = PulseBinarySensorDescription(
    key="running",
    translation_key="running",
    device_class=BinarySensorDeviceClass.RUNNING,
)

INFRASTRUCTURE_PROBLEM_DESCRIPTION = PulseBinarySensorDescription(
    key="infrastructure_problem",
    translation_key="infrastructure_problem",
    device_class=BinarySensorDeviceClass.PROBLEM,
    icon="mdi:shield-check",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Richtet Binary-Sensoren für eine Pulse-Instanz ein."""

    coordinator: PulseDataUpdateCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_entities() -> None:
        data = coordinator.data
        if data is None:
            return
        entities: list[BinarySensorEntity] = []

        if "infrastructure_problem" not in known:
            known.add("infrastructure_problem")
            entities.append(PulseInfrastructureProblemBinarySensor(coordinator))

        host_ids = remap_alias_ids(
            set(entry.options.get(CONF_KNOWN_HOSTS, [])) | set(data.hosts),
            dict(entry.options.get(CONF_ALIAS_MAP, {})),
        )
        for resource_id in sorted(host_ids):
            unique = f"host_{resource_id}_online"
            if unique not in known:
                known.add(unique)
                entities.append(PulseHostOnlineBinarySensor(coordinator, resource_id))

        if entry.options.get(CONF_INCLUDE_GUESTS, True):
            for resource_id in data.guests:
                unique = f"guest_{resource_id}_running"
                if unique not in known:
                    known.add(unique)
                    entities.append(PulseGuestRunningBinarySensor(coordinator, resource_id))

        if entry.options.get(CONF_INCLUDE_CONTAINERS, False):
            for resource_id in data.containers:
                unique = f"container_{resource_id}_running"
                if unique not in known:
                    known.add(unique)
                    entities.append(PulseGuestRunningBinarySensor(coordinator, resource_id, containers=True))

        if entities:
            async_add_entities(entities)

    add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_new_entities))


class PulseHostOnlineBinarySensor(PulseHostEntity, BinarySensorEntity):
    """Host-Online-Sensor."""

    entity_description = HOST_ONLINE_DESCRIPTION

    def __init__(self, coordinator: PulseDataUpdateCoordinator, resource_id: str) -> None:
        super().__init__(coordinator, resource_id, "online")

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None and self.coordinator.last_update_success

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        resource = self.resource
        if resource is None:
            return False
        return resource.is_host_online


class PulseGuestRunningBinarySensor(PulseGuestEntity, BinarySensorEntity):
    """VM-/Container-Running-Sensor."""

    entity_description = GUEST_RUNNING_DESCRIPTION

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        resource_id: str,
        *,
        containers: bool = False,
    ) -> None:
        super().__init__(coordinator, resource_id, "running", containers=containers)

    @property
    def is_on(self) -> bool | None:
        resource = self.resource
        if resource is None:
            return None
        return resource.is_running


class PulseInfrastructureProblemBinarySensor(PulseEntity, BinarySensorEntity):
    """Gesamtproblem-Sensor für Vulpo."""

    entity_description = INFRASTRUCTURE_PROBLEM_DESCRIPTION

    def __init__(self, coordinator: PulseDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "infrastructure_problem")
        self._known_hosts: set[str] = set()

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None or "resources" in data.stale or "alerts" in data.stale:
            return None

        self._known_hosts.update(data.hosts)
        if any(alert.is_critical for alert in data.alerts):
            return True

        critical_hosts = self._critical_host_ids()
        if not critical_hosts:
            return None if not data.hosts else False
        if any(host_id not in data.hosts or not data.hosts[host_id].is_host_online for host_id in critical_hosts):
            return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        critical_hosts = self._critical_host_ids()
        triggering_hosts = [
            {"id": host_id, "name": data.hosts[host_id].name if host_id in data.hosts else host_id}
            for host_id in sorted(critical_hosts)
            if host_id not in data.hosts or not data.hosts[host_id].is_host_online
        ]
        triggering_alerts = [
            {
                "id": alert.alert_id,
                "level": alert.level,
                "type": alert.type,
                "resource_id": alert.resource_id,
            }
            for alert in data.alerts
            if alert.is_critical
        ]
        return {
            "triggering_hosts": triggering_hosts,
            "triggering_alerts": triggering_alerts,
        }

    def _critical_host_ids(self) -> set[str]:
        data = self.coordinator.data
        if data is None:
            return set()
        alias_map = dict(self._entry.options.get(CONF_ALIAS_MAP, {}))
        self._known_hosts = remap_alias_ids(self._known_hosts, alias_map)
        mode = self._entry.options.get(CONF_CRITICAL_HOSTS_MODE, CRITICAL_MODE_ALL)
        if mode == CRITICAL_MODE_SELECTED:
            return remap_alias_ids(self._entry.options.get(CONF_CRITICAL_HOSTS, []), alias_map)
        self._known_hosts.update(data.hosts)
        return set(self._known_hosts)
