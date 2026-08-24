# Plan: Home-Assistant-Custom-Integration für Pulse (rcourtman/Pulse)

Stand: 2026-08-23 · Status: Entwurf (Plan-Checkpoint, noch nicht implementiert)

Ziel: Pulse bleibt zentrale Monitoring-Quelle. Eine eigene HACS-fähige
HA-Integration `pulse` spiegelt die Pulse-Daten als HA-Devices/-Entities,
Darstellung primär in Vulpo (iOS).

---

## 1. API-Grundlagen (recherchiert aus `docs/API.md` des Pulse-Repos)

Auth (3 Varianten, wir nutzen die erste):
- `X-API-Token: <token>` — empfohlen
- `Authorization: Bearer <token>`
- Session-Cookie (`pulse_session` + `pulse_csrf`) — für uns irrelevant

Relevante Endpunkte:

| Endpunkt | Scope | Nutzen für uns |
|---|---|---|
| `GET /api/health` | public | Config-Flow-Vorabtest, Erreichbarkeit ohne Token |
| `GET /api/version` | public | Diagnose, Versions-Gating |
| `GET /api/state` | `monitoring:read` | Vollzustand: Nodes, VMs, Container, Storage, Alerts |
| `GET /api/state/summary` | `monitoring:read` | Leichtgewichtige Zähler (activeAlerts, nodes, vms, containers, dockerHosts[], lastUpdate) |
| `GET /api/resources` | `monitoring:read` | Einheitliche, paginierte Ressourcenliste mit `type`/`source`/`status` |
| `GET /api/alerts/active` | `monitoring:read` | Aktive Alarme inkl. Severity |
| `GET /api/recovery/points` / `rollups` | `monitoring:read` | PBS-Backup-Läufe: letzter Erfolg, Fehlschläge |
| `GET /api/storage/` | `monitoring:read` | Storage-Auslastung je Node/Pool |
| `GET /api/agents/diagnostics` | admin | **nicht** verwenden (Adminrechte nötig) |

`/api/state/summary` Beispiel (aus Doku):
```json
{"activeAlerts":1,"nodes":2,"vms":8,"containers":12,
 "dockerHosts":[{"name":"Docker Host","containers":5,"uptimeSeconds":86400,"cpuUsagePercent":12.5}],
 "lastUpdate":"2026-05-24T10:11:12Z"}
```

`/api/resources` Filter/Parameter:
- `type`: agent, vm, system-container, container, docker-service, storage, pbs,
  pmg, k8s-cluster, k8s-node, pod, k8s-deployment, physical_disk, ceph
- `source`: proxmox, agent, docker, pbs, pmg, kubernetes
- `status`: online, offline, warning, unknown
- `page` (1), `limit` (default 50, **max 100**), `sort`, `order`

Wichtige Fallstricke:
- **Negativ-Sentinel**: Guest-Disk-Prozente sind `-1`, wenn die VM gestoppt ist
  oder kein Guest-Agent läuft; `diskStatusReason` erklärt den Grund. Die Doku
  weist Konsumenten an, **jeden negativen Wert** als „keine Daten" zu behandeln
  — also `< 0 → None`, nicht nur `== -1`. Nie als 0 % oder -1 % melden.
- **Pagination**: `limit` max. 100 → über alle Seiten iterieren, sonst fehlen
  Ressourcen ab der 101. still und leise.
- Pro-/Enterprise-Endpunkte liefern `402 Payment Required` → als „Feature nicht
  verfügbar" behandeln, nicht als Fehler.
- **Listen-Antwort ist unvollständig**: Die Doku sagt explizit, dass
  `GET /api/resources` „optimized for list views" ist und große, plattform-
  spezifische Felder nur `GET /api/resources/{id}` liefert. Wenn Temperatur oder
  Storage-Pool-Details in der Liste fehlen, muss je Host ein Detail-Call folgen
  (nur für Hosts, nicht für jeden Container — sonst explodiert die Requestzahl).
- **`/api/version` hat keine Instanz-ID**: liefert nur `version`, `buildTime`,
  `channel`, `deploymentType`, `updateAvailable`, `latestVersion`. Der
  Config-Entry-`unique_id` muss daher aus der normalisierten Host-URL+Port
  gebildet werden.
- Feldnamen der Detailschemata (`/api/state`, `/api/resources`) sind in der Doku
  nur teilweise dokumentiert → **Schritt 0 unten ist verpflichtend.**

