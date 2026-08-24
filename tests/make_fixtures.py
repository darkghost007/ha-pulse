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

    canonical = res.get("canonicalIdentity")
    if isinstance(canonical, dict):
        out["canonicalIdentity"] = {
            "primaryId": p.get(canonical.get("primaryId"), "canon"),
            "aliases": [p.get(a, "canon") for a in canonical.get("aliases", [])],
        }
    return out


def main(source: Path, dest: Path) -> None:
    state = json.loads(source.read_text())
    p = Pseudonymizer()

    picked: list[dict] = []
    seen: dict[str, int] = {}
    for res in state.get("resources", []):
        rtype = res.get("type")
        limit = SAMPLE_PER_TYPE.get(rtype, 0)
        if seen.get(rtype, 0) >= limit:
            continue
        seen[rtype] = seen.get(rtype, 0) + 1
        picked.append(clean_resource(res, p))

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
    dest.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    print(f"{dest}: {len(picked)} Ressourcen, {len(alerts)} Alerts")


if __name__ == "__main__":
    src = Path(sys.argv[1]).expanduser()
    out = Path(__file__).parent / "fixtures" / "state.json"
    out.parent.mkdir(exist_ok=True)
    main(src, out)
