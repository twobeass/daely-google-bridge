# 06 – Bridge-Architektur (Daely → Google Calendar, Read-Only)

## TL;DR
Single-Direction-Sync **Daely → Google Calendar**. Pro Daely-Profil ein eigener Google-Subkalender. Polling-basiert (kein Webhook-Receiver), inkrementell via `SyncTokenPair`. SQLite hält das Mapping `daely_event_id → google_event_id`. **Keine Rückrichtung, keine Konflikt-Auflösung** – wenn der User in Google ein Event löscht, re-creates die Bridge es beim nächsten Poll. Rückrichtung Google → Daely läuft über Daelys eingebauten URL-Calendar-/External-Account-Mechanismus, nicht über die Bridge.

## Komponenten-Diagramm

```
              ┌─────────────────────┐
              │  Daely Backend      │
              │  daely-connect.com  │
              └──────────┬──────────┘
                         │ HTTPS (Bearer JWT, dio-Pattern)
                         │ /api/groups/<gid>/calendars/check-update
                         │ /api/groups/<gid>/calendars/with-events
                         ▼
       ┌────────────────────────────────────┐
       │       daely-google-bridge          │
       │                                    │
       │   ┌──────────────────────────┐     │
       │   │   sync.py (main loop)    │     │
       │   │   - APScheduler/while    │     │
       │   │   - exp. Backoff         │     │
       │   └────┬──────────┬──────────┘     │
       │        │          │                │
       │        ▼          ▼                │
       │  ┌──────────┐  ┌──────────┐        │
       │  │ daely_   │  │ google_  │        │
       │  │ client   │  │ client   │        │
       │  │ .py      │  │ .py      │        │
       │  │          │  │          │        │
       │  │ - ROPC   │  │ - OAuth2 │        │
       │  │ - SyncT  │  │ - Events │        │
       │  │ - Reads  │  │ - Writes │        │
       │  └────┬─────┘  └─────┬────┘        │
       │       │              │             │
       │       └──────┬───────┘             │
       │              ▼                     │
       │      ┌───────────────┐             │
       │      │  mapper.py    │             │
       │      │  (pure func.) │             │
       │      └───────┬───────┘             │
       │              ▼                     │
       │      ┌───────────────┐             │
       │      │   store.py    │             │
       │      │   SQLite      │             │
       │      │  (id mapping, │             │
       │      │   sync state) │             │
       │      └───────────────┘             │
       │                                    │
       └────────────────────────────────────┘
                         │ OAuth2 (offline access)
                         │ google-api-python-client
                         │ events.insert / .patch / .delete
                         ▼
              ┌─────────────────────┐
              │ Google Calendar v3  │
              │ www.googleapis.com  │
              └─────────────────────┘
```

## Sync-Strategie

### Phase 1 – Initial Full-Sync (One-Time pro Calendar)

```
für jeden Daely-Calendar (aus Bridge-Config / via /api/groups/<gid>/calendars):
    GET /api/groups/<gid>/calendars/with-events?syncToken=null
        → CalendarWithEvents { events: [...], syncTokens: SyncTokenPair }
    für jedes Event in events:
        google_event = mapper.daely_to_google(event, profile_calendar_mapping)
        google_id = google_client.events_insert(target_calendar, google_event)
        store.put(daely_id=event.id, google_id=google_id, calendar_id=event.calendarId)
    store.set_sync_token(calendar_id, syncTokens.internal, syncTokens.external)
```

### Phase 2 – Incremental Sync (jedes Polling-Intervall)