---

## 1b. Live-Befund (Pulse 6.3.1, aufgenommen 2026-08-23)

Gegen `http://pulse.example:7655` aufgenommen. **Die Doku ist an mehreren
Stellen veraltet — maßgeblich ist dieser Abschnitt.**

### Was tatsächlich überwacht wird

| Host (`type: agent`) | Technologie | Status |
|---|---|---|
| `host-a` | linux | online |
| `host-b` | linux (Unraid) | **degraded** |
| `host-c` | linux | online |
| `host-d` | windows | online |

`nodes: 0` — **der Proxmox-Server war zum Aufnahmezeitpunkt offline.** Wichtig:
Im gesamten `/api/state` gibt es **keinerlei Spur** eines PVE-Nodes — keine
Ressource mit Status `offline`, kein Eintrag in `connectionHealth`. Die 4 VMs
sind libvirt-Gäste auf **host-b**, nicht auf dem PVE (`parentName: host-b`,
`technology: libvirt`); `platformType: proxmox-pve` ist nur Pulses generischer
Tag. Es gibt keinen Ressourcentyp `backup-host`, `node` oder `container` im Sinne der
Doku.

> **Offene, entwurfsrelevante Frage:** Lässt Pulse offline Nodes komplett aus
> `/api/state` fallen, oder ist der PVE gar nicht in Pulse konfiguriert? Mit
> `monitoring:read` nicht entscheidbar (`/api/config/nodes` → 403
> `missing_scope: settings:read`). Falls Pulse offline Hosts **verschwinden**
> lässt, kann `binary_sensor.<host>_online` seinen Offline-Zustand nicht aus
> dem Status ableiten — die Ressource ist dann einfach weg. Deshalb gilt:
> **Host-Online = Ressource vorhanden UND Status nicht offline/degraded.**
> Eine bekannte, aber im aktuellen Payload fehlende Host-Ressource ergibt
> `off` (nicht `unavailable`) — genau die Information, die in Vulpo zählt.
> Verifikation: erneute Aufnahme, sobald der PVE wieder läuft, plus ein Test
> mit einem gezielt abgeschalteten Host.

### Tatsächliche Ressourcentypen (591 gesamt)

| Typ | Anzahl | Für uns |
|---|---|---|
| `docker-volume` | 383 | **ignorieren** |
| `docker-image` | 83 | **ignorieren** |
| `app-container` | 70 | Docker-Container (optional) |
| `docker-network` | 20 | **ignorieren** |
| `physical_disk` | 14 | Platten-Health/Temperatur |
| `storage` | 13 | Pools (Unraid cache/array, ZFS) |
| `vm` | 4 | VMs |
| `agent` | 4 | **die Hosts** |

486 der 591 Ressourcen sind Docker-Rauschen. Ohne harte Typ-Allowlist erzeugt
die Integration hunderte sinnlose Entities.

### Feldstruktur (verifiziert)

- Metriken: `cpu.current` (%), `memory.current` (%) mit `total/used/free`
  (Bytes), `disk.current` (%) mit `total/used/free`, `uptime` (Sekunden),
  `network.rxBytes/txBytes`, `diskIO.readRate/writeRate`.
- **Kein `source`-Feld** — es heißt `sourceType` (String) und `sources` (Liste).
- **Kein `supersededIds`** in dieser Version. `canonicalIdentity` hat
  `primaryId` + `aliases`. Identität daher: `canonicalIdentity.primaryId` als
  Schlüssel, `aliases` für die Migrationserkennung.
- `parentId` verweist auf die Agent-Ressource → Host-Hierarchie funktioniert
  (VM „guest-a" → `agent-…` „host-b").
- `identity.ips` enthält **echte LAN- und IPv6-Adressen** → bestätigt die
  Diagnostics-Auflage.
- `policy.routing.redact` markiert sensible Ressourcen (z. B. Storage) bereits
  selbst — diese Liste als Basis für unsere Redaktion übernehmen.
- `status` ist **nicht** einheitlich: Hosts `online`/`degraded`, Guests
  `running`/`stopped`, dazu `unknown`. Getrennte Mappings nötig.
- **Kein `-1`-Sentinel beobachtet**: fehlende Guest-Disk-Daten äußern sich als
  **komplett fehlender `disk`-Schlüssel** (die VMs haben keinen). Beides
  behandeln: Schlüssel fehlt → `None`, Wert `< 0` → `None`.

