# 10 – Realtime-API (`/realtime`-Endpoint)

> **Stand 2026-06-03, GELÖST (v1.6.0):** Realtime-Push funktioniert. Die
> SignalR-Integration empfängt `ReceiveNotification`-Events live (Termin am
> Handy angelegt → Notification in ~1 s auf der Bridge-Connection).
>
> Zwei Bugs hatten es in v1.0–v1.5 blockiert, **beide live diagnostiziert**:
> 1. **`calendars: null`/`[]` = „subscribe zu KEINEN Kalendern".** Der
>    `SetFilter` braucht die **echten internen Calendar-UUIDs**. Leerer
>    Array → Connection für nichts registriert → keine Pushes (obwohl
>    `SetFilter` mit `result:null`/Erfolg bestätigt wird).
> 2. **Subject-Format ist punkt-getrennt**, nicht slash-getrennt — der
>    Parser hätte echte Events verworfen.
>
> Die v1.1.0-Hypothese „Same-Account-Suppression" war **falsch** (widerlegt
> u. a. dadurch, dass Handy↔Tablet desselben Accounts sich gegenseitig per
> Push sehen). Kein mitmproxy nötig — der Re-Test mit garantiert-gutem
> Trigger (Handy-Anlegen) + echten Calendar-IDs hat's geklärt.

## TL;DR

Daely betreibt eine **SignalR-basierte Realtime-API** unter
`https://daely-connect.com/realtime`. Die offizielle App (Flutter via
`signalr_netcore`-Paket) nutzt sie, um Notification-Events vom Backend
push-basiert zu empfangen — statt zu polln.

Nicht plain SSE: SignalR ist Microsofts ASP.NET-Core-Realtime-Stack mit
JSON-Hub-Protokoll. Das Protokoll auto-negotiates Transports (WebSocket
bevorzugt, SSE als Fallback, LongPolling als letzte Option).

## Endpoint-Map

| Methode | Pfad                                | Zweck                                                           |
|---------|-------------------------------------|-----------------------------------------------------------------|
| POST    | `/realtime/negotiate?negotiateVersion=1` | Standard-SignalR-Negotiate. Liefert `connectionId`, `connectionToken`, `availableTransports`. Bearer-Auth nötig. |
| GET     | `/realtime?id=<connectionToken>`    | SSE-Transport (Accept: `text/event-stream`). Bearer-Auth nötig.|
| WS      | `wss://daely-connect.com/realtime?id=<connectionToken>` | WebSocket-Transport. Bearer-Auth via Query oder Header.      |

**Auth:** Standard-Bearer-Token aus dem Daely-OAuth-Flow (gleicher AT wie für
`/api/*`). Bei Disconnect ruft die App `acquireNewToken` auf und reconnected.

## SignalR Hub-Protokoll (JsonHubProtocol v1)

### Server → Client

**Hub-Method:** `ReceiveNotification(map)` — der Server invokiert diese
Methode auf dem verbundenen Client. Map-Inhalt entspricht einem
serialisierten `RealtimeEvent`-Objekt.

### Client → Server

**Hub-Method:** `SetFilter(filter)` — der Client schickt sein Filter-Objekt
nach Connect-Etablierung (verm. einmalig direkt nach handshake_ok).
Argument: serialisiertes `RealtimeFilter`-Objekt.

**Keine weiteren `invoke()`-Calls** entdeckt — die App schickt nur SetFilter
und konsumiert dann passiv `ReceiveNotification`-Pushes. Sehr schlank.

## Datenmodelle

### `RealtimeFilter` (Client → Server)

JSON-Felder entdeckt aus `_$$RealtimeFilterImplToJson` in
`asm/common/models/realtime/realtime_filter.dart`:

```json
{
  "user":                    "<user-uuid>",
  "group":                   "<group-uuid>",
  "subscribeUserCalendars":  true,
  "subscribeGroupCalendars": true,
  "subscribeChores":         false,
  "subscribeChecklists":     false
}
```

**Beobachtungen:**
- `user` ist die UUID des angemeldeten User-Accounts (matcht
  `UserMe.id` aus `findings/02_AUTH.md`).
- `group` ist die Group-UUID, deren Calendar/Chores/Checklists subscriben.
- Die `subscribe*`-Flags gates pro Topic-Kind: nur die mit `true` werden
  gepusht.
- Für die Bridge sinnvoll: `subscribeUserCalendars=true`,
  `subscribeGroupCalendars=true`, Rest auf `false`. Wir wollen nur
  Calendar-Events.