```
für jeden Daely-Calendar:
    syncToken = store.get_sync_token(calendar_id)   # internal
    GET /api/groups/<gid>/calendars/check-update?internal=...&external=...
        → CheckCalendarsUpdateResponse { requiresUpdate, updateCheckIntervalMinutes, errors }
    if not requiresUpdate:
        # nichts zu tun. Polling-Intervall NÄCHSTES MAL respektieren
        store.set_recommended_interval(updateCheckIntervalMinutes)
        continue
    
    GET /api/groups/<gid>/calendars/with-events?syncToken=internal
        → diff oder Vollantwort (je nach Server-Verhalten)
    
    für jedes Event:
        if event.deleted:
            google_id = store.get(event.id)
            if google_id: google_client.events_delete(google_id)
            store.remove(event.id)
        elif store.has(event.id):
            google_event = mapper.daely_to_google(event, ...)
            google_id = store.get(event.id)
            google_client.events_patch(google_id, google_event)
        else:
            # neu: insert wie in Phase 1
            ...
    store.set_sync_token(calendar_id, response.syncTokens.internal, ...)
```

### Recurring-Event-Sonderfall

Series-Master haben `recurringId == null` (sind selbst der Master). Series-Instances haben `recurringId == <masterId>`.

**Kritische Regel im Mapper**: Bridge muss bei Initial-Sync ALLE Series-Master ZUERST schreiben, dann erst Instances. Sonst kennt die Bridge die Mapping-ID des Masters nicht, wenn sie eine Instance schreibt. Realisierbar via 2-Pass-Sortierung:

```python
events_sorted = sorted(events, key=lambda e: e.recurringId is not None)
# zuerst alle mit recurringId is None, dann alle mit recurringId
```

### Was passiert, wenn der User in Google etwas löscht/ändert

- **Löschen in Google**: Beim nächsten Bridge-Poll erkennt der Bridge dies erst beim nächsten Schreib-Versuch. `events_patch` auf eine gelöschte Google-ID liefert 404 → Bridge tut: `events_insert` neu, **store updaten** mit neuer google_id. Effekt: Re-Creation. **Feature, kein Bug**.
- **Editieren in Google**: Wird beim nächsten Bridge-Push überschrieben. **Feature, kein Bug** – Bridge ist Single-Source-of-Truth.
- **Komplett neuer Event in Google**: Bleibt erhalten und wird nie von der Bridge angefasst (Bridge nutzt nur die Events aus dem Mapping-Store).

## Konflikt-Auflösung
**Entfällt**, weil read-only.

Daely ist die einzige Schreib-Quelle. Bridge ist deterministisch idempotent: für jedes Daely-Event gibt's genau einen Google-Event, identifizierbar via `daely_id` ↔ `google_id` im SQLite-Store.

## Failure-Modes

| Failure | Detection | Reaction |
|---|---|---|
| Daely AT abgelaufen | 401 von daely-connect.com | Refresh-Token-Flow gegen Keycloak (sso.daely-connect.com), retry. Wenn refresh selbst fehlschlägt: log+exit, User muss sich neu einloggen |
| Daely RT invalid | `invalid_grant` Response | Bridge stoppt, fordert User-Eingriff (interactive Re-Login) |
| Daely Backend down | 5xx oder Connection-Error | Exp. Backoff (1s, 2s, 4s, 8s, max 5min), letzter SyncToken bleibt, **kein Datenverlust** |
| Google Quota überschritten | 403 mit `quotaExceeded` | Exp. Backoff bis nächster Sliding-Window-Reset |
| Google Token expired | 401 | google-auth lib refresht automatisch via refresh_token |
| Google Calendar gelöscht | 404 auf events_insert | Log warning, evtl. neuen Sub-Calendar via bootstrap-Flow anbieten |
| SQLite-File korrumpiert | DB-Open-Error | Backup einlesen falls vorhanden, sonst kompletter Re-Sync (Phase 1) – danach gleicher Zustand |
| Mapper-Fehler bei einem Event | Exception während daely_to_google | Event skippen, in DB als `failed=true` markieren, weiter mit nächstem Event. Daily-Job versucht failed-Events erneut |

## Rate-Limiting

