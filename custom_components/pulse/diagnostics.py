"""Diagnostics für Pulse ohne Rohpayload oder Secrets."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .coordinator import PulseDataUpdateCoordinator, PulseResource


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Liefert eine sichere, allowlist-basierte Diagnose."""

    coordinator: PulseDataUpdateCoordinator | None = getattr(entry, "runtime_data", None)
    data = coordinator.data if coordinator is not None else None
    if data is None:
        return {
            "entry": {"has_data": False},
            "resources": {},
            "alerts": {},
        }

    return {
        "entry": {
            "has_data": True,
            "scan_interval": coordinator.update_interval.total_seconds()
            if coordinator.update_interval is not None
            else None,
        },
        "summary": {
            "active_alerts": data.summary.active_alerts,
            "hosts_online": data.summary.hosts_online,
            "hosts_offline": data.summary.hosts_offline,
            "vms_running": data.summary.vms_running,
            "vms_stopped": data.summary.vms_stopped,
            "containers_running": data.summary.containers_running,
            "containers_stopped": data.summary.containers_stopped,
        },
        "resources": {
            "hosts": [_resource_diag(item, f"host_{index}") for index, item in enumerate(data.hosts.values(), 1)],
            "guests": [_resource_diag(item, f"guest_{index}") for index, item in enumerate(data.guests.values(), 1)],
            "containers": [
                _resource_diag(item, f"container_{index}") for index, item in enumerate(data.containers.values(), 1)
            ],
            "storages": [
                _resource_diag(item, f"storage_{index}") for index, item in enumerate(data.storages.values(), 1)
            ],
            "physical_disks": [
                _resource_diag(item, f"physical_disk_{index}")
                for index, item in enumerate(data.physical_disks.values(), 1)
            ],
            "ignored_types": dict(sorted(data.ignored_types.items())),
        },
        "alerts": {
            "by_level": _alert_counts(data.alerts),
        },
        "stale": sorted(data.stale),
    }


def _resource_diag(resource: PulseResource, pseudo_id: str) -> dict[str, Any]:
    return {
        "id": pseudo_id,
        "type": resource.type,
        "status": resource.status,
        "source_type": resource.source_type,
        "cpu_usage": resource.cpu_usage,
        "memory_usage": resource.memory_usage,
        "storage_usage": resource.storage_usage,
        "has_temperature": resource.temperature is not None,
        "has_uptime": resource.uptime_seconds is not None,
    }


def _alert_counts(alerts) -> dict[str, int]:
    counts: dict[str, int] = {}
    for alert in alerts:
        key = alert.level or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