### `RealtimeEvent` (Server → Client) — ECHTES Wire-Format (live verifiziert)

Ein live empfangenes `ReceiveNotification`-Argument (UUIDs hier synthetisch):

```json
{
  "resourceType": "Calendar",
  "subject": "calendar.calendar.<calendarUuid>.event.<eventUuid>.created",
  "time": "2026-06-03T07:18:19.9431427+00:00"
}
```

**Wichtig — das statische `toString`-Schema war irreführend.** Das echte
Payload hat **nur** `resourceType`, `subject`, `time`. Die im
Dart-`toString` sichtbaren Felder `topic`/`entityId`/`topicKind`/`topicKindId`
tauchen im Wire-Payload **nicht** auf (vermutlich interne Struktur nach
dem Parsing, nicht das Transport-Format).

**Subject ist PUNKT-getrennt** (nicht `/`):

```
calendar . calendar . <calendarUuid> . event . <eventUuid> . <action>
   │          │            │            │          │           │
 domain   resource       cal-id      resource   event-id   created|
                                                           updated|deleted
```

- UUIDs enthalten Bindestriche, nie Punkte → `split(".")` ist sicher.
- `domain` (erstes Segment) bestimmt das Routing: `calendar`, `chore`,
  `checklist`, `group`, `user`, `administration`, `meal-plan`.
- `<action>` (letztes Segment): `created` | `updated` | `deleted`.
- Calendar-Events: `event_id` = Segment nach `event`, `calendar_id` =
  Segment nach dem zweiten `calendar`.

Die Bridge (`RealtimeEvent` in `models.py`) parst genau das: `domain`,
`is_calendar_event`, `action`, `event_id`, `calendar_id`.

## Verbindungs-Lebenszyklus (RealtimeService)

Aus den State-Strings (`SignalR connected`, `SignalR reconnected:`,
`SignalR disconnected:`, `Reconnected to SignalR`, `Could not init realtime
service`, `Initial connection failed:`):

1. **Init:** `HubConnectionBuilder().withUrl(API_URL + "/realtime",
   accessTokenFactory: ...).build()` — auto-negotiate-Transport.
2. **Register handler:** `connection.on("ReceiveNotification", _onMessage)`.
3. **Start:** `connection.start()` — POST negotiate, dann Transport-Connect.
4. **SetFilter:** `connection.invoke("SetFilter", [<filter-json>])`.
5. **Receive loop:** Server pushed `ReceiveNotification`-Messages; Client
   dispatcht via `subject`-Routing in die zuständigen Cubits (`CalendarCubit
   ::_onRealtimeEvent`, `ChoreCubit::_onRealtimeEvent`, etc.).
6. **Disconnect-Recovery:** `_retryConnection` mit Exponential-Backoff;
   `acquireNewToken` ruft den OAuth-Refresh, falls AT abgelaufen.

## Was die Bridge daraus baut (Plan)

### Architektur

```
                       ┌─────────────────────┐
                       │  Daely backend      │
                       │  /realtime (SignalR)│
                       └─────────┬───────────┘
                                 │ ReceiveNotification(map)
                                 ▼
┌───────────────────────────────────────────────┐
│  RealtimeClient (new module)                  │
│  - SignalR JSON Hub Protocol                  │
│  - SSE transport (simpler than WS)            │
│  - SetFilter(user, group, calendars=true)     │
│  - on_event callback                          │
│  - reconnect-on-failure with backoff          │
└───────────────────┬───────────────────────────┘
                    │ event["subject"] starts with "calendar/" ?
                    ▼
        ┌───────────────────────┐
        │ targeted_sync()       │ ← incremental_sync with smaller window
        └───────────────────────┘
                    │
                    ▼
              event_mapping table
              + Google patch/insert
```

### Strategie

- **SSE transport, nicht WS.** Begründung: einfacher zu implementieren,
  kein Binary-Frame-Handling, simpel via `httpx.stream()`. SignalR fragt im
  Negotiate explizit nach `ServerSentEvents`-Transport (siehe
  `SERVERSENTEVENTS`-String im Constants-Pool).
- **Polling bleibt als Fallback.** Bei Realtime-Disconnect-Loop läuft das
  Polling weiter — die Bridge wird nicht "nur" realtime, sondern
  "realtime mit Polling-Safety-Net".
- **Targeted Sync, nicht Full.** Ein eintreffendes Event mit
  `subject=calendar/event` triggert ein spezielles `targeted_sync(entityId)`,
  das nur dieses Event neu von Daely zieht und Google-seitig patcht — viel
  billiger als ein voller Cycle.
