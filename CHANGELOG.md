# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in diesem File festgehalten.

Format orientiert sich an [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/).
Versionsnummern folgen [Semantic Versioning 2.0](https://semver.org/spec/v2.0.0.html).

Image-Tag pro Release: `ghcr.io/twobeass/daely-google-bridge:vX.Y.Z` (zusätzlich
zu `:latest`). Wer pinnen will, kann auf einen konkreten Release-Tag fixieren.

## [Unreleased]

_(noch nichts.)_

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

[Unreleased]: https://github.com/twobeass/daely-google-bridge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/twobeass/daely-google-bridge/releases/tag/v0.1.0
