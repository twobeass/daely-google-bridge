# 04 – Calendar-Sync-Architektur

## TL;DR
Drei distinkte Calendar-Quellen: (1) **interne Daely-Kalender** mit App-eigenen Events (`/api/groups/<gid>/calendars` + `/events`), (2) **OAuth-verknüpfte externe Provider** (Google/Microsoft/Apple via `/api/external-accounts`), (3) **URL-basierte CalDAV/ICS-Kalender** (`/api/url-calendars`). Der inkrementelle Sync nutzt `SyncTokenPair` (internal+external Token). **Für die Mission ist (3) der einfachste Workaround-Pfad**: URL-Calendars haben einen `writable: bool`-Feld, weisen also auf vollwertigen Two-Way-Sync hin – und die Anzahl der URL-Kalender pro User scheint nicht offensichtlich limitiert.

## Beweise

### Drei Calendar-Sourcen

| Source | Endpoint | Modell | Auth | Hauptzweck |
|---|---|---|---|---|
| Internal | `/api/groups/<gid>/calendars` | `Calendar` mit `external: false` | Bearer | Daely-eigene Kalender, in App editiert |
| External Account | `/api/external-accounts` | `ExternalAccount` + `Calendar` mit `external: true` | Bearer (App) + OAuth (extern) | Google/Microsoft/Apple-Calendar-Sync |
| URL | `/api/url-calendars` | `UrlCalendar` | Bearer | CalDAV/ICS-URL eines beliebigen Servers |

### `Calendar`-Modell (intern + extern)
**Datei**: `findings/blutter_out/asm/common/models/calendar/calendar.dart`

Felder (aus toString-Strings):
- `id`, `title`, `description`, `colorCode`, `customColorCode`
- `startDate`, `endDate`, `timeZone`
- `external: bool`, `externalId`, `externalSyncToken`, `internalSyncToken`
- `presentationType`, `calendarType`, `shareType`
- `ownerId`, `profileId`, `isClassSchedule`
- `events: List<CalendarEvent>?` (optional, abhängig vom Endpoint)
- `eventsIncluded: bool`, `hasError: bool`
- `url` (für URL-Calendars)

### `UrlCalendar`-Modell
**Datei**: `findings/blutter_out/asm/familyplannerapp/screens/settings/models/url_calendar/url_calendar.dart`

Felder:
- `internalId` (Daely-eigene ID)
- `url` (CalDAV-/ICS-URL)
- `title`
- `timezone`
- `calendarType`
- `writable: bool` ⚠️ **Two-Way-Sync-Indikator**
- `groupId` (welcher Familie zugeordnet)
- `sharedWith: List<SharedWithEntry>` mit Entries `{groupId, shareType}`

Das `writable`-Feld ist Schlüssel: Falls `true`, schreibt Daely auch zurück in den CalDAV-Server. Daraus folgt:

**Workaround-Idee (für die Mission)**: Eigenen CalDAV-Bridge-Server hosten (z. B. Radicale auf einem eigenen Host), URL der Bridge in Daely registrieren. Damit hat man unbegrenzt viele Quellen, weil man hinter der einen URL beliebig viele Kalender konsolidieren kann.

### `SyncTokenPair`-Modell
**Datei**: `findings/blutter_out/asm/common/models/calendar/sync_token_pair.dart`

```
SyncTokenPair(internal: …, external: …)
```

Zwei separate Sync-Tokens: einer für Daely-interne Änderungen (z. B. ein anderer Familien-Member hat lokal was geändert), einer für die externe Quelle (Google/MS/CalDAV-Server). Standard-Pattern für Differential-Sync.

### `UpdateCalendarEventType`-Enum
**Datei**: `findings/blutter_out/asm/common/models/calendar/update_calendar_event_type.dart`

Enum-Strings (aus toString): `"UpdateCalendarEventType."`. Ohne tieferes Disassembly sind die einzelnen Werte nicht direkt sichtbar, aber typische Werte für Recurrence-Edits sind: `single`, `thisAndFollowing`, `all` – das deutet, dass die App auch Serientermine korrekt patchen kann.

### `ShareType`-Enum
**Datei**: `findings/blutter_out/asm/common/models/calendar/share_type/share_type.dart`

Strings: `"ShareType."` – wieder Enum-Marker. Vermutete Werte (basierend auf typischer Calendar-Sharing-Logik + den `sharedWith`-Strukturen): `none`, `read`, `readWrite`, `owner` o. ä.

### `ExternalAccount`-Modell
**Datei**: `findings/blutter_out/asm/familyplannerapp/screens/settings/models/external_calendar/external_account.dart`

Felder:
- `id`, `userId`
- `accountId`, `accountName`, `accountType` (`AccountType.google` / `.microsoft` / `.apple`)
- `hasError`

### Calendar-Service-Operationen
**Datei**: `findings/blutter_out/asm/common/service/calendar/calendar_rest_service.dart`

Pfad-Bestandteile (mit `/api/groups/<gid>` als Prefix):
| Pfad | Vermutete Methode | Zweck |
|---|---|---|
| `/calendars` | GET / POST | Kalender-Liste / -Anlage |
| `/calendars/<id>` | GET / PUT / DELETE | Einzelkalender |
| `/calendars/check-update?...` | GET (mit Sync-Token in Query) | Inkrementeller Sync-Check |
| `/calendars/with-events?...` | GET (mit Datums-Bereich) | Kalender + Events bündig |
| `/calendars/<id>/share` | POST | Kalender freigeben |
| `/calendars/<id>/events` | GET / POST | Event-Liste / -Anlage |
| `/calendars/<id>/events/<eventId>` | GET / PUT / DELETE | Einzel-Event |

