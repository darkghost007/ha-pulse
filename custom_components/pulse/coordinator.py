"""Coordinator und Normalisierung für Pulse."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PulseApiClient, PulseAuthError, PulseConnectionError, PulseApiError
from .const import (
    CONF_ALIAS_MAP,
    CONF_KNOWN_HOSTS,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    CONTAINER_TYPES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GUEST_TYPES,
    HEALTHY_STATES,
    OFFLINE_STATES,
    HOST_TYPES,
    PHYSICAL_DISK_TYPES,
    RUNNING_STATES,
    STORAGE_TYPES,
)

_LOGGER = logging.getLogger(__name__)

RESOURCE_ENTITY_KEYS = (
    "online",
    "running",
    "health",
    "cpu_usage",
    "memory_usage",
    "storage_usage",
    "disk_usage",
    "temperature",
    "disk_temperature",
    "disk_problems",
    "disk_life_remaining",
    "network_rx_rate",
    "network_tx_rate",
    "disk_read_rate",
    "disk_write_rate",
    "agent_version",
    "agent_last_report",
    "uptime",
    "status",
    "usage",
    "used",
    "total",
    "containers_running",
    "containers_stopped",
    "container_problems",
    "guests_running",
    "guests_stopped",
)
RESOURCE_ENTITY_PLATFORMS = ("sensor", "binary_sensor")
ENTITY_RESOURCE_TYPES = HOST_TYPES | GUEST_TYPES | CONTAINER_TYPES | STORAGE_TYPES


@dataclass(slots=True)
class PulseResource:
    """Normalisierte Pulse-Ressource."""

    resource_id: str
    canonical_id: str
    aliases: tuple[str, ...]
    identity_keys: tuple[str, ...]
    name: str
    type: str
    status: str | None
    source_type: str | None
    parent_resource_id: str | None
    parent_canonical_id: str | None = None
    host_canonical_id: str | None = None
    cpu_usage: float | None = None
    memory_usage: float | None = None
    storage_usage: float | None = None
    storage_used: int | None = None
    storage_total: int | None = None
    temperature: float | None = None
    uptime_seconds: float | None = None
    disk_health: str | None = None
    disk_storage_state: str | None = None
    disk_spun_down: bool | None = None
    disk_wearout: float | None = None
    agent_version: str | None = None
    agent_last_report_at: datetime | None = None
    network_rx_rate: float | None = None
    network_tx_rate: float | None = None
    disk_read_rate: float | None = None
    disk_write_rate: float | None = None
    docker_health: str | None = None
    docker_oom_killed: bool | None = None

    @property
    def is_running(self) -> bool:
        return self.status in RUNNING_STATES

    @property
    def is_host_online(self) -> bool:
        """Erreichbarkeit — `degraded` zählt als online, nur echte Ausfälle nicht."""

        return self.status is not None and self.status not in OFFLINE_STATES

    @property
    def is_host_healthy(self) -> bool:
        """Gesundheit — `degraded` zählt hier als Problem."""

        return self.status in HEALTHY_STATES


@dataclass(slots=True)
class PulseAlert:
    """Normalisierter Pulse-Alarm."""

    alert_id: str
    level: str | None
    type: str | None
    resource_id: str | None
    resource_name: str | None
    message: str | None
    since: datetime | None
    resolved_resource_id: str | None
    resolved_host_id: str | None
    acknowledged: bool

    @property
    def is_critical(self) -> bool:
        return self.level == "critical"


@dataclass(slots=True)
class PulseSummary:
    """Abgeleitete Zähler für Hub-Entities."""

    active_alerts: int
    hosts_online: int
    hosts_offline: int
    vms_running: int
    vms_stopped: int
    containers_running: int
    containers_stopped: int


@dataclass(slots=True)
class PulseData:
    """Vollständig normalisierter Pulse-Zustand."""

    hosts: dict[str, PulseResource]
    guests: dict[str, PulseResource]
    containers: dict[str, PulseResource]
    storages: dict[str, PulseResource]
    physical_disks: dict[str, PulseResource]
    alerts: list[PulseAlert]
    summary: PulseSummary
    last_update: datetime | None
    ignored_types: dict[str, int] = field(default_factory=dict)
    stale: set[str] = field(default_factory=set)
    removed_resource_ids: set[str] = field(default_factory=set)
    infrastructure_issues: list[dict[str, Any]] = field(default_factory=list)

    @property
    def resources(self) -> dict[str, PulseResource]:
        return {
            **self.hosts,
            **self.guests,
            **self.containers,
            **self.storages,
            **self.physical_disks,
        }


class PulseDataUpdateCoordinator(DataUpdateCoordinator[PulseData]):
    """Ein Coordinator pro Pulse-Instanz."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.api = PulseApiClient(
            async_get_clientsession(hass),
            entry.data["host"],
            entry.data["api_token"],
            verify_ssl=entry.data.get(CONF_VERIFY_SSL, True),
        )
        self._hass = hass
        interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    async def _async_update_data(self) -> PulseData:
        try:
            payload = await self.api.async_get_state()
        except PulseAuthError as err:
            raise ConfigEntryAuthFailed("Pulse API-Token wurde abgelehnt") from err
        except (PulseConnectionError, PulseApiError) as err:
            raise UpdateFailed(str(err)) from err
        data = normalize_state(payload)
        self._async_persist_identity_data(data)
        self._async_migrate_alias_unique_ids(data)
        return data

    def _async_persist_identity_data(self, data: PulseData) -> None:
        """Persistiert bekannte Hosts und Alias-Zuordnungen im Config-Entry."""

        alias_map = dict(self.config_entry.options.get(CONF_ALIAS_MAP, {}))
        for resource in data.resources.values():
            for alias in resource.aliases:
                if alias != resource.canonical_id:
                    alias_map[alias] = resource.canonical_id
        known_hosts = remap_alias_ids(self.config_entry.options.get(CONF_KNOWN_HOSTS, []), alias_map)
        known_hosts.update(data.hosts)

        if (
            sorted(known_hosts) != sorted(self.config_entry.options.get(CONF_KNOWN_HOSTS, []))
            or alias_map != dict(self.config_entry.options.get(CONF_ALIAS_MAP, {}))
        ):
            options = dict(self.config_entry.options)
            options[CONF_KNOWN_HOSTS] = sorted(known_hosts)
            options[CONF_ALIAS_MAP] = alias_map
            self._hass.config_entries.async_update_entry(self.config_entry, options=options)

    def _async_migrate_alias_unique_ids(self, data: PulseData) -> None:
        """Migriert Entity-Registry-IDs bei laufenden canonicalId-Wechseln."""

        registry = er.async_get(self._hass)
        device_registry = dr.async_get(self._hass)
        for old_id, new_id in self.config_entry.options.get(CONF_ALIAS_MAP, {}).items():
            _migrate_resource_identity(registry, device_registry, self.config_entry.entry_id, old_id, new_id)
        for resource in data.resources.values():
            for alias in resource.aliases:
                if alias != resource.canonical_id:
                    _migrate_resource_identity(
                        registry,
                        device_registry,
                        self.config_entry.entry_id,
                        alias,
                        resource.canonical_id,
                    )


