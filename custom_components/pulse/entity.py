"""Entity-Basisklassen für Pulse."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, CONF_ALIAS_MAP, DOMAIN
from .coordinator import PulseDataUpdateCoordinator, PulseResource, remap_alias_id

ResourceGetter = Callable[[Any], dict[str, PulseResource]]


class PulseEntity(CoordinatorEntity[PulseDataUpdateCoordinator]):
    """Gemeinsame Basis für alle Pulse-Entities."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: PulseDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._entry = coordinator.config_entry
        self._key = key
        self._attr_unique_id = f"{self._entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            manufacturer="Pulse",
            name="Pulse",
            configuration_url=self._entry.data.get(CONF_HOST),
        )


class PulseResourceEntity(PulseEntity):
    """Basis für ressourcenbezogene Entities."""

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        resource_id: str,
        key: str,
        resource_getter: ResourceGetter,
    ) -> None:
        super().__init__(coordinator, f"{resource_id}_{key}")
        self._resource_id = resource_id
        self._resource_getter = resource_getter
        self._attr_unique_id = f"{self._entry.entry_id}_{resource_id}_{key}"

    @property
    def resource(self) -> PulseResource | None:
        data = self.coordinator.data
        if data is None:
            return None
        return self._resource_getter(data).get(self.current_resource_id)

    @property
    def current_resource_id(self) -> str:
        return remap_alias_id(self._resource_id, dict(self._entry.options.get(CONF_ALIAS_MAP, {})))

    @property
    def available(self) -> bool:
        return self.resource is not None and super().available

    @property
    def device_info(self) -> DeviceInfo:
        resource = self.resource
        current_resource_id = self.current_resource_id
        name = resource.name if resource is not None else current_resource_id
        via_device = (DOMAIN, self._entry.entry_id)
        if resource is not None and resource.parent_canonical_id:
            via_device = (DOMAIN, f"{self._entry.entry_id}_{resource.parent_canonical_id}")
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_{current_resource_id}")},
            manufacturer="Pulse",
            name=name,
            via_device=via_device,
        )


class PulseHostEntity(PulseResourceEntity):
    """Basis für Host-Entities."""

    def __init__(self, coordinator: PulseDataUpdateCoordinator, resource_id: str, key: str) -> None:
        super().__init__(coordinator, resource_id, key, lambda data: data.hosts)


class PulseGuestEntity(PulseResourceEntity):
    """Basis für VM-/Container-Entities."""

    def __init__(
        self,
        coordinator: PulseDataUpdateCoordinator,
        resource_id: str,
        key: str,
        *,
        containers: bool = False,
    ) -> None:
        getter = (lambda data: data.containers) if containers else (lambda data: data.guests)
        super().__init__(coordinator, resource_id, key, getter)


class PulseStorageEntity(PulseResourceEntity):
    """Basis für Storage-Entities."""

    def __init__(self, coordinator: PulseDataUpdateCoordinator, resource_id: str, key: str) -> None:
        super().__init__(coordinator, resource_id, key, lambda data: data.storages)


class PulsePhysicalDiskEntity(PulseResourceEntity):
    """Basis für physische Platten."""

    def __init__(self, coordinator: PulseDataUpdateCoordinator, resource_id: str, key: str) -> None:
        super().__init__(coordinator, resource_id, key, lambda data: data.physical_disks)
