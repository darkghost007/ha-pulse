# Pulse für Home Assistant

Custom-Integration für [Pulse](https://github.com/rcourtman/Pulse). Die
Integration liest pro Polling-Zyklus genau einmal `GET /api/state` und bildet
Hosts, VMs, Docker-Container, Storage und aktive Alerts als Home-Assistant-
Devices und Entities ab.

## Status

Version 0.2.0 für Pulse 6.3.1. Die Integration ist read-only und nutzt
den Header `X-API-Token` mit einem Token, der nur den Scope `monitoring:read`
benötigt.

## Einrichtung

1. Repository als HACS-Custom-Repository hinzufügen.
2. Home Assistant neu starten.
3. Integration `Pulse` hinzufügen.
4. Pulse-URL und API-Token eintragen.

Die URL darf nur `http` oder `https` verwenden. Zugangsdaten in der URL, Query-
Parameter und Fragmente werden abgelehnt. Bei `http://` verlangt der Config-Flow
eine explizite Bestätigung, weil Token und Infrastruktur-Topologie unverschlüsselt
übertragen werden.

## Optionen

- `scan_interval`: Standard 60 s, Minimum 15 s.
- `include_guests`: VMs als eigene Devices und Entities anlegen.
- `include_docker_containers`: Docker-Container anlegen; standardmäßig aus, weil
  reale Pulse-Instanzen sehr viele Container-Ressourcen liefern können.
- `critical_hosts_mode`: Entweder alle aktuellen und zukünftigen Hosts oder eine
  explizite Auswahl für den Gesamtproblem-Sensor.

## Entities

Pro Host entstehen Online-, CPU-, Arbeitsspeicher-, Storage-, Uptime- und Status-Entities;
Temperatur wird nur angelegt, wenn Pulse einen Wert liefert. Storage-Ressourcen
erhalten Usage-, Used- und Total-Sensoren. VMs und optionale Docker-Container
erhalten Running-, CPU-, Speicher- und Disk-Entities.

Am Hub-Device entstehen Zähler für aktive Alerts, Host-/VM-/Container-Status und
`binary_sensor.pulse_infrastructure_problem`. Bei `device_class: problem` bedeutet
`on`, dass ein kritischer Host offline ist oder ein kritischer Alert aktiv ist.
Zusätzlich liefert `sensor.pulse_gesamtstatus` einen anzeigefertigen Zustand
`ok`, `warning` oder `problem`; `sensor.pulse_warnungen` zählt Warn-Alerts und
erreichbare, aber degradierte Hosts, `sensor.pulse_kritische_alarme` zählt
kritische Alerts.

Pro Host gibt es Container- und Gästezähler unabhängig davon, ob Docker-Container
als eigene Entities aktiviert sind: laufende/gestoppte Container,
Container-Probleme sowie laufende/gestoppte Gäste.

## Sicherheit

Tokens, rohe aiohttp-Objekte und API-Rohpayloads werden nicht geloggt. Diagnostics
werden aus einer Allowlist synthetisiert und enthalten keine echten Hostnamen,
IPs, Pfade, Tags, Alert-Texte oder Tokens.

## Bekannte Einschränkungen

Libvirt-Gäste können in Pulse `memory.current=100` mit `used == total` und
`free == 0` melden, obwohl keine verwertbaren Balloon-Werte vorliegen. Die
Integration behandelt diesen Prozentwert deshalb als unbekannt, damit Vulpo
keinen dauerhaften RAM-Fehlalarm für laufende oder gestoppte Gäste zeigt.

## Entwicklung

```bash
/opt/homebrew/bin/python3.14 -m venv .venv
uv pip install --python .venv/bin/python homeassistant==2026.8.3 pytest pytest-homeassistant-custom-component aioresponses syrupy
.venv/bin/python -m pytest
```

Fixtures unter `tests/fixtures/` sind synthetisch. Echte Pulse-Dumps gehören nie
ins Repository.