def normalize_state(payload: dict[str, Any]) -> PulseData:
    """Normalisiert `/api/state` in stabile Ressourcentabellen."""

    stale: set[str] = set()
    raw_resources = payload.get("resources")
    if isinstance(raw_resources, list):
        resources = raw_resources
    else:
        resources = []
        stale.add("resources")

    by_resource_id: dict[str, str] = {}
    resource_types: dict[str, str | None] = {}
    parent_resource_ids: dict[str, str | None] = {}
    for raw in resources:
        if not isinstance(raw, dict):
            continue
        resource_id = _string(raw.get("id"))
        canonical_id = _canonical_id(raw)
        if resource_id:
            resource_types[resource_id] = _string(raw.get("type"))
            parent_resource_ids[resource_id] = _string(raw.get("parentId"))
        if resource_id and canonical_id:
            by_resource_id[resource_id] = canonical_id

    hosts: dict[str, PulseResource] = {}
    guests: dict[str, PulseResource] = {}
    containers: dict[str, PulseResource] = {}
    storages: dict[str, PulseResource] = {}
    physical_disks: dict[str, PulseResource] = {}
    ignored_types: dict[str, int] = {}
    removed_resource_ids: set[str] = set()

    for raw in resources:
        if not isinstance(raw, dict):
            stale.add("resources")
            continue
        resource_type = _string(raw.get("type"))
        model = _resource_from_raw(raw, by_resource_id, resource_types, parent_resource_ids)
        if model is None:
            ignored_types[resource_type or "missing"] = ignored_types.get(resource_type or "missing", 0) + 1
            if resource_type in ENTITY_RESOURCE_TYPES or resource_type is None:
                stale.add("resources")
            continue
        if resource_type in HOST_TYPES:
            hosts[model.canonical_id] = model
        elif resource_type in GUEST_TYPES:
            guests[model.canonical_id] = model
        elif resource_type in CONTAINER_TYPES:
            containers[model.canonical_id] = model
        elif resource_type in STORAGE_TYPES:
            if _should_skip_storage(raw, model):
                ignored_types[resource_type or "missing"] = ignored_types.get(resource_type or "missing", 0) + 1
                removed_resource_ids.add(model.canonical_id)
                continue
            storages[model.canonical_id] = model
        elif resource_type in PHYSICAL_DISK_TYPES:
            physical_disks[model.canonical_id] = model
            removed_resource_ids.add(model.canonical_id)
        else:
            ignored_types[resource_type or "missing"] = ignored_types.get(resource_type or "missing", 0) + 1

    exact_identity_index, hash_identity_index = _build_resource_identity_indexes(
        {
            **hosts,
            **guests,
            **containers,
            **storages,
            **physical_disks,
        }
    )

    raw_alerts = payload.get("activeAlerts")
    if isinstance(raw_alerts, list):
        alerts = []
        for item in raw_alerts:
            if isinstance(item, dict):
                alerts.append(
                    _alert_from_raw(
                        item,
                        {
                            **hosts,
                            **guests,
                            **containers,
                            **storages,
                            **physical_disks,
                        },
                        exact_identity_index,
                        hash_identity_index,
                    )
                )
            else:
                stale.add("alerts")
    else:
        alerts = []
        stale.add("alerts")

    summary = PulseSummary(
        active_alerts=len(alerts),
        hosts_online=sum(1 for host in hosts.values() if host.is_host_online),
        hosts_offline=sum(1 for host in hosts.values() if not host.is_host_online),
        vms_running=sum(1 for guest in guests.values() if guest.is_running),
        vms_stopped=sum(1 for guest in guests.values() if not guest.is_running),
        containers_running=sum(1 for container in containers.values() if container.is_running),
        containers_stopped=sum(1 for container in containers.values() if not container.is_running),
    )

    return PulseData(
        hosts=hosts,
        guests=guests,
        containers=containers,
        storages=storages,
        physical_disks=physical_disks,
        alerts=alerts,
        summary=summary,
        last_update=parse_pulse_time(payload.get("lastUpdate")),
        ignored_types=ignored_types,
        stale=stale,
        removed_resource_ids=removed_resource_ids,
        infrastructure_issues=_infrastructure_issues(payload),
    )


