# 99 – Offene Fragen und Phase-Übergänge

## Zusammenfassung Phase 0 + Phase 1' (Flutter-Anpassung)

| Phase | Status | Kommentar |
|---|---|---|
| 0 – APK-Inventur | ✅ | `00_OVERVIEW.md`. Flutter-Build (Dart 3.8.1 AOT) bestätigt – Stop-Kriterium aus CLAUDE.md getriggert, Plan angepasst. |
| 0a – Format-Check | ✅ | XAPK aufgesplittet, base.apk + libs separiert. |
| 1' – Flutter-Statik via blutter | ✅ | blutter erfolgreich gebaut (mit User-Space-Deps + ICU-Symlink-Workaround), 116 MB asm + 2.6 MB Object Pool extrahiert. `findings/blutter_out/`. |
| 1 – API-Endpoints | ✅ | `01_ENDPOINTS.md`. 9 Service-Klassen, ~50 Endpoint-Pfade, Base-URL, Auth-Mechanik. |
| 2 – Foto-Limit | ⚠️ teilweise | `03_PHOTO_LIMIT.md`. Statisch: `maxImages` ist Server-Feld, kein Client-Hardcode. Server-Enforcement-Status nur per Live-Test klärbar. |
| 2 – Calendar-Sync | ⚠️ teilweise | `04_CALENDAR_SYNC.md`. Drei Sync-Sourcen, `SyncTokenPair`-Mechanik, `writable`-Feld auf URL-Calendars. Tatsächliches Hersteller-Limit nicht ersichtlich. |
| 2 – Cert-Pinning | ✅ | Negativ. Default-TLS, kein Pinning – relevant für ggf. spätere Phase 5. |
| Auth-Flow | ✅ | `02_AUTH.md`. Keycloak Realm `daely`, Client `mobile-app`, ROPC supported, `offline_access` möglich. Discovery vom User direkt geholt. |
| 3 – Test-Client | ⏸ | Skelett in `02_AUTH.md` enthalten, **nicht ausgeführt** (CLAUDE.md-Regel). Erste Live-Calls brauchen User-Freigabe. |
| 4 – Hypothesen-Verifikation | ⏸ | Nicht begonnen. Konkrete Test-Listen in `03/04_*.md`. |
| 5 – Live-Capture | ⏸ | Nicht nötig auf aktuellem Stand. Kein Cert-Pinning erkannt → falls jemals nötig, einfach realisierbar. |

## Was statisch nicht beantwortbar ist

### A. Server-seitige Validierung von `maxImages`
**Frage**: Akzeptiert der Backend-Server einen 16. Foto-Upload mit 200, oder antwortet er 4xx?
**Status**: Unbekannt. Statisch nicht entscheidbar.
**Test**: 1 kontrollierter `POST /api/gallery/groups/<gid>/upload` – nach Verifikation mit GET, dass aktuell `images.length == maxImages` ist. Bedarf User-Freigabe (Phase 4).
**Folge wenn nicht enforced**: Direkter Bypass durch Python-Client. Mission gelöst.
**Folge wenn enforced**: Workaround über mehrere Familien-Groups oder Self-Service-PUT auf `/config` testen.

### B. Server-seitiges Limit für URL-Calendar-Anzahl
**Frage**: Wie viele `UrlCalendar`-Einträge erlaubt der Server pro User/Group?
**Status**: Unbekannt. Statisch keine Begrenzung im Modell ersichtlich.
**Test**: 1 zusätzlichen URL-Calendar via `POST /api/url-calendars` hinzufügen, prüfen, ob 200 oder 4xx.
**Folge wenn unbeschränkt**: Eigener CalDAV-Bridge-Server lokal hosten, dort beliebig viele Kalender konsolidieren, eine URL nach Daely melden.

### C. Tatsächliches Verhalten des `PUT /api/gallery/.../config`-Endpoints
**Frage**: Akzeptiert der Server `{maxImages: 99}` als Self-Service-Request?
**Status**: Code-Pfad existiert (DioMixin::put-Aufrufe), aber wahrscheinlich vom Server abgelehnt (sonst wäre der ganze Limit-Mechanismus sinnlos).
**Test**: 1 `PUT`-Call, sehen ob 200 oder 4xx.