Die genauen Methoden ergeben sich aus den `DioMixin::get/post/put/delete`-Aufrufen, im Detail nicht extrahiert (1-2h Mehrarbeit). Für die Mission reicht die Strukturkenntnis.

### External-Calendar-Service-Operationen
**Datei**: `findings/blutter_out/asm/familyplannerapp/screens/settings/service/external_calendar/external_calendar_rest_service.dart`

Methoden (im Disassembly als top-level method-marker erkennbar):
- `getUrlCalendars()` → GET `/api/url-calendars`
- `addUrlCalendar()` → POST `/api/url-calendars` mit Body `{url, title, timezone, calendarType, writable, groupId, sharedWith}`
- `deleteUrlCalendar()` → DELETE `/api/url-calendars/<id>`
- weitere Methoden für `connect`/`disconnect`/`add-calendar-to-application`/`remove-calendar-from-application` (External-Account-Toggles)

### One-Way / Two-Way-Sync-Indikatoren
- Asset-Files `assets/icons/one-way-sync.svg` und `assets/icons/two-way-sync.svg` (im `flutter_assets`-Bundle).
- `writable: bool`-Feld auf `UrlCalendar` differenziert die beiden Modi.
- Bei verbundenen Google-/MS-Accounts ist das Token zur Two-Way-Synchronisation in `external_account` hinterlegt (Scopes enthalten `Calendars.ReadWrite`).

## Interpretation

### Mission-Frage: „Limit Kalender-Sync"
Die ursprüngliche Mission-Beschreibung erwähnt „Kalender-Sync-Limits". Aus dem App-Code wird **kein hardcoded Limit für Anzahl Kalender** sichtbar:
- `getUrlCalendars()` liefert eine `List<UrlCalendar>` ohne Pagination → impliziert keine n-Begrenzung im Modell.
- Kein `MAX_CALENDAR_COUNT` o. ä. in den Strings.
- Die Beschränkung „nur 1 externer Account pro Provider" (laut Hersteller-Marketing) könnte server-seitig stehen oder in der UI: Beim Connect-Flow gibt's evtl. einen Pre-Check, der den vorhandenen Account des gleichen Provider-Typs entfernen will.

### Workaround-Paths (unklar, ob Limit überhaupt existiert)

**Pfad A – mehrere URL-Kalender**:
Wenn das Hersteller-Limit `1 Google-Kalender pro Tablet` lautet, aber URL-Kalender frei mehrfach addierbar sind, dann:
1. Eigenen CalDAV-Server hosten (Radicale, Baikal, NextCloud)
2. Beliebige Google-/MS-/Apple-Kalender per OAuth in CalDAV einlesen (existierende Tools)
3. Mehrere CalDAV-URLs in Daely registrieren

**Pfad B – mehrere Familien-Groups**:
Falls auch URL-Kalender pro Group limitiert sind, aber Groups frei kreierbar: mehrere Groups anlegen, in jedem nur 1 Kalender, Tablet alle Groups beitreten lassen. Nicht überprüft, ob das Tablet alle Groups gleichzeitig anzeigt.

**Pfad C – Server akzeptiert beliebig viele**:
Statisch nichts ersichtlich, was eine Beschränkung erzwingt. **Möglich, dass das „Limit" der Marketing-Aussage nur ein UI-Default ist**, der in der App das „Verbinden"-Button-Verhalten steuert, aber nicht die API.

### Inkrementeller Sync ist sauber implementiert
`SyncTokenPair` deutet auf einen ausgereiften Server – wir können das im Python-Client einfach übernehmen: Token aus Response merken, beim nächsten `check-update` zurückschicken, nur Diff bekommen.

### Two-Way-Sync mit eigenem CalDAV-Server
`UrlCalendar.writable=true` bedeutet, Daely macht PUT/POST/DELETE auf dem URL-Server für Events, die im Tablet erstellt wurden. Das eröffnet einen sehr eleganten Workflow:
- Eigener CalDAV-Server als single source of truth
- Daely + alle anderen Apps (Apple, Google, Outlook) synchronisieren beide Richtungen damit
- Foto-Limit + Calendar-Limit umgangen, weil der CalDAV-Server alle Daten kapselt

## Confidence
**high**: Endpoint-Struktur, Modell-Felder, drei Calendar-Source-Typen, `writable`-Feld, `SyncTokenPair`-Mechanik.

**medium**: HTTP-Methoden pro Endpoint (sample-weise verifiziert, nicht vollständig).

**low**: Tatsächliche Server-Limits für URL-Calendar-Anzahl (rein aus Code nicht ableitbar – Marketing-Aussage hat ggf. UI-Wurzel). One-Way- vs. Two-Way-Branch-Logik in der Sync-Routine (Asset-Icons existieren, aber Branch-Code im Disassembly nicht im Detail nachvollzogen).

## Offene Punkte / Phase-3-Tests

1. **Lese-Test**: `GET /api/url-calendars` → wie viele Kalender hat der Test-Account aktuell?
2. **POST-Test**: Einen Test-CalDAV-URL hinzufügen (z. B. öffentliche Feed-URL eines Festkalenders) → klärt, ob ein zweiter URL-Calendar überhaupt akzeptiert wird.
3. **External-Accounts-Test**: `GET /api/external-accounts` → schon ein Google-Account verbunden? Wenn ja, ist sichtbar, ob der Server bei `POST /connect` ein zweites Google-Account zulässt.
4. **`writable`-Test (mit eigenem CalDAV-Server)**: Lokalen Radicale aufsetzen, in Daely als writable URL-Calendar registrieren, im Tablet ein Event erstellen, prüfen ob es im Radicale ankommt.

**Alle vier sind Live-Calls gegen `daely-connect.com` und brauchen explizite Freigabe.**