def _resource_from_raw(
    raw: dict[str, Any],
    by_resource_id: dict[str, str],
    resource_types: dict[str, str | None],
    parent_resource_ids: dict[str, str | None],
) -> PulseResource | None:
    resource_type = _string(raw.get("type"))
    if resource_type not in HOST_TYPES | GUEST_TYPES | CONTAINER_TYPES | STORAGE_TYPES | PHYSICAL_DISK_TYPES:
        return None

    resource_id = _string(raw.get("id"))
    canonical_id = _canonical_id(raw)
    if not resource_id or not canonical_id:
        return None

    parent_resource_id = _string(raw.get("parentId"))
    disk = raw.get("disk") if isinstance(raw.get("disk"), dict) else {}
    memory = raw.get("memory") if isinstance(raw.get("memory"), dict) else {}
    cpu = raw.get("cpu") if isinstance(raw.get("cpu"), dict) else {}
    physical_disk = raw.get("physicalDisk") if isinstance(raw.get("physicalDisk"), dict) else {}
    agent = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
    network = raw.get("network") if isinstance(raw.get("network"), dict) else {}
    disk_io = raw.get("diskIO") if isinstance(raw.get("diskIO"), dict) else {}
    docker = raw.get("docker") if isinstance(raw.get("docker"), dict) else {}
    platform_data = raw.get("platformData") if isinstance(raw.get("platformData"), dict) else {}

    is_guest = resource_type in GUEST_TYPES | CONTAINER_TYPES
    is_running = _string(raw.get("status")) in RUNNING_STATES
    storage_usage = normalize_percent(disk.get("current")) if isinstance(disk, dict) else None
    cpu_usage = normalize_percent(cpu.get("current")) if isinstance(cpu, dict) else None
    memory_usage = normalize_memory_percent(memory)
    if is_guest and not is_running:
        cpu_usage = None
        memory_usage = None
        storage_usage = None

    return PulseResource(
        resource_id=resource_id,
        canonical_id=canonical_id,
        aliases=tuple(_canonical_aliases(raw)),
        identity_keys=tuple(_resource_identity_keys(raw, resource_id, canonical_id, _canonical_aliases(raw))),
        name=_string(raw.get("displayName")) or _string(raw.get("name")) or canonical_id,
        type=resource_type,
        status=_string(raw.get("status")),
        source_type=_string(raw.get("sourceType")),
        parent_resource_id=parent_resource_id,
        parent_canonical_id=by_resource_id.get(parent_resource_id or ""),
        host_canonical_id=_host_canonical_id(resource_id, by_resource_id, resource_types, parent_resource_ids),
        cpu_usage=cpu_usage,
        memory_usage=memory_usage,
        storage_usage=storage_usage,
        storage_used=_positive_int(disk.get("used")) if isinstance(disk, dict) else None,
        storage_total=_positive_int(disk.get("total")) if isinstance(disk, dict) else None,
        temperature=_resource_temperature(raw),
        uptime_seconds=_positive_number(raw.get("uptime")),
        disk_health=_string(physical_disk.get("health")),
        disk_storage_state=_string(physical_disk.get("storageState")),
        disk_spun_down=physical_disk.get("spunDown") if isinstance(physical_disk.get("spunDown"), bool) else None,
        disk_wearout=_wearout(physical_disk.get("wearout")),
        agent_version=_string(agent.get("agentVersion")),
        agent_last_report_at=parse_pulse_time(agent.get("lastReportAt")),
        network_rx_rate=_positive_number(network.get("rxBytes")),
        network_tx_rate=_positive_number(network.get("txBytes")),
        disk_read_rate=_positive_number(disk_io.get("readRate")),
        disk_write_rate=_positive_number(disk_io.get("writeRate")),
        docker_health=_string(docker.get("health")) or _string(platform_data.get("health")),
        docker_oom_killed=_boolish(docker.get("oomKilled"))
        if _boolish(docker.get("oomKilled")) is not None
        else _boolish(platform_data.get("oomKilled")),
    )