- **Konfigurations-Toggle.** `realtime.enabled: false` (default) → Bridge
  bleibt im klassischen Polling-Modus. Erst opt-in nach Erst-Test.

### Risiken

1. **Server-Verhalten unter Filter-Subscribe.** Wir wissen nicht
   100 %, ob der Server fehlertolerant ist, wenn wir nur calendar-Topics
   subscriben. Probe-Bedarf.
2. **Re-Connect-Logik.** Refresh-Token-Rotation während offener
   Stream-Verbindung ist nicht-trivial. Tests müssen das abdecken.
3. **Resource-Verbrauch beim Backend.** Eine persistente Connection
   pro Bridge-Instanz statt einem 15-min-Polling-Hit. Reduziert Last
   eigentlich, ist aber ein anderes Lastprofil — Daely könnte das via
   Connection-Limits drosseln. → Polling-Fallback fängt das ab.

## Vor Implementierung erforderlich

**Probe-Phase B** mit User-Freigabe (CLAUDE.md Regel 6):

1. **Probe 1 — Negotiate:** `POST /realtime/negotiate?negotiateVersion=1`
   mit Bearer. Klärt:
   - Welche Transports der Server wirklich anbietet (WebSockets, SSE, LP)
   - Format des Negotiate-Response (Standard-SignalR oder Daely-Variante)
   - Connection-ID/Token-Format
   1 Live-Call. Niedriges Risiko (read-only Discovery).

2. **Probe 2 — SSE-Connect + SetFilter + capture:** Connect auf
   `GET /realtime?id=<token>` mit `Accept: text/event-stream` + Bearer,
   Handshake senden, `SetFilter`-Invoke schicken (mit User+Group-UUID + nur
   `subscribeUserCalendars`/`subscribeGroupCalendars`), Stream für ~60s
   capturen + anonymisieren. Klärt:
   - Genaue Frame-Struktur der `ReceiveNotification`-Messages
   - Welche Felder das `RealtimeEvent`-Objekt wirklich enthält
   - Ob `SetFilter` ohne weitere Argumente funktioniert
   - Heartbeat/Ping-Verhalten des Servers
   1 persistente Connection für 60s. Mittleres Risiko, da etwas Erprobungs-
   Verkehr entsteht. Anonymisierung der gecaptureten Frames vor Persistenz.

## Live-validierte Erkenntnisse (Probe-Phase B, 2026-05-08)

Drei live Probes (alle mit User-Freigabe pro Session) haben die statische
Analyse bestätigt + folgende präzise Details ergänzt:

### Negotiate-Response (echt)

```json
{
  "negotiateVersion": 1,
  "connectionId": "<22-char base64ish>",
  "connectionToken": "<22-char base64ish>",
  "availableTransports": [
    {"transport": "WebSockets", "transferFormats": ["Text", "Binary"]},
    {"transport": "ServerSentEvents", "transferFormats": ["Text"]},
    {"transport": "LongPolling", "transferFormats": ["Text", "Binary"]}
  ]
}
```

Standard ASP.NET Core SignalR. Alle drei Transports angeboten, wir wählen
SSE für die Bridge.

### SSE-Connection-Lifecycle

1. `GET /realtime?id=<connectionToken>` mit `Accept: text/event-stream` +
   `Authorization: Bearer <AT>` → `200 OK`, Headers:
   - `content-type: text/event-stream`
   - `transfer-encoding: chunked`
   - `cache-control: no-cache,no-store`
2. Server sendet sofort einen SSE-Comment `:\r\n` als Alive-Signal.
3. Client POSTet Handshake `{"protocol":"json","version":1}\x1e` an
   die gleiche URL (`Content-Type: text/plain;charset=UTF-8`).
4. POST-Response: `200 OK` mit leerem Body.
5. Server pushed über SSE: `data: {}\x1e\r\n\r\n` — Handshake-Ack (leeres
   JSON-Objekt = OK).
6. Client POSTet `SetFilter`-Invocation:
   `{"type":1,"invocationId":"1","target":"SetFilter","arguments":[<filter>]}\x1e`
7. Server pushed über SSE:
   `data: {"type":3,"invocationId":"1","result":null}\x1e\r\n\r\n` — Result
   (type 3) mit `result: null` = SetFilter OK.
8. Server pushed alle **15 Sekunden** einen Ping:
   `data: {"type":6}\x1e\r\n\r\n`. SignalR-Default-`keepAliveInterval`.
