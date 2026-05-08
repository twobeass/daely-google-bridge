# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in diesem File festgehalten.

Format orientiert sich an [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
Versionsnummern folgen [Semantic Versioning 2.0](https://semver.org/spec/v2.0.0.html).

Image-Tag pro Release: `ghcr.io/twobeass/daely-google-bridge:vX.Y.Z` (zusätzlich
zu `:latest`). Wer pinnen will, kann auf einen konkreten Release-Tag fixieren.

## [Unreleased]

_(noch nichts.)_

## [1.0.2] - 2026-05-08

Patch — fixt Realtime-Filter (Server fired keine Notifications mit
leerem `calendars`-Feld) + macht Connection-Liveness im Log sichtbar.

### Behoben
- **Empty-`calendars`-Filter-Bug**: bei v1.0.0/v1.0.1 sendete der
  Bridge-Filter `calendars: []`. Empirisch (User-Test): selbst mit
  `subscribeUserCalendars=true` und `subscribeGroupCalendars=true` fired
  der Server **keine** ReceiveNotifications, wenn `calendars`-Liste leer
  ist. Bridge ruft jetzt vor dem SetFilter `get_calendars(group.id)` auf
  und passt die UUIDs der internen Calendars (`calendarType=0`) als
  `calendars`-Liste in den Filter ein. Externe Calendars (Google-/Apple-/
  MS-synced, `calendarType != 0`) werden nicht mit-subscribed — das ist
  konsistent mit der Mapper-Filter-Regel.

### Geändert
- **Liveness-Logging im Realtime-Client**:
  - Pings (alle 15s) loggen jetzt jeden 4. Ping als `realtime.ping`
    (≈ minütlich) — Operator sieht `docker compose logs` weiß-und-grün
    dass die Verbindung lebt.
  - SetFilter-Completion ist jetzt `info`-Level (war `debug`), damit der
    Erfolg des Subscribe sofort sichtbar ist.
  - Unbekannte Invocation-Targets loggen jetzt `info` mit Payload-Preview,
    damit Server-seitige Protokoll-Drift nicht silent passiert.

## [1.0.1] - 2026-05-08

Patch — fixt Delete-Propagation bei Realtime-Triggern.

### Behoben
- **Realtime-Trigger machte `incremental_sync` statt `full_sync`** —
  `incremental_sync` hat `detect_missing_as_deleted=False`, das heißt
  physische Deletes (Daely entfernt das Event ganz aus der API, statt es
  mit `deleted=true` zu flaggen) wurden silently übersprungen. Effekt
  beim User: Termin auf dem Tablet gelöscht → blieb in Google.
  Jetzt: Realtime-Trigger ruft `full_sync` mit Deletion-Detection.
  Polling-Loop (alle 15min) bleibt auf `incremental_sync` (cheap), weil
  ein nicht-realtime Daemon-Restart sowieso periodisch `full_sync` macht.
- 1 neuer Regression-Test der explizit prüft dass der Realtime-Trigger
  `full_sync_fn`, nicht `incremental_sync_fn` verwendet.

## [1.0.0] - 2026-05-08

**Stable release.** Die Bridge ist feature-complete für ihren primären
Use-Case (Daely-Events sichtbar in Google-Calendar-Widgets/Home-Assistant)
und hat seit Tagen produktiv ohne Probleme gelaufen. Zusätzlich liefert
1.0 das letzte große UX-Feature: Sub-Sekunden-Latenz statt 15-Minuten-
Polling über SignalR-Realtime.

### Hinzugefügt
- **§1.1 Realtime-Push (SignalR über SSE)** — opt-in via `realtime.enabled:
  true`. Bridge öffnet eine persistente SSE-Connection zu Daelys
  `/realtime`-Hub und triggert bei jedem Calendar-Push einen
  debounced incremental_sync. Effekt: Calendar-Änderungen propagieren
  in Sekunden statt 15 Minuten.
  - Reverse-Engineered SignalR JSON Hub Protocol (negotiate → handshake →
    SetFilter → ReceiveNotification-Stream)
  - Auto-reconnect mit exponential backoff (1s → 5min cap)
  - Token-Rotation mid-stream (über `acquireNewToken`-Pattern)
  - Liveness-Detection: kein Ping in 45s → Reconnect
  - Polling bleibt aktiv als Safety-Net — wenn Realtime-Connection
    wiederholt droppt, läuft der 15-min-Cycle weiter
  - 20 neue Offline-Tests gegen einen Fake-SSE-Stream
- `RealtimeClient`-Klasse in `realtime_client.py` (eigenständig nutzbar)
- `RealtimeFilter`/`RealtimeEvent`-Modelle mit `extra="ignore"` für
  zukünftige Server-Schema-Änderungen
- Config-Sektion `realtime:` mit Toggle + Debounce + per-Topic-Subscribes
- `findings/10_REALTIME_API.md` — vollständige Dokumentation des Daely-
  Realtime-Protokolls inkl. Live-validierter Frame-Formate

### Geändert
- Bridge-Daemon (`bridge run`) startet jetzt zwei parallel laufende
  Schleifen wenn beide enabled: Polling-Scheduler (15min default) und
  Realtime-Push-Listener. Ein realtime-getriggerter Sync läuft als
  scheduler-managed One-Shot-Job (idempotent mit `replace_existing=True`).

## [0.1.1] - 2026-05-08

Patch-Release. Härtet die SQLite-WAL-Sichtbarkeit zwischen dem Daemon-
Schreibprozess und parallelen Reader-Prozessen (z. B. `bridge doctor` via
`docker compose exec`).

### Behoben
- **WAL-Sichtbarkeits-Race**: nach jedem Sync-Cycle stellt `sync._finalize`
  jetzt einen expliziten `PRAGMA wal_checkpoint(PASSIVE)` aus, sobald die
  `sync_history`-Zeile geschrieben ist. Damit sehen frisch geöffnete
  Reader-Connections (zweiter Prozess, separate `sqlite3.connect`) die Zeile
  sofort, ohne auf SQLites Auto-Checkpoint zu warten.
- **`bridge doctor` macht eigenen Checkpoint**: ruft `Store.checkpoint()`
  direkt nach Init auf, damit der Read alles inkl. ungefachecktes WAL-
  Material sieht — Belt-and-Suspenders zur ersten Maßnahme.
- **`bridge doctor` zeigt nicht mehr `[WARN]` direkt nach Container-Start**:
  ein leeres `sync_history` ist ein normaler Post-Restart-Zustand, kein
  Fehler. Der Output ist jetzt `[OK] last sync: pending — first cycle
  hasn't completed yet`.

### Hinzugefügt
- `Store.checkpoint(mode="PASSIVE"|"FULL"|"RESTART"|"TRUNCATE")` als
  öffentliche API. Returns SQLites `(busy, log_frames, checkpointed)`-Tripel
  oder `None` bei Non-WAL-DBs.

## [0.1.0] - 2026-05-08

Erstes getaggtes Release. Konsolidiert alles vom RE-Abschluss über die
3e/3f-Mapping-Erweiterungen bis zu Operations-Tools (Migrations, Retry-Loop,
Sync-History, CLI-Commands, Health-Endpoint, CI).

### Sync-Kern

- Einseitiger Sync **Daely → Google Calendar** mit Polling (default alle
  15 min, `lookback_days=30`, `lookahead_days=365`).
- ROPC-Auth gegen Daely-Keycloak (`sso.daely-connect.com`, Realm `daely`,
  Client `mobile-app`). Refresh-Token-Rotation transparent.
- Google-OAuth via InstalledAppFlow, `redirect_uri http://localhost:<port>`,
  konfigurierbarer `oauth_local_port`.
- Bootstrap-CLI: Daely-Login → Google-OAuth → automatisches Anlegen pro Profil
  einer Google-Sub-Calendar → Persistenz der Mapping-Tabelle.
- Sync-Engine:
  - Polling-Loop (apscheduler).
  - `full_sync` mit Deletion-Detection per Snapshot-Diff.
  - `incremental_sync` (kürzeres Window, nur `deleted=true`-Events).
  - Per-Event-Error-Isolation: ein Failure stoppt den Cycle nicht.

### Mapper

- `daely_event_to_google()` – pure function, alle 17 Daely-Event-Felder
  auf Google-Body abgebildet.
- Recurring: master-only Strategie (`deduplicate_recurring()`), RRULE wird
  durchgereicht, Google expandiert.
- Filter: Events aus externen Daely-Kalendern (`calendarType != 0`)
  werden übersprungen, um Sync-Loops mit Daelys eigener Google-Integration
  zu vermeiden.
- **Profil-Footer** (Phase 3e): `👥 Beteiligt: …` an die Description, Namen
  case-insensitive sortiert.
- **Profil-Color-Mapping** (Phase 3f): Daely-`colorCode` → eine der 11
  Google-`colorId`s per nearest-RGB-Match. `profile_overrides` in der
  Config zum Pinnen einzelner Profile.
- **Multi-Participant-Title-Prefix** (Phase 3f): bei ≥2 Beteiligten
  farbige Punkt-Emojis vor dem Titel, Reihenfolge identisch zum Footer.
- `customColorCode` und Daely-only-Felder (`recurringId`, `profileId`, …)
  werden in `extendedProperties.private` gespiegelt für Diagnose.

### Persistenz & Migrationen

- **SQLite-Store** (`bridge.db`), idempotente UPSERTs.
- **Schema-Migration-Framework**: forward-only Migration-Liste in `store.py`,
  beim `Store()`-Init automatisch angewendet. Pre-framework-DBs werden via
  `event_mapping`-Tabellen-Detection als v1 erkannt, ohne dass die Initial-
  Migration neu läuft. Vor jedem echten Upgrade Best-Effort-Backup unter
  `bridge.db.bak.v<N>-<timestamp>`.
- **Schema v1**: `event_mapping`, `sync_state`, `tokens`.
- **Schema v2**: drei neue Spalten auf `event_mapping`
  (`retry_after`, `retry_count`, `last_error`) + neue Tabelle `sync_history`
  als Audit-Log abgeschlossener Sync-Cycles.
- `Store.schema_version`, `Store.migrated_from_version`, `Store.last_backup_path`
  Properties zur Diagnose.

### Reliability

- **Retry-Loop** für `failed=true`-Mappings: nach einem Patch-/Delete-Fehler
  wird der nächste Versuch exponentiell zurückgehalten (Default: 60s
  verdoppelnd, gedeckelt auf 1h). In der Cooldown-Zeit wird das Event
  übersprungen statt erneut gegen Google zu hämmern. Erfolgreiche Re-Patches
  setzen den Retry-Zustand automatisch zurück. Skip-Counter im `SyncReport`:
  `skipped_retry_cooldown`.
- **Sync-History-Persistenz**: jeder Sync-Cycle (auch aborted-at-top runs)
  schreibt eine Zeile in die `sync_history`-Tabelle. Auto-Pruning hält die
  letzten 500 Einträge.

### CLI

- `bridge bootstrap` — interaktives Setup.
- `bridge run` / `--once` — Daemon mit initialem Full-Sync, dann periodisches
  `incremental_sync` über apscheduler.
- `bridge status` — Quick-Look auf Tokens, Mappings, letzten Sync.
- **`bridge doctor`** — Health-Diagnose mit `[OK]`/`[WARN]`/`[FAIL]`-Markern:
  Config, DB-Schema, Tokens, Mappings, Sync-Alter, Error-Trend, Profile-
  Mapping-Konsistenz. Exit-Code 0/1/2 für Cron-Integration.
- **`bridge doctor --live`** — zusätzlich Daely-Refresh + Google-`list_calendars`-
  Ping (braucht Netzwerk).
- **`bridge resync [--calendar <id>] [--dry-run]`** — setzt
  `last_seen_updated=NULL` auf den passenden Mapping-Rows, damit der nächste
  Sync sie mit aktueller Mapper-Logik (z. B. neuen Farben) re-patcht.
- **`bridge re-color [--dry-run]`** — Convenience-Alias für `bridge resync`
  über alle Calendars.

### Health-Endpoint (opt-in)

- Tiny stdlib-`http.server` als Daemon-Thread parallel zum Sync-Loop.
- `GET /healthz` — 200 wenn letzter Sync innerhalb von `poll_interval × 2 + 60s`,
  sonst 503.
- `GET /readyz` — 200 wenn beide Refresh-Tokens (Daely + Google) im Store.
- `GET /status` — JSON mit Schema-Version, Mapping-Counts, letzten 10
  History-Einträgen.
- Bind default `127.0.0.1:8090`, in Config konfigurierbar (`health_server`-
  Sektion). Default off, opt-in via `enabled: true`.

### Operations

- Multi-Arch-Docker-Image (amd64+arm64) via GHA, published auf `ghcr.io/twobeass/daely-google-bridge`.
- **`tests.yml`** GitHub-Action: `pytest` auf jedem Push und PR gegen `main`.
- **`release.yml`** GitHub-Action: bei Push eines `v*`-Tags automatisches
  GitHub-Release; Body wird aus dem passenden CHANGELOG-Abschnitt extrahiert.
- 260 Offline-Tests (`respx` + `unittest.mock`), keine Live-Calls in CI.

### Findings & Doku

- Vollständige sanitisierte API-Dokumentation in `findings/00–08`.
- `findings/09_FUTURE_ROADMAP.md` als Erweiterungs-Katalog mit
  Wert-/Komplexitäts-/Risiko-Bewertung pro Idee.
- README mit Bedienungs-/Command-Übersicht, Update-Anleitung,
  Health-Endpoint-Setup.

### Out-of-Scope

- **Reverse-Sync (Google → Daely)** — bewusst nicht implementiert; Daelys
  eigene Google-/CalDAV-Integration deckt das ab.
- **Photo-Upload** — ursprüngliches Mission-Ziel, zurückgestellt; Widget-
  Use-Case (das eigentliche Pain-Point) ist gelöst.

[Unreleased]: https://github.com/twobeass/daely-google-bridge/compare/v1.0.2...HEAD
[1.0.2]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.0.2
[1.0.1]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.0.1
[1.0.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v1.0.0
[0.1.1]: https://github.com/twobeass/daely-google-bridge/releases/tag/v0.1.1
[0.1.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v0.1.0