def _host_canonical_id(
    resource_id: str,
    by_resource_id: dict[str, str],
    resource_types: dict[str, str | None],
    parent_resource_ids: dict[str, str | None],
) -> str | None:
    """Löst den nächsten Host-Vorfahren über die vollständige Roh-Ressourcen-Kette."""

    current = resource_id
    seen: set[str] = set()
    for _ in range(32):
        if current in seen:
            return None
        seen.add(current)
        resource_type = resource_types.get(current)
        if resource_type in HOST_TYPES:
            return by_resource_id.get(current)
        parent_id = parent_resource_ids.get(current)
        if parent_id is None:
            return None
        current = parent_id
    return None


def _should_skip_storage(raw: dict[str, Any], resource: PulseResource) -> bool:
    """Filtert Unraid-Mitglieder und leere Storage-Schatten ohne stale zu setzen."""

    if resource.storage_total is None or resource.storage_total <= 0:
        return True
    if _storage_kind(raw) == "unraid-cache-pool" and "zfs" not in _resource_tags(raw):
        return True
    return False


def _storage_kind(raw: dict[str, Any]) -> str | None:
    storage = raw.get("storage")
    if isinstance(storage, dict):
        value = _string(storage.get("type")) or _string(storage.get("kind"))
        if value:
            return value
    return (
        _string(raw.get("storageType"))
        or _string(raw.get("storage_type"))
        or _string(raw.get("subType"))
        or _string(raw.get("subtype"))
    )