### Alerts

`GET /api/state` → `activeAlerts` (mehrere aktive Einträge) mit den Feldern `id`, `level`
(`warning` / `critical`), `type`, `message`, `resourceId`, `resourceName`,
`node`, `instance`, `value`, `threshold`, `startTime`, `acknowledged`,
`metadata`. Severity heißt **`level`**, nicht `severity`.

### Was so nicht baubar ist

- **PBS-Backup-Sensoren**: `/api/recovery/rollups` und `/api/recovery/points`
  liefern beide `{"data":[],"meta":{"total":0}}`. Pulse sieht `host-a` nur als
  generischen Linux-Agent, nicht als Proxmox Backup Server. `last_backup`,
  `failed_backups`, `last_backup_status`, `datastore_usage` haben **keine
  Datenquelle**. → aus dem Umfang gestrichen, bis PBS in Pulse als PBS-Node
  eingebunden ist.
- **Temperatur**: `temperatureMonitoringEnabled: false`; genau **eine** von 591
  Ressourcen hat einen Wert (`63`). Temperatur-Sensor nur dynamisch anlegen,
  wenn das Feld gefüllt ist.
- **`GET /api/storage/`** ist kein Listen-Endpunkt: liefert HTTP 400
  `missing_storage_id`. Storage kommt aus den `type: storage`-Ressourcen.
- **`GET /api/version`** hat keine Instanz-ID (bestätigt): `version 6.3.1`,
  `buildTime`, `channel`, `deploymentType: docker`, `updateAvailable`.
- `lastUpdate` ist **uneinheitlich**: in `/api/state/summary` ein ISO-String,
  in `/api/state` ein Epoch-**Millisekunden**-Integer. Beide Formate parsen.

### Konsequenz für die Abfragestrategie

`GET /api/state` (1,4 MB) enthält bereits Ressourcen **und** Alerts. Ein
einziger Call pro Zyklus reicht — `/api/resources` und `/api/alerts/active`
entfallen. Bei 30 s Intervall sind das 1,4 MB/30 s; deshalb Default-Intervall
auf **60 s** anheben und im Options-Flow ab 15 s erlauben.

---

### Schritt 0 (erledigt am 2026-08-23): Live-Schema aufgenommen
Gegen die reale Pulse-Instanz je einmal `curl` auf `/api/health`, `/api/version`,
`/api/state/summary`, `/api/state`, `/api/resources?limit=100`,
`/api/alerts/active`, `/api/recovery/rollups` ausführen.

**Rohantworten bleiben außerhalb von Git.** Sie landen unter
`~/.local/state/pulse-api-samples/` (nicht im Repo). Ins Repo kommen nur
synthetische Fixtures, erzeugt von einem Sanitizer mit **Allowlist**: nur
bekannte Feldnamen und numerische Werte werden übernommen, jeder Identifier,
Name, Hostname, IP, URL, Pfad, Tag und Freitext wird durch ein stabiles
Pseudonym ersetzt (`host-1`, `10.0.0.1`, `pool-a`). Zusätzlich
`docs/pulse-api-samples/` in `.gitignore` eintragen, falls doch dort abgelegt
wird. Grund: Dieses Repo hat ein `origin` — ein roher `/api/state`-Dump wäre
die vollständige Topologie der Infrastruktur.

Die Sensorliste unten wird
gegen diese Samples verifiziert und ggf. korrigiert — die Doku ist die Hypothese,
die Samples sind die Wahrheit.

---

## 2. Architektur

Repo-Layout (HACS-kompatibel, eigenes Git-Repo `ha-pulse`):

```
custom_components/pulse/
  __init__.py          # async_setup_entry / unload / reload, Coordinator-Init
  api.py               # PulseApiClient (aiohttp, HA-Session)
  const.py             # DOMAIN, Defaults, Keys
  coordinator.py       # PulseDataUpdateCoordinator + normalisiertes Datenmodell
  config_flow.py       # User-Flow + Reauth + Options-Flow (Intervall)
  diagnostics.py       # async_get_config_entry_diagnostics (redacted)
  entity.py            # PulseEntity-Basisklasse (DeviceInfo, availability)
  sensor.py
  binary_sensor.py
  manifest.json
  strings.json
  translations/{en,de}.json
hacs.json
README.md
```