- Bridge respektiert `updateCheckIntervalMinutes` aus `CheckCalendarsUpdateResponse` als Minimum-Polling-Intervall.
- Default-Initial: 5 min, vom Server möglicherweise höher gesetzt.
- Pro Calendar separat: keine global-zentrale Polling-Synchronisierung nötig, weil pro Calendar getrennte Sync-Tokens.

## Tech-Stack

| Layer | Komponente | Begründung |
|---|---|---|
| Sprache | Python 3.12 | Stable, dataclasses + `match`, async support |
| HTTP-Client | `httpx` | Sync- und Async-API, HTTP/2-fähig, gute Timeouts/Retry-Hooks. Default für Daely-Calls und (alternativ) für Google |
| Google-API | `google-auth` + `google-api-python-client` (primär), Fallback `httpx` direkt | google-api-python-client ist offiziell, gut dokumentiert. Für minimale Image-Size Alternative: Direkt-httpx gegen `https://www.googleapis.com/calendar/v3/...` (lohnt nur wenn man auf 100 KB Container-Größe optimieren will) |
| OAuth Google | `google-auth-oauthlib` | für initiale Authorization-Flow, persistiert refresh_token in SQLite |
| Persistenz | `sqlite3` (stdlib) | 2 Tabellen, kein ORM nötig |
| Validation | `pydantic` v2 | Daely-Response-Validierung (16 Felder × 2 Sub-Models = überschaubar). Hilft beim Erkennen, wenn Daely ein neues Feld einführt |
| Scheduling | **`apscheduler`** primär, alternativ **systemd-Timer** auf VPS | apscheduler einfacher in Container/lokal; systemd-Timer wenn VPS bereits systemd-orchestriert ist |
| Logging | `structlog` mit JSON-Output | strukturierte Logs für später ELK/Loki-Auswertung; lokal trotzdem human-readable durch Renderer |
| Config | YAML + `pyyaml` | Profile-Mapping ist 1× definiert, statisch, kein .env-Fetisch nötig |
| Tests | `pytest` + `respx` (httpx-Mock) | Mapper isoliert testen, Daely-Responses gemockt |
| Linting | `ruff` | schnell, kombiniert flake8+isort+black-Komponenten |

### Bootstrap-Skript-Auftrag

`bridge bootstrap` macht folgendes:

1. ROPC-Login bei Keycloak (Email/Passwort einmalig vom User), persistiert refresh_token in SQLite.
2. Google-OAuth2-Flow: öffnet Browser oder zeigt URL+Code an (`google-auth-oauthlib.flow.InstalledAppFlow.run_local_server` oder `.run_console`), persistiert google-refresh_token.
3. Liest Daely-Familie + Profile (`GET /api/groups`, dann pro Group `GET /api/groups/<gid>/calendars`).
4. Pro gefundenes **Profil** (nicht pro Calendar!): Erstellt einen Google-Sub-Calendar via `calendarList.insert` mit Name `Daely - <Profilname>`, persistiert das Mapping in SQLite + schreibt `config.example.yaml` mit Vorschlag.
5. User editiert `config.yaml` (z. B. Profile umbenennen, Calendar-IDs anpassen), startet dann `bridge run`.

## Repo-Layout