### D. HTTP-Methoden / Body-Schemas für Group/List/Chore/Meal-Plan-Endpoints
**Frage**: Vollständige Method-Mapping pro Endpoint?
**Status**: Sample-weise verifiziert, nicht erschöpfend.
**Aufwand**: 1-2h tiefere blutter-Asm-Analyse pro Service ODER durch Live-Capture in 5 Min komplett.
**Mission-Relevanz**: Nur indirekt – Nicht nötig für Foto-/Calendar-Workaround. Wird aber für einen vollständigen Python-Client gebraucht.

### E. Einzelwerte der Enum-Typen
**Frage**: Was sind die konkreten Werte von `ShareType`, `UpdateCalendarEventType`, `AccountType`, `CalendarPresentationType`?
**Status**: Enum-Marker-Strings sichtbar (z. B. `"ShareType."`), aber Einzelwerte nicht ohne tieferes Disassembly.
**Aufwand**: 30 Min für jeden, oder 5 Min via Live-API-Sample.

### F. Pagination
**Frage**: Werden `?page=`, `?cursor=`, `?since=` o. ä. Query-Params irgendwo verwendet?
**Status**: Bisher nicht in Strings aufgetaucht. Wahrscheinlich nicht implementiert (junges Backend, Familien-Daten sind klein).
**Test**: Erstmals beim Live-Call Schauen, ob Response Pagination-Cursor enthält.

### G. SSE/WebSocket
**Strings-Treffer**: `package:sse_channel/io.dart`, `package:web_socket_channel/...`, `common/service/realtime/...`. Backend hat Realtime-Push-Mechanismus.
**Frage**: Welcher Endpoint? Welches Auth?
**Status**: Nicht analysiert.
**Mission-Relevanz**: Niedrig – Polling reicht für Mission.

## Beobachtete Sicherheits-/Privacy-Aspekte

(Per CLAUDE.md-Regel hier dokumentieren, nicht ausnutzen.)

- **Keycloak Public Client mit ROPC**: Kein Sicherheits-Bug an sich, aber Attack-Surface für Credential-Stuffing. Server hat hoffentlich Brute-Force-Schutz.
- **Refresh-Token in `flutter_secure_storage`**: Standard. Kein lokales Cleartext gefunden.
- **Cleartext-`.env`-Datei in der APK**: Enthält Google-Client-IDs, MS-Client-ID. Das ist normal (Public Clients, IDs sind nicht geheim). Aber: jeder Reverser sieht sie.
- **Keine Anti-Tampering-Mechanik**: Keine SafetyNet/Play-Integrity-Checks, kein App-Attestation. Eine modifizierte APK könnte unerkannt agieren. **Per Mission verboten** (modifizierte APK aufs Tablet).
- **Kein Cert-Pinning**: MITM-Attacks gegen einen ahnungslosen User in einem WLAN möglich – aber das ist eine Schwachstelle, die der Hersteller adressieren sollte, nicht eine, die wir ausnutzen.

Falls beim Live-Test der Backend-Server mit auffälligem Verhalten reagiert (z. B. unauthentifizierte IDOR-Treffer, Privilege-Escalation), wandert das in `findings/SECURITY_RESPONSIBLE_DISCLOSURE.md` (heute noch nicht angelegt, kein Befund).

## Vorschlag für nächste Iteration

**Empfehlung**: Mission-orientiert in 3 Schritten weitermachen:

1. **Kleinster sinnvoller Live-Test (Phase 3-Start)**: Nur ROPC-Login → `GET /api/users/me` → Token persisten. Klärt, ob ROPC tatsächlich gehen, welcher Token wie lange gültig ist, ob das User-Modell wie vermutet aussieht. **1 Live-Call**.

2. **Status-Quo-Reads (Phase 3)**: `GET /api/groups`, `GET /api/url-calendars`, `GET /api/external-accounts`, `GET /api/gallery/groups/<gid>/overview` mit echten Daten des User-Accounts. Keine Schreib-Ops. Klärt aktuelles Setup. **4 Live-Calls**.

3. **Kontrollierte Schreib-Probe (Phase 4)**: Je nach Phase-3-Ergebnis ein einzelner zielgerichteter POST/PUT/DELETE pro offene Frage (A, B, C oben). **Pro Frage 1 Call.**

Vor jedem Schritt User-Freigabe einholen.

Falls der User stattdessen direkt zu **Phase 5 (mitmproxy)** will, ist das jetzt deutlich einfacher als ursprünglich gedacht: kein Cert-Pinning, kein App-Attestation – ein gerooteter Emulator + System-CA-Push reicht. Trotzdem 4-6h Setup, daher nicht der Default.
