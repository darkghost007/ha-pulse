"""Erzeugt synthetische Test-Fixtures aus einem echten Pulse-`/api/state`-Dump.

Allowlist-basiert: nur bekannte Feldnamen werden übernommen, jeder Identifier,
Name, Hostname, Pfad und jede IP wird durch ein stabiles Pseudonym ersetzt.
Der Rohdump bleibt außerhalb des Repos.

    python3 tests/make_fixtures.py ~/.local/state/pulse-api-samples/state.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Nur diese Felder wandern in die Fixtures.
METRIC_KEYS = {"current", "total", "used", "free"}
RATE_KEYS = {"rxBytes", "txBytes", "readRate", "writeRate"}
RESOURCE_KEYS = {
    "id", "type", "technology", "name", "displayName", "platformType",
    "sourceType", "sources", "status", "parentId", "parentName", "uptime",
    "temperature", "childCount",
}
ALERT_KEYS = {"id", "level", "type", "resourceId", "resourceName", "acknowledged"}

# Wie viele Ressourcen je Typ ins Fixture wandern.
SAMPLE_PER_TYPE = {
    "agent": 4, "vm": 2, "storage": 2, "physical_disk": 2,
    "app-container": 3, "docker-volume": 1, "docker-image": 1, "docker-network": 1,
}


class Pseudonymizer:
    """Stabile, kollisionsfreie Pseudonyme pro Kategorie."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def get(self, value: str | None, kind: str) -> str | None:
        if value is None:
            return None
        key = f"{kind}\x00{value}"
        if key not in self._map:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._map[key] = f"{kind}-{self._counts[kind]}"
        return self._map[key]


def clean_metrics(block: object) -> dict | None:
    if not isinstance(block, dict):
        return None
    out = {k: v for k, v in block.items() if k in METRIC_KEYS and isinstance(v, (int, float))}
    return out or None


def clean_tags(value: object, p: Pseudonymizer) -> list[str] | None:
    if isinstance(value, str):
        tags = [value]
    elif isinstance(value, list):
        tags = []
        for item in value:
            if isinstance(item, str):
                tags.append(item)
            elif isinstance(item, dict):
                raw = item.get("name") or item.get("id") or item.get("tag")
                if isinstance(raw, str):
                    tags.append(raw)
    else:
        return None

    cleaned = [tag if tag in {"none", "zfs"} else p.get(tag, "tag") for tag in tags]
    return [tag for tag in cleaned if tag]


def clean_storage(block: object) -> dict | None:
    if not isinstance(block, dict):
        return None
    out = {
        key: value
        for key, value in block.items()
        if key in {"type", "kind"} and isinstance(value, str)
    }
    return out or None


def clean_physical_disk(block: object) -> dict | None:
    if not isinstance(block, dict):
        return None
    out: dict = {}
    for key in ("temperature", "wearout", "sizeBytes"):
        value = block.get(key)
        if isinstance(value, (int, float)):
            out[key] = value
    for key in ("health", "storageState", "diskType"):
        value = block.get(key)
        if isinstance(value, str):
            out[key] = value
    if isinstance(block.get("spunDown"), bool):
        out["spunDown"] = block["spunDown"]
    smart = block.get("smart")
    if isinstance(smart, dict) and isinstance(smart.get("powerOnHours"), (int, float)):
        out["smart"] = {"powerOnHours": smart["powerOnHours"]}
    return out or None


def clean_agent(block: object) -> dict | None:
    if not isinstance(block, dict):
        return None
    out = {}
    if isinstance(block.get("agentVersion"), str):
        out["agentVersion"] = block["agentVersion"]
    if isinstance(block.get("lastReportAt"), (int, float, str)):
        out["lastReportAt"] = block["lastReportAt"]
    return out or None


def clean_rates(block: object, keys: set[str]) -> dict | None:
    if not isinstance(block, dict):
        return None
    out = {key: value for key, value in block.items() if key in keys and isinstance(value, (int, float))}
    return out or None


def clean_health_block(block: object) -> dict | None:
    if not isinstance(block, dict):
        return None
    out = {}
    if isinstance(block.get("health"), str):
        out["health"] = block["health"]
    if isinstance(block.get("oomKilled"), bool):
        out["oomKilled"] = block["oomKilled"]
    return out or None


