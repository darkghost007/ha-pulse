"""Konstanten für die Pulse-Integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "pulse"

CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_ALLOW_INSECURE: Final = "allow_insecure"
CONF_INCLUDE_GUESTS: Final = "include_guests"
CONF_INCLUDE_CONTAINERS: Final = "include_docker_containers"
CONF_CRITICAL_HOSTS_MODE: Final = "critical_hosts_mode"
CONF_CRITICAL_HOSTS: Final = "critical_hosts"
CONF_KNOWN_HOSTS: Final = "known_hosts"
CONF_ALIAS_MAP: Final = "alias_map"

CRITICAL_MODE_ALL: Final = "all"
CRITICAL_MODE_SELECTED: Final = "selected"

DEFAULT_SCAN_INTERVAL: Final = 60
MIN_SCAN_INTERVAL: Final = 15
REQUEST_TIMEOUT: Final = 15

# Ressourcentypen, die Pulse liefert. Der Rest (docker-volume, docker-image,
# docker-network) ist reines Rauschen — bei einer realen Instanz 486 von 591
# Ressourcen — und wird nie zu Entities.
TYPE_HOST: Final = "agent"
TYPE_VM: Final = "vm"
TYPE_CONTAINER: Final = "app-container"
TYPE_STORAGE: Final = "storage"
TYPE_DISK: Final = "physical_disk"

HOST_TYPES: Final = frozenset({TYPE_HOST})
GUEST_TYPES: Final = frozenset({TYPE_VM})
CONTAINER_TYPES: Final = frozenset({TYPE_CONTAINER})
STORAGE_TYPES: Final = frozenset({TYPE_STORAGE})
PHYSICAL_DISK_TYPES: Final = frozenset({TYPE_DISK})

#: Status, die einen laufenden Gast kennzeichnen.
RUNNING_STATES: Final = frozenset({"running", "online"})
#: Status, die einen gesunden Host kennzeichnen.
HEALTHY_STATES: Final = frozenset({"online", "active", "running"})
#: Status, bei denen ein Host nicht erreichbar ist. `degraded` gehört bewusst
#: NICHT dazu: ein Host mit Warnung ist erreichbar und darf in einem
#: connectivity-Sensor nicht als offline erscheinen.
OFFLINE_STATES: Final = frozenset({"offline", "unreachable", "missing", "error", "unknown"})

PLATFORMS: Final = [Platform.BINARY_SENSOR, Platform.SENSOR]

ATTRIBUTION: Final = "Data provided by Pulse"

# Kurzbezeichnungen für Alarmtypen. Die rohen Pulse-Typen sind maschinenlesbar,
# aber in einer Geräteübersicht schwer zu erfassen. Unbekannte Typen werden
# unverändert durchgereicht.
ALERT_TYPE_LABELS: dict[str, str] = {
    "docker-container-health": "ungesund",
    "docker-container-state": "gestoppt",
    "docker-container-oom-kill": "Speicherüberlauf",
    "storage-topology": "Speicher-Topologie",
    "node-offline": "offline",
    "guest-offline": "offline",
}

# Kurzbezeichnungen für Ressourcen-Zustände, analog zu ALERT_TYPE_LABELS.
STATUS_LABELS: dict[str, str] = {
    "online": "online",
    "offline": "offline",
    "degraded": "beeinträchtigt",
    "warning": "Warnung",
    "failed": "ausgefallen",
    "error": "Fehler",
    "unreachable": "nicht erreichbar",
    "active": "aktiv",
    "running": "läuft",
    "stopped": "gestoppt",
    "unknown": "unbekannt",
    "missing": "nicht gemeldet",
}
