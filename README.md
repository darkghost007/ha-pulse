<img src="images/pulse-logo.png" alt="Pulse" width="96" align="right">

# Pulse für Home Assistant

Custom-Integration für [Pulse](https://github.com/rcourtman/Pulse). Die
Integration liest pro Polling-Zyklus genau einmal `GET /api/state` und bildet
Hosts, VMs, Docker-Container, Storage und aktive Alerts als Home-Assistant-
Devices und Entities ab.

## Status

Version 0.9.2 für Pulse 6.3.1. Die Integration ist read-only und nutzt
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
- `ignored_risk_codes`: Risiko-Gründe, die Pulse an einem Pool meldet, aber die
  bewusst hingenommen werden — etwa `unraid_no_parity` bei einem Array, das
  absichtlich ohne Parität läuft. Die Auswahl listet die Gründe, die die eigene
  Instanz gerade meldet.

## Was wird angezeigt

Pro Host steht in der Hauptansicht ein Gerätestatus `ok`, `warning` oder
`problem`. Er fasst Erreichbarkeit, Pulse-Warnungen, kritische Alarme und den
Status zugehöriger Pools zusammen; die auslösenden Ressourcen und Alarme stehen
als Attribute für die Detailansicht bereit.

Sind sämtliche Risiko-Gründe eines Pools über `ignored_risk_codes` abgewählt,
zählt dieser Pool nicht mehr als Warnung. Ein Host, den Pulse allein wegen
dieses Pools als `degraded` führt, gilt dann ebenfalls als `ok` — und sein
Statussensor meldet `online` statt `degraded`.

Maßgeblich dafür ist `agent.storageRisk`: Pulse bildet den Agentenstatus als
`storageStatus(host.Status, agent.StorageRisk)`, und `host.Status` kennt für
Agenten nur `online`/`offline`. `degraded` kann also ausschließlich aus diesem
Risiko stammen; Container und Gäste fließen dort nicht ein, ein RAID-Verbund
ohne eigene Ressource dagegen schon. Zusätzlich bleibt die Warnung stehen,
sobald ein Pool oder eine Platte unabhängig davon auffällig ist. Ein Host, der
nicht erreichbar ist, wird nie beschönigt — abgewählt werden Risiken, keine
Ausfälle.

Das Attribut `ignored_risks` an Gerätestatus und Statussensor benennt, was
abgewählt wurde. Der Spiegeleintrag aus `connectedInfrastructure` wird für
denselben Host mitgefiltert — zugeordnet über `scopeAgentId`, weil
Anzeigenamen nicht eindeutig sind; ein echter Ausfall dort bleibt unabhängig
davon stehen. Was Pulse selbst anzeigt, bleibt unverändert.

Alarme werden über alle bekannten Pulse-Kennungen der Ressource aufgelöst,
einschließlich Docker-IDs im Format `docker:<agent>/<container>`. Die
Detailattribute von Gerätestatus, Warnungen und kritischen Alarmen enthalten
lesbare Angaben wie Ressource, Typ, Meldung, Zeitpunkt, Quittierung und Host,
aber keine internen IDs oder Hashes.

In der Hauptansicht bleiben außerdem Online, CPU, Arbeitsspeicher, Storage,
Uptime und Temperatur. `Temperatur` ist ausschließlich der Host-eigene
Pulse-Wert; `Plattentemperatur` ist separat der höchste gültige Wert der
zugehörigen physischen Platten. Werte `<= 0` werden ignoriert. Physische Platten
erscheinen bewusst nicht als eigene Geräte oder Entities, sondern fließen in
Host-Details und Plattentemperatur ein.

Storage-Devices werden nur für echte Pools angelegt. Bei Unraid werden
Cache-Pool-Mitglieder und leere Array-Schatten übersprungen; für Vulpo reichen
die Pool-Auslastungen. Used-/Total-Werte, rohe Statuswerte und Zähler bleiben
als Diagnose-Entities erhalten.

Plattenzustand fließt in den Host-Gerätestatus ein. Zusätzlich gibt es pro Host
einen diagnostischen Zähler für Plattenprobleme und, falls Pulse verwertbare
Werte liefert, die niedrigste verbleibende Plattenlebensdauer. Seriennummern
werden nicht als Attribute ausgegeben.

Netzwerk- und Platten-I/O-Raten sowie Agent-Version und letzter Agent-Bericht
werden nur als Diagnose-Entities angelegt, wenn Pulse diese Werte für den Host
liefert. Die nativen Durchsatzwerte bleiben Byte pro Sekunde; Home Assistant
bekommt für neue Entities Megabyte pro Sekunde mit zwei Nachkommastellen als
Anzeigevorschlag.

Am Hub-Device entstehen zusätzlich Gesamtstatus, Warnungen, kritische Alarme,
aktive Alerts und Host-/VM-/Container-Zähler. `binary_sensor.pulse_infrastructure_problem`
bleibt für Automationen erhalten und wird `on`, wenn ein kritischer Host offline
ist oder ein kritischer Alert aktiv ist. Der Gesamtstatus berücksichtigt auch
Pulse-eigene Infrastrukturmeldungen aus `connectedInfrastructure` bzw. als
Fallback `connectionHealth`.

## Sicherheit

Tokens, rohe aiohttp-Objekte und API-Rohpayloads werden nicht geloggt. Diagnostics
werden aus einer Allowlist synthetisiert und enthalten keine echten Hostnamen,
IPs, Pfade, Tags, Alert-Texte oder Tokens.

## Bekannte Einschränkungen

Docker friert `health` beim Stoppen auf dem letzten Prüfergebnis ein. Ein seit
Wochen gestoppter Container meldet deshalb weiter `unhealthy`. Der Zähler
`Container-Probleme` misst `health` nur an laufenden Containern; ein
OOM-Abschuss oder ein unerwarteter Zustand zählt unabhängig davon.


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

## Lizenz

MIT — siehe [LICENSE](LICENSE).

## Logo und Zugehörigkeit

Dieses Projekt ist **nicht** von den Entwicklern von Pulse herausgegeben oder
unterstützt. Es ist eine unabhängige Integration, die die öffentliche Pulse-API
liest.

Das verwendete Logo stammt aus
[rcourtman/Pulse](https://github.com/rcourtman/Pulse/blob/main/docs/images/pulse-logo.png)
und gehört seinen Urhebern. Pulse steht unter der
[MIT-Lizenz](https://github.com/rcourtman/Pulse/blob/main/LICENSE), die die
Weiterverwendung erlaubt, aber keine Marken- oder Namensrechte überträgt. Das
Logo dient hier allein der Kenntlichmachung, mit welchem Dienst die Integration
spricht. Siehe [NOTICE](NOTICE).