def clean_resource(res: dict, p: Pseudonymizer) -> dict:
    out: dict = {}
    for key in RESOURCE_KEYS:
        if key not in res:
            continue
        value = res[key]
        if key in ("id", "parentId"):
            value = p.get(value, "res")
        elif key in ("name", "displayName", "parentName"):
            value = p.get(value, "host")
        out[key] = value

    for block in ("cpu", "memory", "disk"):
        cleaned = clean_metrics(res.get(block))
        if cleaned is not None:
            out[block] = cleaned
    storage = clean_storage(res.get("storage"))
    if storage is not None:
        out["storage"] = storage
    physical_disk = clean_physical_disk(res.get("physicalDisk"))
    if physical_disk is not None:
        out["physicalDisk"] = physical_disk
    agent = clean_agent(res.get("agent"))
    if agent is not None:
        out["agent"] = agent
    network = clean_rates(res.get("network"), {"rxBytes", "txBytes"})
    if network is not None:
        out["network"] = network
    disk_io = clean_rates(res.get("diskIO"), {"readRate", "writeRate"})
    if disk_io is not None:
        out["diskIO"] = disk_io
    docker = clean_health_block(res.get("docker"))
    if docker is not None:
        out["docker"] = docker
    platform_data = clean_health_block(res.get("platformData"))
    if platform_data is not None:
        out["platformData"] = platform_data
    tags = clean_tags(res.get("tags"), p)
    if tags is not None:
        out["tags"] = tags

    canonical = res.get("canonicalIdentity")
    if isinstance(canonical, dict):
        out["canonicalIdentity"] = {
            "primaryId": p.get(canonical.get("primaryId"), "canon"),
            "aliases": [p.get(a, "canon") for a in canonical.get("aliases", [])],
        }
    return out


def should_prefer_resource(res: dict) -> bool:
    if res.get("type") == "physical_disk":
        physical_disk = res.get("physicalDisk")
        temperature = physical_disk.get("temperature") if isinstance(physical_disk, dict) else res.get("temperature")
        return isinstance(temperature, (int, float)) and temperature > 0
    if res.get("type") == "storage":
        tags = res.get("tags")
        if isinstance(tags, str):
            return tags == "zfs"
        if isinstance(tags, list):
            return any(tag == "zfs" or (isinstance(tag, dict) and tag.get("name") == "zfs") for tag in tags)
    return False


def pick_resources(resources: list[dict]) -> list[dict]:
    picked: list[dict] = []
    seen: dict[str, int] = {}
    ordered = sorted(resources, key=lambda item: not should_prefer_resource(item))
    for res in ordered:
        rtype = res.get("type")
        limit = SAMPLE_PER_TYPE.get(rtype, 0)
        if seen.get(rtype, 0) >= limit:
            continue
        seen[rtype] = seen.get(rtype, 0) + 1
        picked.append(res)
    return add_parent_chain(picked, resources)


def add_parent_chain(picked: list[dict], resources: list[dict]) -> list[dict]:
    by_id = {res.get("id"): res for res in resources if isinstance(res.get("id"), str)}
    output = list(picked)
    included = {res.get("id") for res in output}
    for res in list(picked):
        current = res
        seen: set[str] = set()
        for _ in range(32):
            parent_id = current.get("parentId")
            if not isinstance(parent_id, str) or parent_id in seen:
                break
            seen.add(parent_id)
            parent = by_id.get(parent_id)
            if parent is None:
                break
            if parent_id not in included:
                output.append(parent)
                included.add(parent_id)
            current = parent
    return output


def clean_connected_infrastructure(items: object, p: Pseudonymizer) -> list[dict]:
    if not isinstance(items, list):
        return []
    output = []
    for item in items:
        if not isinstance(item, dict):
            continue
        cleaned = {}
        if isinstance(item.get("name"), str):
            cleaned["name"] = p.get(item["name"], "infra")
        for key in ("healthStatus", "lastSeen", "version"):
            if isinstance(item.get(key), (str, int, float)):
                cleaned[key] = item[key]
        if cleaned:
            output.append(cleaned)
    return output


def clean_connection_health(items: object, p: Pseudonymizer) -> dict[str, bool]:
    if not isinstance(items, dict):
        return {}
    return {p.get(str(name), "infra"): value for name, value in items.items() if isinstance(value, bool)}


def main(source: Path, dest: Path) -> None:
    state = json.loads(source.read_text())
    p = Pseudonymizer()

    picked = [clean_resource(res, p) for res in pick_resources(state.get("resources", []))]

    alerts = []
    for alert in state.get("activeAlerts", [])[:4]:
        cleaned = {k: alert[k] for k in ALERT_KEYS if k in alert}
        cleaned["id"] = p.get(cleaned.get("id"), "alert")
        cleaned["resourceId"] = p.get(cleaned.get("resourceId"), "res")
        cleaned["resourceName"] = p.get(cleaned.get("resourceName"), "host")
        alerts.append(cleaned)

    fixture = {
        "resources": picked,
        "activeAlerts": alerts,
        "lastUpdate": 1787520028513,
        "temperatureMonitoringEnabled": False,
    }
    connected = clean_connected_infrastructure(state.get("connectedInfrastructure"), p)
    if connected:
        fixture["connectedInfrastructure"] = connected
    connection_health = clean_connection_health(state.get("connectionHealth"), p)
    if connection_health:
        fixture["connectionHealth"] = connection_health
    dest.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    print(f"{dest}: {len(picked)} Ressourcen, {len(alerts)} Alerts")


if __name__ == "__main__":
    src = Path(sys.argv[1]).expanduser()
    out = Path(__file__).parent / "fixtures" / "state.json"
    out.parent.mkdir(exist_ok=True)
    main(src, out)