`manifest.json`: `"domain":"pulse"`, `"config_flow":true`, `"iot_class":"local_polling"`,
`"integration_type":"hub"`, `"requirements":[]` (nur `aiohttp` aus HA-Core),
`"version"` gepflegt (HACS-Pflicht).

Datenfluss:
```
DataUpdateCoordinator (Intervall, default 30 s)
  → api.get_state()        # ein Call: resources + activeAlerts + stats
  → Normalisierung in PulseData:
       hosts:      {resource_id: HostModel}     # Nodes/Agents/PBS/PMG/Docker-Hosts
       guests:     {resource_id: GuestModel}    # VMs/LXC/Docker-Container
       storages:   {resource_id: StorageModel}
       alerts:     [AlertModel]
       summary:    SummaryModel
  → Entities lesen nur aus PulseData (kein eigener I/O)
```

Ein Config-Entry = eine Pulse-Instanz. Nur **ein** Coordinator, alle Plattformen
teilen ihn (kein N+1-Polling).

### Dynamische Devices/Entities
- Nach jedem erfolgreichen Refresh: Set der bekannten Resource-IDs vergleichen.
  Neue IDs → `async_add_entities` über einen im Setup registrierten Callback.
- Verschwundene IDs → Entity bleibt bestehen, nie automatisch löschen
  (Pulse-Aussetzer würden sonst Historie zerreißen). Unterscheidung:
  ein fehlender **Host** setzt seinen `online`-Binary-Sensor auf `off` und die
  übrigen Sensoren des Hosts auf `unavailable`; ein fehlender **Gast/Storage**
  wird komplett `unavailable`.
  Aufräumen über `async_remove_config_entry_device` (User-initiiert).
- Device-Hierarchie: Pulse-Instanz = Hub-Device; Hosts = Devices mit
  `via_device` = Hub; VMs/LXC/Container = Devices mit `via_device` = Host.
  (Bewusst so: eigene Guest-Devices sind ausdrücklich gewünscht. `via_device`
  erhält die host-zentrische Sicht, Docker-Container sind per Default aus,
  damit die Device-Zahl nicht explodiert.)
- **Identität**: Schlüssel ist `canonicalIdentity.primaryId`
  (z. B. `agent:<machine-uuid>`, `vm:<machine-uuid>:libvirt:domain-<hash>`),
  **nicht** die Top-Level-`id` — die ist ein kurzer Hash und kann
  sich ändern. `unique_id` = `{entry_id}_{primary_id}_{key}`.
  Kein Name-basiertes Keying.
- **ID-Übergänge**: `supersededIds` gibt es in 6.3.1 **nicht**, wohl aber
  `canonicalIdentity.aliases`. Wechselt die `primaryId`, taucht die alte ID
  typischerweise in `aliases` der neuen Ressource auf. Beim Refresh wird darauf
  geprüft und der Registry-Eintrag per `async_update_entity(new_unique_id=…)`
  migriert statt neu angelegt; die Zuordnung wird im Config-Entry persistiert.
  Ohne das entstehen Duplikat-Entities und die Langzeitstatistik reißt ab.
  Greift die Heuristik nicht, ist das Ergebnis eine neue Entity — dokumentiert
  als akzeptiertes Restrisiko, nicht als stiller Datenverlust.

---

## 3. Entitäten

**Pro Host** (Proxmox-Node, PBS, Unraid/Agent, Docker-Host, Linux-Server):

| Entity | Platform | device_class | state_class | Unit |
|---|---|---|---|---|
| `online` | binary_sensor | connectivity | — | — |
| `cpu_usage` | sensor | — | measurement | % |
| `memory_usage` | sensor | — | measurement | % |
| `storage_usage` (root/größter Pool) | sensor | — | measurement | % |
| `temperature` (falls geliefert) | sensor | temperature | measurement | °C |
| `uptime` | sensor | uptime | — | — (Boot-Zeitpunkt) |
| `status` | sensor | enum | — | — |

`uptime` als `SensorDeviceClass.UPTIME` (Boot-Zeitpunkt) statt Sekundenzähler —
HA-Konvention, verhindert dauernd wechselnde States. `native_value` muss ein
**zeitzonenbewusstes `datetime`** sein (UTC), kein ISO-String.