```
daely-google-bridge/
├── README.md                        # Setup, ENV-Variablen, Beispiel-Run
├── pyproject.toml                   # Deps: httpx, pydantic, google-auth*, apscheduler, structlog, pyyaml
├── config.example.yaml              # Template
├── .gitignore                       # *.db, secrets/, __pycache__
│
├── src/daely_google_bridge/
│   ├── __init__.py
│   ├── __main__.py                  # CLI: argparse → bootstrap | run | status | resync
│   │
│   ├── daely_client.py              # ROPC, refresh, Calendar-Reads, SyncToken-Param-Bau
│   ├── google_client.py             # Google-OAuth-Setup, events_insert/_patch/_delete, calendarList_insert
│   │
│   ├── mapper.py                    # daely_to_google(event, profile_to_calendar) -> google_event_dict
│   │                                # Pure function, keine I/O. Testbar.
│   │
│   ├── sync.py                      # Hauptloop: Phase-1-Initial, Phase-2-Incremental
│   │                                # Importiert daely_client + google_client + mapper + store
│   │
│   ├── store.py                     # SQLite-Wrapper. Tabellen:
│   │                                #   event_mapping(daely_id PK, google_id, calendar_id, last_synced_at, failed)
│   │                                #   sync_state(calendar_id PK, internal_token, external_token, recommended_interval_min)
│   │                                #   tokens(provider PK, refresh_token, access_token, expires_at)
│   │
│   ├── models.py                    # pydantic-Modelle (CalendarEvent, StartEnd, CheckCalendarsUpdateResponse, etc.)
│   │                                # 1:1 zu Daely-Wire-Format (tolerant gegen unbekannte Felder via model_config)
│   │
│   ├── config.py                    # YAML-Loader, Validation
│   │
│   └── logging.py                   # structlog-Setup
│
├── docker/
│   ├── Dockerfile                   # Multi-Stage, slim Python 3.12
│   └── docker-compose.yml           # bridge + (optional) volume für SQLite
│
├── systemd/                         # Optional: Unit + Timer
│   ├── daely-google-bridge.service
│   └── daely-google-bridge.timer
│
└── tests/
    ├── conftest.py                  # Fixtures: gemockte Daely-Responses
    ├── test_mapper.py               # Hauptarbeit: alle 16 Event-Felder × Recurrence × All-Day × Reminders
    ├── test_store.py                # SQLite-Roundtrip
    ├── test_daely_client.py         # mit respx (httpx mock)
    └── test_sync.py                 # Mock-Daely + Mock-Google, end-to-end-Flow
```

### `config.example.yaml`

```yaml
daely:
  email: ${DAELY_EMAIL}                # via env, ggf. mit dotenv
  # password is NEVER in config; only used during bootstrap; refresh_token persists in store

google:
  oauth_client_secrets_file: ./secrets/google_oauth_client.json
  scopes:
    - https://www.googleapis.com/auth/calendar

# Profil → Google-Sub-Calendar-Mapping
# Bridge bootstrap erstellt diese Sub-Calendars, schreibt das Mapping hier rein
profile_calendar_mapping:
  daely_profile_id_001: google_calendar_id_aaa@group.calendar.google.com   # "Mama"
  daely_profile_id_002: google_calendar_id_bbb@group.calendar.google.com   # "Papa"
  daely_profile_id_003: google_calendar_id_ccc@group.calendar.google.com   # "Kind"

sync:
  poll_interval_minutes: 15              # Default; Server-Hint via updateCheckIntervalMinutes überschreibt nach unten
  initial_sync_only: false               # für Tests: nur Phase 1 laufen, dann exit

logging:
  level: INFO
  format: json                           # alternativ "text" für lokal
  file: ./logs/bridge.log
```

### CLI-Befehle

```
bridge bootstrap         # interaktiv: Daely-Login, Google-OAuth, Sub-Calendars anlegen
bridge run               # Daemon-Modus, läuft im Vordergrund (Container) oder bg (systemd)
bridge run --once        # 1× sync und exit (für Cron)
bridge status            # Letzter Sync, # Events synced, # Failures, nächste Polling-Zeit
bridge resync <cal_id>   # Force full re-sync für einen Calendar (bei Schema-Änderungen)
```

## Datei für Datei – Rationale