def _resource_tags(raw: dict[str, Any]) -> set[str]:
    tags = raw.get("tags")
    if isinstance(tags, str):
        return {tags}
    if not isinstance(tags, list):
        return set()
    output: set[str] = set()
    for item in tags:
        if isinstance(item, str) and item:
            output.add(item)
        elif isinstance(item, dict):
            value = _string(item.get("name")) or _string(item.get("id")) or _string(item.get("tag"))
            if value:
                output.add(value)
    return output


def _resource_temperature(raw: dict[str, Any]) -> float | None:
    physical_disk = raw.get("physicalDisk")
    if isinstance(physical_disk, dict):
        temperature = _number(physical_disk.get("temperature"))
        if temperature is not None:
            return temperature
    return _number(raw.get("temperature"))


def _wearout(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return min(number, 100.0)


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _infrastructure_issues(payload: dict[str, Any]) -> list[dict[str, Any]]:
    connected = payload.get("connectedInfrastructure")
    if isinstance(connected, list):
        output: list[dict[str, Any]] = []
        for item in connected:
            if not isinstance(item, dict):
                continue
            status = _string(item.get("healthStatus"))
            if status is None or status in {"online", "active"}:
                continue
            output.append(
                {
                    "name": _string(item.get("name")) or "unknown",
                    "status": status,
                    "last_seen": item.get("lastSeen"),
                    "version": _string(item.get("version")),
                    "problem": status in {"offline", "failed", "error", "unreachable"},
                    "source": "connectedInfrastructure",
                }
            )
        return output

    connection_health = payload.get("connectionHealth")
    if not isinstance(connection_health, dict):
        return []
    return [
        {
            "name": str(name),
            "status": "offline",
            "last_seen": None,
            "version": None,
            "problem": True,
            "source": "connectionHealth",
        }
        for name, ok in connection_health.items()
        if ok is False
    ]


def _resource_identity_keys(
    raw: dict[str, Any],
    resource_id: str,
    canonical_id: str,
    aliases: list[str],
) -> list[str]:
    """Sammelt alle im Payload bekannten Kennungen einer Ressource."""

    keys = [canonical_id, resource_id, *aliases]
    metrics_target = raw.get("metricsTarget")
    if isinstance(metrics_target, dict):
        value = _string(metrics_target.get("resourceId"))
        if value:
            keys.append(value)
    elif isinstance(metrics_target, str):
        keys.append(metrics_target)

    docker = raw.get("docker")
    if isinstance(docker, dict):
        container_id = _string(docker.get("containerId"))
        agent_id = _string(docker.get("agentId"))
        if container_id:
            keys.append(container_id)
        if agent_id and container_id:
            keys.append(f"docker:{agent_id}/{container_id}")

    seen: set[str] = set()
    return [key for key in keys if key and not (key in seen or seen.add(key))]


def _build_resource_identity_indexes(
    resources: dict[str, PulseResource],
) -> tuple[dict[str, str], dict[str, str]]:
    """Baut exakte und Hash-Fallback-Indizes ohne mehrdeutige Treffer."""

    exact_candidates: dict[str, set[str]] = {}
    hash_candidates: dict[str, set[str]] = {}
    for resource in resources.values():
        for key in resource.identity_keys:
            exact_candidates.setdefault(key, set()).add(resource.canonical_id)
            normalized = _normalized_identity_key(key)
            if normalized:
                hash_candidates.setdefault(normalized, set()).add(resource.canonical_id)

    exact_index = {key: next(iter(ids)) for key, ids in exact_candidates.items() if len(ids) == 1}
    hash_index = {key: next(iter(ids)) for key, ids in hash_candidates.items() if len(ids) == 1}
    return exact_index, hash_index


def _normalized_identity_key(value: str | None) -> str | None:
    """Reduziert heterogene Pulse-IDs auf den stabilen Hash-Anteil."""

    if not value:
        return None
    tail = value.rsplit("/", 1)[-1]
    if ":" in tail:
        tail = tail.split(":", 1)[1]
    return tail or None


def _resolve_alert_resource_id(
    resource_id: str | None,
    exact_identity_index: dict[str, str],
    hash_identity_index: dict[str, str],
) -> str | None:
    if resource_id is None:
        return None
    if resource_id in exact_identity_index:
        return exact_identity_index[resource_id]
    normalized = _normalized_identity_key(resource_id)
    if normalized is None:
        return None
    return hash_identity_index.get(normalized)


def _alert_from_raw(
    raw: dict[str, Any],
    resources: dict[str, PulseResource],
    exact_identity_index: dict[str, str],
    hash_identity_index: dict[str, str],
) -> PulseAlert:
    raw_resource_id = _string(raw.get("resourceId"))
    resolved_resource_id = _resolve_alert_resource_id(raw_resource_id, exact_identity_index, hash_identity_index)
    resolved_resource = resources.get(resolved_resource_id or "")
    return PulseAlert(
        alert_id=_string(raw.get("id")) or "unknown",
        level=_string(raw.get("level")),
        type=_string(raw.get("type")),
        resource_id=raw_resource_id,
        resource_name=_string(raw.get("resourceName")),
        message=_string(raw.get("message")),
        since=parse_pulse_time(raw.get("startTime")),
        resolved_resource_id=resolved_resource_id,
        resolved_host_id=resolved_resource.host_canonical_id if resolved_resource else None,
        acknowledged=bool(raw.get("acknowledged")),
    )


def normalize_percent(value: Any) -> float | None:
    """Normalisiert Prozentwerte; negative oder ungültige Werte sind unbekannt."""

    number = _number(value)
    if number is None or number < 0:
        return None
    return number


def normalize_memory_percent(memory: object) -> float | None:
    """Normalisiert RAM-Prozente und filtert bekannte libvirt-Artefakte."""

    if not isinstance(memory, dict):
        return None
    if memory.get("used") == memory.get("total") and memory.get("free") == 0:
        return None
    return normalize_percent(memory.get("current"))


def parse_pulse_time(value: Any) -> datetime | None:
    """Parst Pulse-Zeitstempel aus Epoch-Millisekunden oder ISO-Strings."""

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            return None
    return None


def boot_time_from_uptime(observed_at: datetime | None, uptime_seconds: float | None) -> datetime | None:
    """Berechnet den HA-konformen Uptime-Zeitstempel."""

    if observed_at is None or uptime_seconds is None or uptime_seconds < 0:
        return None
    return observed_at - timedelta(seconds=uptime_seconds)


def _canonical_id(raw: dict[str, Any]) -> str | None:
    canonical = raw.get("canonicalIdentity")
    if isinstance(canonical, dict):
        return _string(canonical.get("primaryId"))
    return None


def _canonical_aliases(raw: dict[str, Any]) -> list[str]:
    canonical = raw.get("canonicalIdentity")
    aliases = canonical.get("aliases") if isinstance(canonical, dict) else []
    if not isinstance(aliases, list):
        return []
    return [alias for alias in (_string(alias) for alias in aliases) if alias]


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _positive_number(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return number


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _migrate_resource_unique_ids(
    registry: er.EntityRegistry,
    entry_id: str,
    old_resource_id: str,
    new_resource_id: str,
) -> None:
    for key in RESOURCE_ENTITY_KEYS:
        old_unique_id = f"{entry_id}_{old_resource_id}_{key}"
        new_unique_id = f"{entry_id}_{new_resource_id}_{key}"
        for platform in RESOURCE_ENTITY_PLATFORMS:
            entity_id = registry.async_get_entity_id(platform, DOMAIN, old_unique_id)
            if entity_id and registry.async_get_entity_id(platform, DOMAIN, new_unique_id) is None:
                registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


def _migrate_resource_identity(
    registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    entry_id: str,
    old_resource_id: str,
    new_resource_id: str,
) -> None:
    _migrate_resource_unique_ids(registry, entry_id, old_resource_id, new_resource_id)
    old_identifier = (DOMAIN, f"{entry_id}_{old_resource_id}")
    new_identifier = (DOMAIN, f"{entry_id}_{new_resource_id}")
    old_device = device_registry.async_get_device({old_identifier})
    if old_device is None:
        return
    identifiers = (old_device.identifiers - {old_identifier}) | {new_identifier}
    device_registry.async_update_device(old_device.id, new_identifiers=identifiers)


def async_cleanup_removed_resources(hass: HomeAssistant, entry: ConfigEntry, data: PulseData) -> None:
    """Entfernt Registry-Reste für nicht mehr als Geräte modellierte Ressourcen."""

    if not data.removed_resource_ids:
        return
    registry = er.async_get(hass)
    for resource_id in data.removed_resource_ids:
        _remove_resource_entities(registry, entry.entry_id, resource_id)

    device_registry = dr.async_get(hass)
    identifier_prefix = f"{entry.entry_id}_"
    for device in list(device_registry.devices.values()):
        if entry.entry_id not in device.config_entries:
            continue
        resource_ids = {
            identifier.removeprefix(identifier_prefix)
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier.startswith(identifier_prefix)
        }
        if resource_ids & data.removed_resource_ids:
            device_registry.async_remove_device(device.id)


def _remove_resource_entities(registry: er.EntityRegistry, entry_id: str, resource_id: str) -> None:
    for key in RESOURCE_ENTITY_KEYS:
        unique_id = f"{entry_id}_{resource_id}_{key}"
        for platform in RESOURCE_ENTITY_PLATFORMS:
            entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
            if entity_id:
                registry.async_remove(entity_id)


def remap_alias_id(resource_id: str, alias_map: dict[str, str]) -> str:
    """Löst persistierte Alias-IDs auf die aktuelle canonical ID auf."""

    seen: set[str] = set()
    current = resource_id
    while current in alias_map and current not in seen:
        seen.add(current)
        current = alias_map[current]
    return current


def remap_alias_ids(resource_ids, alias_map: dict[str, str]) -> set[str]:
    """Remappt eine Menge Ressourcen-IDs und entfernt dadurch abgelöste IDs."""

    return {remap_alias_id(resource_id, alias_map) for resource_id in resource_ids}