Boot-Zeitpunkt = Beobachtungszeit − `uptimeSeconds`. Damit Polling-Jitter und
Uhrenversatz nicht bei jedem Refresh einen neuen Zeitstempel erzeugen, wird der
zuletzt gemeldete Wert beibehalten, solange die Neuberechnung um weniger als
60 s abweicht; größere Sprünge gelten als Reboot. Negative, fehlende oder
nicht-numerische `uptimeSeconds` → `None`. Kein `uptime_seconds`-Attribut
(Recorder-Churn); Vulpo rechnet die Dauer aus dem Zeitstempel.

**Pro Storage/Datastore**: `usage` (%, measurement), `used`/`total` (Bytes,
device_class `data_size`, state_class `measurement`).

**PBS-spezifisch**: **zurückgestellt** — siehe Abschnitt 1b. Pulse liefert
derzeit keine Backup-Daten (`/api/recovery/*` leer, `host-a` ist nur ein
Linux-Agent). Der Code hält den Pfad offen: sobald `recovery/rollups` Daten
liefert, werden `last_backup` (timestamp), `failed_backups_24h`,
`last_backup_status` und `datastore_usage` dynamisch ergänzt. Bis dahin werden
sie **nicht angelegt** — kein Dauer-`unknown` im Vulpo-Dashboard.

**Pro VM/LXC/Container** (opt-in, siehe Optionen): `running` (binary_sensor,
device_class running), `cpu_usage`, `memory_usage`, `disk_usage` (mit `-1`→`None`).

**Pulse-Gesamt** (am Hub-Device):
`active_alerts`, `hosts_online`, `hosts_offline`, `vms_running`, `vms_stopped`,
`containers_running`, `containers_stopped` (alle measurement),
`binary_sensor.pulse_infrastructure_problem` (device_class `problem`).

Bewusst **nicht** `infrastructure_ok` genannt: bei `device_class: problem`
bedeutet `on` = Problem. Ein Entity namens `..._ok`, das im Fehlerfall `on` ist,
wird in Vulpo und in Automationen zwangsläufig irgendwann falsch herum gelesen.
Vulpo zeigt „🟢 Alle Systeme OK", solange dieser Sensor `off` ist.

Logik: Problem, wenn (a) ein als kritisch geltender Host offline ist **oder**
(b) ≥1 aktiver Alert mit Severity `critical`.
- `unknown`, wenn die dafür nötigen Daten (Ressourcenliste oder Alerts) im
  aktuellen Zyklus veraltet sind — ein Teilausfall darf nie „gesund" melden.
- Leere Infrastruktur (0 Hosts) → `unknown`, nicht `off`.
- Kritische Hosts: Default ist „**alle aktuellen und zukünftigen Hosts**" als
  eigener Modus, nicht als abgehakte Liste — sonst zählt ein neu entdeckter
  Host stillschweigend nicht mit. Der Options-Flow erlaubt alternativ eine
  explizite Auswahl.
- Attribute listen die auslösenden Hosts/Alerts für die Vulpo-Detailansicht.

Icons nur wo HA keine sinnvolle Default-Ableitung hat (`mdi:server`,
`mdi:memory`, `mdi:harddisk`, `mdi:shield-check`).

---

## 4. Config Flow

Schritt `user`: `host` (URL, bevorzugt `https://pulse-server:7655`), `api_token`
(`TextSelector` password), `verify_ssl` (bool, default true), `scan_interval`
(int, default 30, min 10).

**URL-Validierung (hart):** nur Schema `http`/`https`, Host und optional
Port/Basispfad. `userinfo`, Query und Fragment werden **abgelehnt**. Die URL
wird normalisiert und so gespeichert.

**Redirects:** alle authentifizierten Requests mit `allow_redirects=False`.
aiohttp folgt sonst per Default und entfernt beim Cross-Origin-Redirect zwar
`Authorization`, **nicht** aber Custom-Header wie `X-API-Token` — ein
umkonfigurierter oder manipulierter Pulse-Endpunkt könnte den Token an eine
fremde Origin weiterreichen. Ein 3xx wird als Konfigurationsfehler gemeldet.

**HTTP statt HTTPS:** kein automatischer Downgrade nach TLS-Fehler. Bei
`http://` verlangt der Flow eine explizite Bestätigung mit Warnung (Token und
gesamte Infrastruktur-Topologie im Klartext im LAN). `verify_ssl=false` erzeugt
eine eigene, separate Warnung.

Token mit **minimalem Scope**: `monitoring:read` genügt für alle geplanten
Endpunkte. Es werden keine Admin-/`settings:*`-Endpunkte und keine schreibenden
Endpunkte aufgerufen — die Integration ist strikt read-only.