| Datei | Was | Warum getrennt |
|---|---|---|
| `daely_client.py` | Auth + Reads (KEINE Writes, da read-only Bridge) | Ein Client = ein Service. Wenn Daely-API-Änderungen kommen, ist genau hier zu patchen |
| `google_client.py` | OAuth + Writes (KEINE Reads außer für `calendarList`) | Symmetrie zum daely_client. Wenn Google-API-Änderungen kommen, hier patchen |
| `mapper.py` | Pure transformation | Testbarkeit. Keine I/O, kein Logger, kein State. `daely_event → google_event_dict`. Macht 80 % der Logik aus, ist also der Hot-Spot für Tests |
| `sync.py` | Orchestrierung der zwei Clients + Mapper + Store | Hier sitzt die Polling-Schleife, Backoff-Logik, Recurrence-Master-First-Sortierung. Pure Composition |
| `store.py` | SQLite-Wrapper | Async-frei (sqlite3 ist sync). Wenn man später auf PostgreSQL wechselt, ist nur dieser File zu ersetzen |
| `models.py` | pydantic für Daely | Validation am Eingangs-Boundary. Tolerant gegen unbekannte Felder (`model_config = ConfigDict(extra='ignore')`) – sonst bricht jeder Daely-API-Patch die Bridge |

## Initiale Implementations-Reihenfolge (Empfehlung)

1. **Pure: `mapper.py` + `models.py` + `tests/test_mapper.py`** – kann komplett offline mit Sample-JSON entwickelt werden, ohne Daely- oder Google-Konto. Sample-JSONs aus dem ersten Live-Read (manuell) als Fixtures.
2. **`store.py` + `tests/test_store.py`** – ebenfalls offline.
3. **`daely_client.py`** + 1 echter ROPC-Login (=erster autorisierter Live-Call gegen Daely).
4. **`google_client.py`** + Google-OAuth-Flow.
5. **`sync.py`** Initial-Phase nur für 1 Test-Calendar (10 Events) erstmals, dann ausweiten.
6. **CLI + Config + Bootstrap-Skript**.
7. **Docker/systemd-Deployment**.

## Sicherheits-Aspekte

- **Refresh-Tokens persistiert in SQLite** – Datei mit `chmod 600`, im Repo `.gitignore`. Auf VPS in `/var/lib/daely-google-bridge/` mit owner-only.
- **OAuth-Client-Secrets-File** für Google – nur lokal, nicht ins Image kompilieren.
- **Daely-Passwort** wird NIE persistiert. Nur 1× im Bootstrap zur RT-Erzeugung benutzt.
- **Logging** darf keine Tokens loggen. structlog-Filter setzt `*token*`-Felder auf `<REDACTED>`.

## Was diese Architektur NICHT kann (Mission-Status absichtlich)

- ❌ Google-Events nach Daely zurückschreiben (das löst der User über Daelys eingebauten URL-Calendar-Feature)
- ❌ Konflikte zwischen Daely und Google auflösen (entfällt durch Single-Source-of-Truth Daely)
- ❌ Photo-Upload-Limit umgehen (separate Mission, siehe 03_PHOTO_LIMIT.md)
- ❌ Andere Calendar-Provider als Google (Apple/MS – Erweiterung trivial via neuer `apple_client.py`/`ms_client.py`, aber nicht Teil der initialen Implementierung)
- ❌ Echtzeit-Push (Daely scheint per `realtime`-Service auch SSE zu haben, aber für eine Bridge ist Polling robuster und einfacher)

## Confidence
**high** für: Architektur-Form, Komponenten-Trennung, Tech-Stack-Eignung, Daely-Read-Endpoints (sind in 01_ENDPOINTS.md verifiziert), Mapping-Strategie via Sub-Calendars.

**medium** für: Genaue Form der `check-update`-Response-Felder bei Diff-vs-Full (Verhalten beim ersten Aufruf mit altem SyncToken). Konkretes Format einzelner Daely-Felder (siehe 05_EVENT_MODEL.md).

**Empfohlener nächster Schritt**: `mapper.py` + `tests/test_mapper.py` mit gemockten Daely-Sample-JSONs (aus 1× echtem GET) implementieren. Das ist 100 % Live-Call-frei nach dem einen Initial-Read.