9. Bei einem Backend-Event (Calendar/Group/etc.): Server pushed Invocation
   `data: {"type":1,"target":"ReceiveNotification","arguments":[{...event-payload...}]}\x1e\r\n\r\n`.

### SSE-Frame-Format (präzise)

- SSE-Event-Separator: **`\r\n\r\n`** (nicht `\n\n`!)
- Innerhalb eines Events: Zeilen ab `data: ` enthalten den Payload
- SignalR-Message-Separator innerhalb eines `data:`-Wertes: **`\x1e`** (RS)
- Eine SSE-Event-Frame kann mehrere SignalR-Messages enthalten, getrennt
  durch `\x1e`

### RealtimeFilter — vollständige Feld-Liste

JSON-Reihenfolge wie vom Dart-toJson serialisiert:

```json
{
  "user":                    "<user-uuid>",
  "group":                   "<group-uuid>",
  "subscribeUserCalendars":  true,
  "subscribeGroupCalendars": true,
  "calendars":               [],                    // ← bisher übersehen!
  "subscribeChores":         false,
  "subscribeChecklists":     false
}
```

Bedeutung von `calendars`: vermutlich Liste konkreter Calendar-UUIDs für
Whitelist-Subscribe; `[]` heißt „keine Whitelist, alle subscribed
Calendars-Topics greifen". Akzeptiert ohne Fehler.

### Geprüfte Best-Practices für Reconnect

- Bei `401` auf der SSE-GET: AT abgelaufen → via Daely-Refresh erneuern,
  neuen Token in nächsten `GET` und `POST`. SignalR's `accessTokenFactory`
  würde das automatisch tun.
- Bei `close`-Message (`type=7`) vom Server: clean disconnect, neu
  negotiaten + reconnecten.
- Bei TCP-Drop (read-error auf SSE-Stream): exponential backoff (1s, 2s,
  4s, …, capped bei 5min) + neu negotiaten.
- `SetFilter` muss nach jedem Reconnect erneut gesendet werden (Server
  hält Filter pro Connection-Token, nicht pro User-Account).

### Confidence (post-Probe)

**high** auf:
- Vollständiger Connection-Lifecycle (negotiate → SSE → handshake → SetFilter)
- Frame-Formate (CRLF separator, RS message terminator, `data:` prefix)
- 15-Sekunden-Heartbeat-Interval
- SetFilter-Argument-Schema (incl. `calendars`)
- Authentication (Bearer im Header funktioniert auf SSE)

**medium** auf:
- Komplette Felder von `RealtimeEvent` — die statisch gefundenen 5
  (subject, topic, entityId, topicKind, topicKindId) sind sehr wahrscheinlich
  korrekt, aber wir haben kein Live-Sample (Tablet-Edits während der Probes
  haben keine Events ausgelöst — möglicherweise weil keine Edits gemacht
  wurden, oder weil der Server bei Edits durch den selben Account keine
  Notifications fired).

**low** auf:
- Verhalten bei `subscribeChores: false` mit zusätzlichen Topic-Edits.
  Da wir kein Live-Event gesehen haben, müssen wir das in Production
  beobachten + via aggressivem Logging der ersten Events validieren.

### Strategie für die Bridge-Implementation

- `RealtimeEvent`-Pydantic-Modell mit `extra="ignore"` — unbekannte Felder
  werden toleriert, falls Server mehr/andere Felder schickt
- `RealtimeClient` loggt das **erste** ReceiveNotification jedes Subjects
  vollständig (raw JSON) für die initiale Validation
- Routing-Logik: `event.is_calendar_event` (domain == `calendar`) → Trigger
  Sync. Alles andere wird gedroppt + geloggt.
- Polling bleibt aktiv als Safety-Net.

## Gelöst: warum kamen vorher keine Notifications? (v1.6.0)

In v1.0–v1.5 kam **null** `ReceiveNotification`, obwohl Connection, Handshake,
`SetFilter`-Completion und Pings alle sauber liefen. Zwei Ursachen, beide am
2026-06-03 live diagnostiziert:

### Ursache 1 — leerer `calendars`-Array = keine Subscription

`SetFilter` wurde **immer** mit `{"type":3,"result":null}` (Erfolg) bestätigt
— aber „akzeptiert" ≠ „subscribed". Ein **leerer oder `null`** `calendars`-
Array registriert die Connection für **keine** Kalender. Erst mit den
**echten internen Calendar-UUIDs** im Array fließen Pushes:

```
calendars: []              → 0 Notifications  (v1.0.0/1.0.1)
calendars: null            → 0 Notifications  (v1.0.3–1.5)
calendars: [<echte cal-id>]→ Notification in ~1 s  ✅ (v1.6.0)
```

Die `subscribe*Calendars`-Booleans allein reichen **nicht** — die `calendars`-
Liste ist Pflicht.

### Ursache 2 — Subject-Format-Annahme war falsch

Selbst wenn der Push angekommen wäre, hätte ihn `is_calendar_event` verworfen:
der Code erwartete `calendar/event` (Slash), das echte Subject ist aber
`calendar.calendar.<id>.event.<id>.<action>` (Punkte). Siehe RealtimeEvent-
Abschnitt oben.

### Widerlegt: „Same-Account-Suppression" (v1.1.0-Hypothese)

Die v1.1.0-Vermutung — der Server pushe nicht an weitere Connections desselben
Accounts — war **falsch**. Belege:
- Handy ↔ Tablet desselben Accounts sehen sich gegenseitig per Push
  (regelmäßiger User-Workflow, kein Tablet-Eingriff nötig).
- Mit korrektem `calendars`-Array empfängt unsere Bridge-Connection (gleicher
  Account) den Push sofort.

Es war nie eine strukturelle Limitation — nur ein leerer Array + ein falscher
Parser. Kein mitmproxy nötig; der Re-Test mit garantiert-gutem Trigger
(Termin am Handy anlegen) + echten Calendar-IDs hat's geklärt.

### Verifizierter Connection-Lifecycle (unverändert korrekt)

- `POST /realtime/negotiate?negotiateVersion=1` → 200, `connectionToken`
- `GET /realtime?id=<token>` (+ `&access_token=<at>`) → 200 `text/event-stream`
- Handshake `{"protocol":"json","version":1}\x1e` → Server: `{}`
- `SetFilter`-Invocation **mit echten calendar-IDs** → `{"type":3,"result":null}`
- Pings `{"type":6}` alle 15 s
- Bei Change: `{"type":1,"target":"ReceiveNotification","arguments":[{…}]}`

## Einzel-Event-Detail-Endpoint — was er (NICHT) liefert (Probe 7/8, v1.6.0)

`GET /api/groups/<gid>/calendars/<calId>/events/<eventId>` — live verifiziert,
read-only. Klärt eine zentrale Frage: **kann man über die Event-ID die
gelöschten Serien-Instanzen erfahren?** → **Nein.**

**Befund (alle UUIDs hier synthetisch):**
- Für ein **Serien-Master** liefert der Endpoint das **unexpandierte
  Master-Objekt** mit den normalen 17 CalendarEvent-Feldern:
  ```json
  {"id": "<masterUuid>", "recurringId": null,
   "recurrence": ["RRULE:FREQ=DAILY;UNTIL=20260613T235900Z;INTERVAL=1"],
   "deleted": false, "updated": "<creation-time>", … }
  ```
- **Keine** Felder `exceptions` / `exdate` / `excludedDates` /
  `deletedInstances` / `overrides`. Die Key-Liste ist exakt das normale
  Event-Schema.
- **Beim Löschen einzelner Instanzen ändert sich das Master-Objekt NICHT:**
  `recurrence` bleibt die volle Original-RRULE, `updated` wird **nicht**
  hochgesetzt. Über mehrere Einzel-Löschungen hinweg identisch.
- Erst beim Löschen der **ganzen** Serie → `404 {"message":"Event not found."}`.

**Konsequenz.** Es gibt **keinen** API-Pfad, der gelöschte Instanzen explizit
ausgibt:
| Quelle | Info über gelöschte Einzel-Instanz |
|---|---|
| Realtime-Notification | nur Master-ID + `deleted` (Einzel-Instanz ununterscheidbar von Ganz-Serien-Löschung) |
| `GET events/<masterId>` | pristine Master, volle RRULE — **nichts** |
| `with-events` | lässt Instanz still aus der Expansion weg → **Lücke** (einzige Spur) |

Daely wendet die Löschung server-seitig **beim Expandieren** an, legt sie aber
nirgends als Datenfeld offen. Das Tablet rendert vermutlich einfach die
Expansion neu (braucht nie „welche Instanz"). Unser Problem ist einzig der
**Daely→Google-RRULE-Übergang** (Google expandiert die volle RRULE, kennt die
Lücke nicht). Daraus folgt die dokumentierte Grenze in
`findings/09 §3.1b`: gelöschte **erste** Instanz nur via Anker-Persistenz
lösbar, nicht über die API.