Validierung: `GET /api/health` (Erreichbarkeit) → dann `GET /api/state/summary`
(Token + Scope). Fehlerabbildung:
`cannot_connect` (ClientError/Timeout), `invalid_auth` (401/403),
`insufficient_scope` (403 mit Scope-Hinweis), `unknown`.

`unique_id` des Entry = normalisierte Host-URL + Port (`/api/version` liefert
**keine** Instanz-ID),
`_abort_if_unique_id_configured()` gegen Doppel-Einrichtung.

Reauth-Flow bei dauerhaftem 401 (`ConfigEntryAuthFailed`).
Options-Flow: `scan_interval`, `include_guests` (VMs/LXC als Entities, default an),
`include_docker_containers` (default **aus** — kann hunderte Entities erzeugen),
`critical_hosts_mode` (`all` = alle aktuellen und zukünftigen Hosts, Default |
`selected`) und, nur bei `selected`, `critical_hosts` (Mehrfachauswahl).

---

## 5. Fehlerbehandlung

| Situation | Verhalten |
|---|---|
| Pulse nicht erreichbar | `UpdateFailed` → alle Entities `unavailable`, Retry durch Coordinator |
| 401/403 | `ConfigEntryAuthFailed` → Reauth-Flow, kein Log-Spam |
| Timeout | `asyncio.timeout(15)` je Request → `UpdateFailed` |
| Host offline | Entity **verfügbar**, Wert `off`/`None` — nicht `unavailable` (sonst geht die Offline-Info in Vulpo verloren) |
| 402 Payment Required | Feature als nicht vorhanden markieren, einmalig `debug` loggen |
| Feld fehlt / Struktur geändert | defensives `.get()` mit `None`, Entity wird `unknown`; **eine** Warnung pro Entry+Feld, danach stumm |
| Teilausfall (ein Nebencall scheitert) | letzte bekannte Daten für den Teil behalten **und den Teil als „stale" markieren**; `UpdateFailed` nur wenn der Kern-Call (`summary`) scheitert. Aggregat-Sensoren, die von einem stale Teil abhängen (v. a. `infrastructure_problem`), werden `unknown` |

**Secrets und Diagnostics:**

- Alle aiohttp-Fehler werden **an der API-Grenze** in eigene, token-freie
  Exception-Typen (`PulseAuthError`, `PulseConnectionError`, `PulseApiError`)
  übersetzt. Nie werden Request-/Response-Objekte, Header, Bodies oder
  `repr(exc)` geloggt — aiohttp-Exceptions tragen Request-Infos inklusive
  Header mit sich.
- Diagnostics werden **nicht** durch Redaktion eines Roh-Payloads erzeugt,
  sondern aus einer expliziten **Allowlist** sicherer Felder: Zähler, Prozente,
  Typen, Status, Entity-Counts, Pulse-Version — plus stabile Pseudonyme statt
  echter Namen/IDs. Kein Roh-API-Payload im Output.
- Tests: der serialisierte Diagnostics-Output wird gegen **jeden** sensiblen
  Wert der Fixtures geprüft (Token, Hostnamen, IPs, Pfade, Tags, Alert-Texte),
  nicht nur gegen den Token. Dazu `caplog`-Tests für die Pfade Auth-Fehler,
  Redirect, Timeout, kaputte Antwort und aktiviertes Debug-Logging.

---

## 6. Vulpo-Aufbereitung

- Alle Prozentwerte als echte `%`-Sensoren mit `state_class: measurement` →
  Progress-Bars und Charts ohne Nachbearbeitung.
- Gesamtstatus oben: `binary_sensor.pulse_infrastructure_ok` +
  `sensor.pulse_active_alerts`.
- Pro Server eine Vulpo-Sektion entlang der HA-Devices (Devices sind bereits
  sauber gruppiert, kein manuelles Entity-Mapping nötig).
- Entity-Namen über `_attr_has_entity_name = True` + Translation-Keys, damit
  HA `Proxmox1 CPU-Auslastung` erzeugt.

---

## 7. Umsetzungsschritte (verifizierbar)

1. **Schema-Aufnahme** → verify: Rohdaten liegen außerhalb des Repos; die ins
   Repo committeten Fixtures enthalten keinen einzigen echten Hostnamen, keine
   IP, keinen Pfad und keinen Token (Test gegen Wertliste).
2. **api.py + Tests** (aiohttp-Mocks gegen die Fixtures) → verify: Test mit 250
   Ressourcen liefert genau 250 (Pagination über 3 Seiten); 401 → `PulseAuthError`,
   402 → Feature-Flag `False` ohne Exception, 500 → `PulseApiError`, Timeout →
   `PulseConnectionError`.
3. **Normalisierung/Modelle + Tests** → verify: parametrisierter Test über
   Disk-Prozent `-1 / -0.5 / 0 / 0.1 / 100 / None / "n/a"` → nur `0/0.1/100`
   liefern Zahlen, alles andere `None`; jede Fixture-Ressource wird genau einer
   Kategorie (host/guest/storage) zugeordnet, keine landet in „unbekannt";
   zwei Ressourcen unterschiedlicher `source` mit gleicher Quell-ID erzeugen
   zwei verschiedene `unique_id`s.
4. **config_flow + Tests** → verify: alle 4 Fehlerpfade getestet, Reauth
   getestet, URL-Validierung (userinfo/Query/Fragment abgelehnt) getestet,
   `allow_redirects=False` durch einen Redirect-Test belegt.
5. **coordinator + entity-Basis** → verify: ein Refresh löst genau die erwartete
   Request-Menge aus (kein Call pro Entity); Nebencall-Fehler setzt nur den
   betroffenen Teil auf `stale`, Kern-Call-Fehler setzt alle Entities auf
   `unavailable`.
6. **sensor.py / binary_sensor.py** → verify: Snapshot-Test (`syrupy`) über
   **exakte Anzahl** Devices und Entities plus je Entity `unique_id`,
   `native_value`, `device_class`, `state_class`, `unit`, `available`.
   `infrastructure_problem`: eigener Test je Fall — leer → `unknown`,
   alles online → `off`, kritischer Host offline → `on`, critical-Alert → `on`,
   Alert-Teil stale → `unknown`.
7. **Dynamik + Identität** → verify: (a) Refresh 2 ohne Host X → Entity bleibt
   registriert, `available=False`; Refresh 3 mit Host X → **dieselbe**
   `entity_id`, keine `_2`-Duplikate. (b) Ressource mit neuer kanonischer ID und
   alter ID in `supersededIds` → Registry-Eintrag migriert, `entity_id` und
   Statistik-ID unverändert. (c) HA-Neustart während Pulse offline → Entities
   kommen zurück, keine Neuanlage. (d) `include_docker_containers` an/aus →
   erwartete Entity-Zahl vorher/nachher.
8. **diagnostics.py** → verify: der serialisierte Output enthält keinen der
   sensiblen Fixture-Werte (Token, Hostnamen, IPs, Pfade, Tags, Alert-Texte);
   `caplog` bleibt in allen Fehlerpfaden inkl. Debug-Level token-frei.
9. **HACS-Struktur, README, hassfest/HACS-Action in CI** → verify: beide Actions
   grün; zusätzlich ein Upgrade-Test (Entry aus vorheriger Version lädt ohne
   Migrationsfehler).
10. **Live-Test in HA + Vulpo-Dashboard** → verify: alle erwarteten Devices
    sichtbar, Prozent-Sensoren erscheinen in Vulpo als Progress-Bar ohne
    Nachbearbeitung, Gesamtstatus schaltet beim Abschalten eines Testhosts
    innerhalb eines Intervalls um.

## 8. Offene Punkte

- **PBS**: Backup-Sensoren sind erst möglich, wenn der Backup-Host in Pulse als
  Proxmox-Backup-Server-Node eingebunden wird (aktuell nur Linux-Agent).
- **Proxmox**: `nodes: 0` — kein PVE-Node in Pulse konfiguriert. Die
  Node-Sensoren (`proxmox_cpu_usage` usw.) entstehen erst danach; der Code
  behandelt sie über denselben Host-Pfad wie die Agents.
- **Temperatur** nur, wenn `temperatureMonitoringEnabled` und ein Wert da ist.
- **host-b ist `degraded`** und es liegen aktive Alerts an (darunter `critical`,
  überwiegend ungesunde Docker-Container). Das ist ein redigierter Befund der
  Infrastruktur, kein Integrationsproblem — aber es heißt, dass
  `infrastructure_problem` sofort nach dem Einrichten `on` steht.
- **Alias-Heuristik** für ID-Wechsel ist unbestätigt: sie ließ sich mit einem
  einzelnen Snapshot nicht verifizieren.
