# 09 – Future Roadmap & Erweiterungs-Möglichkeiten

> **Stand:** 2026-05-08 (post-3f, nach Profil-Color-Mapping-Release)
> **Zweck:** Umfassender Katalog möglicher Erweiterungen der `daely-google-bridge`,
> jeweils mit Bewertung von Wert, Komplexität, Risiko und Empfehlung. Dient
> als Entscheidungs-Hilfe — keine Implementierungs-Spezifikation.

## Executive Summary

Die Bridge erfüllt heute ihren Kern-Auftrag: **Daely-Events sichtbar in
Google-Calendar-Widgets, Home Assistant und sonstigen Google-integrierten
Tools**. Mit Phase 3f (Profil-Farben + Multi-Participant-Emoji-Prefix) ist
auch die letzte UX-Limitation aus der ursprünglichen Mission erschlagen.

Was jetzt sinnvoll wäre, sortiert sich grob in drei Cluster:

1. **Robustheit-Plus** — Schema-Migrations, Retry-Loop, Health-Endpoint,
   `bridge resync`/`bridge re-color`/`bridge doctor`-Commands. Niedrig-
   hängende Früchte, die den 24/7-Betrieb netter machen ohne neue Features
   zu erfordern. **Stand v0.1.0: alle umgesetzt.**
2. **Reicheres Mapping** — Reminder-Optionen, Recurring-Instance-Overrides,
   konfigurierbare Description-Templates, optionale Location-Geocoding.
   Inkrementelle UX-Wins.
3. **Photo-Bridge** — das ursprüngliche Mission-Ziel, nie umgesetzt. Hoher
   Aufwand, mittlerer Wert für den aktuellen Use-Case (Widgets sind gelöst).

Reverse-Sync (Google → Daely) und Multi-Mandanten-Hosting bleiben **bewusst
out-of-scope** — siehe §11.

> **Update 2026-05-08:** Der zuvor als "harte Limitation" gelistete Punkt
> *MFA-Support via Device-Code-Flow* (§2.1) ist obsolet — Daely bietet User-
> seitig kein MFA an. ROPC läuft stabil. Sollte sich das je ändern, ist
> der Pfad in §2.1 als Referenz erhalten geblieben.

---

## Bewertungs-Schema

Damit die Tabelle und die Detail-Sektionen mit konsistenten Markern arbeiten:

**Wert** (für den aktuellen User-Use-Case):
- ★☆☆ niedrig — nice to have, kein konkretes Pain-Point
- ★★☆ mittel — adressiert ein bekanntes Pain-Point oder erschließt sinnvollen
  Sekundär-Use-Case
- ★★★ hoch — signifikanter UX-Win oder hebt eine harte Limitation auf

**Komplexität** (Aufwand = sinnvoller Tagesschätzwert für Implementierung
inkl. Tests + Doku):
- **S** klein — 2–4 Stunden, isolierte Code-Änderung, keine neuen Endpoints
- **M** mittel — 1–2 Tage, neue Module/Endpoints/Probe-Calls, Schema-Erweiterung
- **L** groß — 3–7 Tage, neue Subsystem-Komponente (z. B. Web-UI, Provider)
- **XL** sehr groß — 2+ Wochen, Architektur-Eingriff oder neuer Stack-Layer

**Risiko** (was kann schiefgehen, wenn's nicht sauber gebaut ist):
- 🟢 niedrig — rein lokale Logik, Tests reichen, kein Live-Daten-Risiko
- 🟡 mittel — braucht Live-Tests gegen Daely oder schreibt nach Google;
  Fehler sind reversibel
- 🔴 hoch — produktive Schreib-Calls gegen Daely-Backend, Foto-Uploads
  (15-Bilder-Limit ist destruktiv), oder Calendar-Mass-Operations mit
  potentiellem Datenverlust

**Status** (informativ):
- *open* — noch nicht angefangen
- *scoped* — grob designed, aber nicht implementiert
- *partial* — Teil-Implementierung vorhanden

---

## Übersichts-Tabelle

| #     | Feature                                          | Cluster   | Wert | Kompl. | Risiko | Status   | Empfehlung    |
|-------|--------------------------------------------------|-----------|:----:|:------:|:------:|----------|---------------|
| 1.1   | Echtzeit-Sync via Daely-SignalR (`realtime`)     | Engine    | ★★★  | L      | 🟡     | **done v1.0.0** | —         |
| 1.2   | Retry-Loop für `failed=true`-Mappings            | Engine    | ★★☆  | S      | 🟢     | **done v0.1.0** | —         |
| 1.3   | Schema-Migrations für SQLite                     | Engine    | ★★★  | S      | 🟢     | **done v0.1.0** | —         |
| 1.4   | Health-Check-HTTP-Endpoint                       | Engine    | ★★☆  | S      | 🟢     | **done v0.1.0** | —         |
| 1.5   | Prometheus-Metrics-Exporter                      | Engine    | ★☆☆  | M      | 🟢     | open     | Maybe         |
| 1.6   | Multi-Account-Setup (mehrere Daely-Logins)       | Engine    | ★☆☆  | L      | 🟡     | open     | No (yet)      |
| 1.7   | Quota-aware Backoff für Google                   | Engine    | ★☆☆  | S      | 🟢     | partial  | Maybe         |
| 2.1   | Device-Code-Flow statt ROPC (MFA-Support)        | Auth      | ☆☆☆  | M      | 🟡     | obsolete | No (Daely bietet kein MFA) |
| 2.2   | Token-Encryption-at-Rest in SQLite               | Auth      | ★☆☆  | M      | 🟢     | open     | No            |
| 3.1   | Recurring-Instance-Overrides (Modified Instances)| Mapping   | ★★☆  | L      | 🟡     | open     | Maybe         |
| 3.2   | Reicheres Reminder-Mapping (multi-method)        | Mapping   | ★☆☆  | S      | 🟢     | open     | Maybe         |
| 3.3   | Daely-Participants → Google-Attendees            | Mapping   | ★☆☆  | M      | 🟡     | open     | No            |
| 3.4   | Location-Geocoding für Maps-Links                | Mapping   | ★☆☆  | M      | 🟢     | open     | No            |
| 3.5   | Privacy-Modus (Title/Location maskieren)         | Mapping   | ★★☆  | S      | 🟢     | open     | Maybe         |
| 3.6   | `customColorCode` ehren (per-event Override)     | Mapping   | ★☆☆  | S      | 🟢     | open     | Maybe         |
| 3.7   | Konfigurierbare Description-Templates            | Mapping   | ★★☆  | M      | 🟢     | open     | Maybe         |
| 3.8   | i18n (Footer-Text, Datums-Format)                | Mapping   | ★☆☆  | S      | 🟢     | open     | No            |
| 4.1   | Foto-Upload-Bridge (ursprüngliches Mission-Ziel) | Photos    | ★★☆  | XL     | 🔴     | open     | Maybe (later) |
| 4.2   | Foto-Source-Adapter (Google Photos, lokal)       | Photos    | ★★☆  | L      | 🟡     | open     | Hängt an 4.1  |
| 5.1   | `bridge resync <cal_id>` als echter Command      | CLI       | ★★☆  | S      | 🟡     | **done v0.1.0** | —         |
| 5.2   | `bridge re-color` Convenience-Command            | CLI       | ★★☆  | S      | 🟢     | **done v0.1.0** | —         |
| 5.3   | `bridge profile list/show` Inspection-Commands   | CLI       | ★☆☆  | S      | 🟢     | open     | Maybe         |
| 5.4   | `bridge doctor` Health-Diagnose-Command          | CLI       | ★★☆  | S      | 🟢     | **done v0.1.0** | —         |
| 5.5   | Web-UI (Bootstrap + Status-Dashboard)            | UX        | ★★☆  | XL     | 🟡     | open     | No (yet)      |
| 6.1   | GitHub Action für `pytest` in CI                 | CI        | ★★★  | S      | 🟢     | **done v0.1.0** | —         |
| 6.2   | Coverage-Gate (Codecov o. ä.)                    | CI        | ★☆☆  | S      | 🟢     | open     | Maybe         |
| 6.3   | Integration-Tests gegen Mock-Daely-Server        | CI        | ★★☆  | L      | 🟢     | open     | Maybe         |
| 6.4   | Release-Tagging-Workflow + Changelog             | CI        | ★★☆  | S      | 🟢     | **done v0.1.0** | —         |
| 7.x   | Reverse-Sync (Google → Daely)                    | Sync      | —    | XL     | 🔴     | n/a      | **No**        |
| 8.1   | Apple Calendar (CalDAV) als Alternativ-Target    | Provider  | ★☆☆  | L      | 🟡     | open     | No            |
| 8.2   | Microsoft Outlook als Alternativ-Target          | Provider  | ★☆☆  | L      | 🟡     | open     | No            |
| 8.3   | Native `.ics`-Export-Endpoint                    | Provider  | ★★☆  | M      | 🟢     | open     | Maybe         |
| 9.1   | Home Assistant Custom-Integration                | Ecosystem | ★★☆  | L      | 🟢     | open     | Maybe         |
| 9.2   | Notification-Webhooks (Slack/Discord/Mail)       | Ecosystem | ★☆☆  | M      | 🟢     | open     | No            |
| 10.1  | Sync-History-Tabelle (Audit-Log)                 | Audit     | ★★☆  | S      | 🟢     | **done v0.1.0** | —         |
| 10.2  | Lokale JSON-Backups der Daely-Events             | Audit     | ★☆☆  | S      | 🟢     | open     | Maybe         |

**Lese-Hilfe:** „Go" = klare Empfehlung, „Maybe" = lohnt Diskussion, „No" =
nicht empfohlen aus dargelegten Gründen.

---

## 1 — Sync-Engine & Reliability

### 1.1 Echtzeit-Sync via Daely-SSE (`realtime`-Service)

**Was.** Daely betreibt laut Dart-AOT-Disassembly einen `realtime`-Service
(SSE über `daely-connect.com/realtime/...`), der Events bei Änderungen
pusht. Die Bridge könnte so Events innerhalb von Sekunden statt 15 Minuten
spiegeln.

**Wie.** Persistente HTTP-Connection mit `text/event-stream`-Header,
JWT-Auth-Header. Bei jedem Push-Event ein Targeted-Sync triggern (statt
voller Cycle). Polling als Fallback bleibt.

**Risiken / Caveats.**
- Schema des `realtime`-Streams ist nicht reversed — würde neuen Probe-
  Aufwand bedeuten (~1–2h dedizierte Live-Read-Session mit Freigabe).
- Re-Connect-Logik (idle timeouts, refresh-token-Rotation während offener
  Stream) ist nicht-trivial. Robustheit braucht eigene Tests.
- Reduziert Daely-Backend-Last — gute Bürger-Eigenschaft.

**Wert/Aufwand.** Niedrigere Sync-Latenz wäre nett, aber 15 min sind für
einen Familienkalender meistens ausreichend. **Wert ★★☆, Komplexität L.**

**Empfehlung.** *Maybe.* Erst nach §1.3 + §2.1 angehen. Wenn Daely irgendwann
das Polling-Volumen drosselt, wird's plötzlich relevant.

---

### 1.2 Retry-Loop für `failed=true`-Mappings

**Was.** Heute: wenn ein Insert/Patch gegen Google fehlschlägt (Rate-Limit,
Netzwerk-Hick-up), landet das in `report.errors` und der Sync geht weiter.
Beim nächsten Cycle wird derselbe Event nur erneut versucht, wenn Daely ihn
zwischenzeitlich aktualisiert hat. Dauerhaft fehlerhafte Events bleiben
unsynchronisiert.

**Wie.** Im `event_mapping`-Schema ein `failed`-Bool + `last_error`-Text +
`retry_after`-Datetime einführen. Bei jedem Sync vorab failed-rows holen,
nochmal versuchen, bei erneutem Fehler exponentiellen Backoff bis Cap.

**Risiken.** Keine — alles lokal, idempotent.

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~3h inkl. Tests). Schema-Migration
nötig (siehe §1.3 als Voraussetzung).

**Empfehlung.** **Go**, direkt nach §1.3. Macht den 24/7-Betrieb
selbstheilender ohne dass User händisch eingreifen muss.

---

### 1.3 Schema-Migrationen für SQLite

**Was.** Aktuell: jede Schema-Änderung erfordert vom User entweder
`DELETE FROM event_mapping;` (volle Resync) oder manuelles ALTER TABLE.
Kein versionierter Migrations-Pfad.

**Wie.** Klein halten: einfache `schema_version`-Tabelle, hardcoded-
Migrations-Liste in `store.py`, beim Startup auto-applizieren wenn `version`
< code-version. Keine Down-Migrations (forward-only). Pattern wie Django/
Alembic-light, ohne externe Dependency.

**Risiken.** Migrations-Bug könnte `bridge.db` korrumpieren. Mitigation:
beim Startup vor jeder Migration ein `bridge.db.bak.<version>` schreiben.

**Wert/Aufwand.** **Wert ★★★, Komplexität S** (~4h). Ist die unsichtbare
Voraussetzung für jede weitere Schema-Änderung (1.2, 10.1) und nimmt dem
User zukünftiges Operations-Risiko ab.

**Empfehlung.** **Go (next),** noch vor 1.2 und 10.1.

---

### 1.4 Health-Check-HTTP-Endpoint

**Was.** Ein winziger HTTP-Server (`/healthz`, `/readyz`) im Bridge-Prozess.
Liefert bei Erreichbarkeit „letzter erfolgreicher Sync vor X Sekunden",
Daely-Auth-Status, Google-Auth-Status. Docker-`HEALTHCHECK`-Direktive
+ Uptime-Monitoring (Uptime-Kuma, Healthchecks.io).

**Wie.** `aiohttp`/`uvicorn`/Stdlib `http.server` in eigenem Thread parallel
zur Sync-Loop. Read-only, kein Auth (lokal-only-Binding).

**Risiken.** Wenn der Endpoint nach außen geöffnet würde, leakt er
Sync-Status — daher per Default Bind auf `127.0.0.1`, dokumentieren.

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~3h). Macht den Container
docker-„nativ" beobachtbar und gibt dem User ein einfaches „lebt sie
noch?".

**Empfehlung.** **Go.** Klein, sicher, sofort nützlich.

---

### 1.5 Prometheus-Metrics-Exporter

**Was.** `/metrics` mit Counters (`events_inserted_total`, `events_patched_total`,
`sync_errors_total`), Histogrammen (`sync_duration_seconds`), Gauges
(`last_sync_age_seconds`).

**Wie.** `prometheus_client`-Lib. Wenn 1.4 vorhanden, gleicher Mini-Server.

**Wert/Aufwand.** Nur sinnvoll für User mit existierendem Prom-Stack.
**Wert ★☆☆, Komplexität M** (~1 Tag inkl. Doku).

**Empfehlung.** *Maybe* — als Add-On für Power-User. Würde ich erst bauen,
wenn jemand explizit fragt.

---

### 1.6 Multi-Account-Setup (mehrere Daely-Konten)

**Was.** Eine Bridge-Instanz spiegelt Kalender mehrerer Daely-Accounts (z. B.
Eltern + Großeltern in zwei separate Google-Konten).

**Wie.** `accounts: [...]`-Liste in der Config; `bridge.db` bekommt
`account_id`-Spalte überall; Sync-Loop iteriert über Accounts. Bootstrap
muss pro Account separat durchlaufen, mit eigenem OAuth-Flow.

**Risiken.** Schema-Refactor ist invasiv. Token-Storage muss disambiguieren.
OAuth-Bootstrap-Flow wird ungemütlich.

**Wert/Aufwand.** **Wert ★☆☆** (du hast genau ein Daely-Konto), **Komplexität L**.

**Empfehlung.** *No (yet)* — erst bauen wenn jemand konkret danach fragt.
Dokumentieren als „möglich, aber nicht aktiv". Pragmatischere Alternative:
zweite Container-Instanz mit eigenem Bind-Mount-Volume.

---

### 1.7 Quota-aware Backoff für Google

**Was.** Google Calendar API hat Quotas (read 1M Queries/Tag, write 100k).
Bei `403 rateLimitExceeded` aktuell: regulärer Backoff. Bei `403 userRateLimitExceeded`
(per-User) und `429`: exponentiell + Jitter ist State-of-the-Art.

**Wie.** `google_client.py` braucht ein wrappendes Retry-Decorator nach
Google's Best-Practice. `tenacity`-Lib oder hand-rolled.

**Wert/Aufwand.** Aktuelle Last ist weit unter Quota; nur theoretisches Risiko.
**Wert ★☆☆, Komplexität S.**

**Empfehlung.** *Maybe*, bei Quota-Anschlägen reaktiv nachrüsten.

---

## 2 — Authentifizierung & Security

### 2.1 Device-Code-Flow statt ROPC (MFA-Support) — *obsolet*

> **Stand 2026-05-08: kein Handlungsbedarf.** Daely bietet User-seitig
> aktuell keine MFA-Aktivierung an, ROPC läuft stabil. Die Sektion bleibt
> als Referenz, falls Daely das je ändern sollte.

**Was.** Die Bridge nutzt heute Resource-Owner-Password-Credentials gegen
Keycloak. Würde Daely je MFA aktivieren, würde ROPC mit `invalid_grant`
scheitern.

**Wie.** Keycloak unterstützt OIDC Device-Code-Flow (`device_authorization_endpoint`
in der OIDC-Discovery-Response). Bootstrap-Flow:
1. Bridge fragt Device-Code an, zeigt User-Code + Verification-URL an
2. User öffnet URL im Browser, gibt User-Code ein, autorisiert (inkl. MFA)
3. Bridge polled `/token` mit `grant_type=urn:ietf:params:oauth:grant-type:device_code`
   bis Erfolg

Refresh-Token-Flow danach unverändert. Keycloak-Realm muss Device-Flow
aktiviert haben — das müsste vorab per OIDC-Discovery geprüft werden (das
ist erlaubter freigabe-freier Call, siehe Memory).

**Risiken.** OIDC-Discovery zeigt schnell, ob `device_authorization_endpoint`
verfügbar ist. Falls Daely's Realm es nicht aktiviert hat, dann ist's nicht
machbar — Plan-B wäre dann ein Frida-Hook in der echten App, was außerhalb
unserer Self-Stop-Kriterien liegt.

**Wert/Aufwand.** Bei aktivem MFA-Zwang wäre **Wert ★★★**, sonst Wert ☆☆☆.
**Komplexität M** (~1.5 Tage inkl. Bootstrap-Refactor).

**Empfehlung.** *No, solange Daely kein MFA anbietet.* Falls sich das ändert:
sobald die OIDC-Discovery-Probe bestätigt, dass Device-Flow am Realm aktiv
ist (1 Curl-Call, 0 Risiko), umsetzen.

---

### 2.2 Token-Encryption-at-Rest in SQLite

**Was.** Daely-Refresh-Token + Google-Refresh-Token liegen heute Klartext
im `bridge.db`. Wer Lesezugriff auf das File hat, kann beide Konten
übernehmen.

**Wie.** `cryptography`-Lib (Fernet); Encryption-Key aus ENV-Variable oder
Keyring; bei Container-Restart aus ENV laden. Alternative: `sqlcipher`
(verschlüsselte SQLite), aber Drop-In-Ersatz mit C-Dependency.

**Risiken.** Wenn der Key verloren geht, ist die DB unbrauchbar — User muss
neu bootstrappen. Akzeptabel solange dokumentiert.

**Wert/Aufwand.** Der Bind-Mount-Pfad `./data/bridge.db` ist auf einer
VM unter Kontrolle des Users, nicht Multi-Tenant. Realistisches Threat-Model
ist niedrig. **Wert ★☆☆, Komplexität M.**

**Empfehlung.** *No.* Filesystem-Permissions auf `data/` reichen für den
aktuellen Threat-Context (Single-User, eigene VM). Im README dokumentieren,
nicht code-seitig lösen.

---

## 3 — Content-Mapping (Daely → Google)

### 3.1 Recurring-Instance-Overrides (Modified Instances)

**Was.** Daely expandiert Recurring-Events server-seitig in einzelne
Instanzen (siehe `findings/05_EVENT_MODEL.md`). Wenn der User in der
Daely-App **eine einzelne Instanz** modifiziert (z. B. Verschiebung um
30 min), kommt sie als Instanz mit abweichendem `start.dateTime` und
`updated`-Timestamp, aber gleichem `recurringId`. Die Bridge dropt sie
heute zugunsten der Master-Instanz (per Dedup-Logik).

**Wie.** Auf Google-Seite gibt's `RECURRENCE-ID` (Modified Instances) — exact
das gleiche Modell wie iCal. Die Bridge müsste:
1. Master + RRULE wie heute schreiben
2. Pro abweichende Instanz ein zusätzliches Event mit `recurringEventId`
   (Google) und `originalStartTime` schreiben
3. Im `event_mapping`-Store separate Rows pro Override-Instanz halten

**Risiken.** Komplexes Diff-Handling (was, wenn der User in Daely eine
Override revoked?). Tests fixture-intensiv.

**Wert/Aufwand.** Selten, aber wenn's fehlt, ist's ein „Bridge sync ist
falsch"-Bug. **Wert ★★☆, Komplexität L** (~5 Tage inkl. Live-Read-Session
für override-Instances + Mapper-Refactor + Test-Fixtures).

**Empfehlung.** *Maybe.* Erst, wenn der User es als Pain-Point meldet
(z. B. „Termin X ist letzte Woche ausgefallen, in Google steht er noch").
Vorher ist's spekulativ.

---

### 3.2 Reicheres Reminder-Mapping

**Was.** Heute: Daely's `reminders: [int minutes]` → Google `popup`-Reminders.
Google unterstützt zusätzlich `email`-Methode.

**Wie.** Config-Toggle `reminder_method: popup | email | both`. Triviale
Logik-Änderung im Mapper.

**Wert/Aufwand.** **Wert ★☆☆** (Popup reicht für den Use-Case), **Komplexität S**.

**Empfehlung.** *Maybe.* Niedrig-hängend, aber kein Pain-Point. Mit-erledigen
falls man eh in `mapper.py` rumfasst.

---

### 3.3 Daely-Participants → Google-Attendees

**Was.** Statt nur Profil-Namen im Footer könnte die Bridge die Daely-Profile
(die `userId` haben — also „echte" Daely-User mit Login-Mail) in Google's
`attendees: [{email}]`-Feld schreiben. Folge: Google verschickt Einladungen,
zeigt RSVP-Status, taucht in den Calendars der Eingeladenen auf.

**Wie.** `Profile.userId` → über `/api/users/<userId>` (Endpoint nicht
verifiziert) Mail holen → in `attendees` schreiben.

**Risiken.**
- Schickt Calendar-Invitations an die Familienmitglieder. Die kriegen plötzlich
  Mails, sehen Einladungen, das Verhalten ändert sich invasiv.
- Daely-Profile ohne `userId` (Kinder ohne eigenen Login) müssten gefiltert
  werden — sonst Bridge-Fehler.
- Erfordert neuen Daely-Endpoint-Probe (`/api/users/<id>` ist nicht
  bestätigt).

**Wert/Aufwand.** **Wert ★☆☆**: dein eigener Google-Calendar zeigt die
Events ohnehin schon, der Footer macht die Beteiligten klar — Attendees
würden das nur duplizieren mit invasivem Side-Effect. **Komplexität M.**

**Empfehlung.** **No.** Hoher Side-Effect-Aufwand, niedriger UX-Win.

---

### 3.4 Location-Geocoding für Maps-Links

**Was.** Daely-`location` ist freier String („Sportplatz Bonn-Beuel"). Google
zeigt das als Text — Klick öffnet Google Maps mit Free-Text-Search. Wenn
die Bridge geocoden würde, wäre der Link präziser (Place-ID).

**Wie.** Google Places-API-Call pro neuem Event (cached pro Location-String).
Schreibt `location` als formatierte Adresse oder Place-ID-URL.

**Risiken.** Quota auf Places-API, Cost (~$5/1000 Calls). Cache-Invalidation.
Datenschutz: jeder Location-String wird an Google gesendet (passiert beim
Daraufklicken eh, aber expliziter Pre-Send ist anders einzuordnen).

**Wert/Aufwand.** Praktisch null Pain-Point heute. **Wert ★☆☆, Komplexität M.**

**Empfehlung.** **No.** Klassisches Over-Engineering.

---

### 3.5 Privacy-Modus (Title/Location maskieren)

**Was.** Pro Daely-Profil oder pro Calendar-Type ein Toggle: „Title in Google
nur als 'Privater Termin' anzeigen, Location leer". Nützlich falls der User
einen Calendar weit teilt (Familie sieht „Privater Termin" statt „Therapie
14:00").

**Wie.** Config-Sektion `privacy.profile_redact: [uuid, ...]` und
`privacy.event_redact_when_private: true`. Mapper checkt vor Schreiben,
ersetzt `summary` durch Platzhalter-String, dropt `location` und `description`.

**Risiken.** Keine technischen — UX-Risiko: User vergisst dass die Maskierung
aktiv ist und sucht Events, die er „nicht findet".

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~3h). Sinnvoll im Kontext
„Privacy-by-Default" gerade für eine Familien-Bridge.

**Empfehlung.** *Maybe.* Würde ich eher als Doku-Hinweis lösen
(„setzt `visibility=private` direkt in Daely → Google hidet's")
als als zweites Maskierungs-System.

---

### 3.6 `customColorCode` ehren (per-event Override)

**Was.** Aktuell explizit nicht gemappt (siehe Phase-3f-Entscheidung).
Eine Opt-in-Variante: wenn der User in Daely einem Event eine eigene
Farbe gibt, gewinnt das über die Profil-Farbe.

**Wie.** Config-Flag `color_mapping.honour_event_color: false` (default).
Im Mapper: wenn `event.customColorCode` gesetzt UND Flag aktiv, statt
Profil-Auto-Match auf `customColorCode` mappen.

**Risiken.** UX-Inkonsistenz: Manche Events sind Profil-koloriert, andere
abweichend — der User-Calendar wirkt „flackerig". Genau aus dem Grund haben
wir's beim 3f-Roll-out bewusst weggelassen.

**Wert/Aufwand.** **Wert ★☆☆, Komplexität S.**

**Empfehlung.** *Maybe.* Erst implementieren, wenn der User später sagt
„hätte gerne pro-Event-Override". Vorher YAGNI.

---

### 3.7 Konfigurierbare Description-Templates

**Was.** Statt hartkodiertem Footer-Format (`👥 Beteiligt: …`) ein Jinja2-
Template, das der User in der Config überschreiben kann. Beispiele:
- Anderer Footer-Text (`Teilnehmer: …` statt `👥 Beteiligt: …`)
- Zusätzliche Felder (Daely-Calendar-Name, Sync-Timestamp, …)
- Gar kein Footer

**Wie.** `description_template` in Config, Default = aktuelles Format,
Mapper rendert über `jinja2`-Engine mit kontrolliertem Variable-Set.

**Risiken.** Template-Errors auf der Live-Bridge ⇒ broken Sync. Mitigation:
Template-Validation beim Config-Load.

**Wert/Aufwand.** **Wert ★★☆** (i18n + personal taste), **Komplexität M**.

**Empfehlung.** *Maybe.* Niedrig-hängend, aber wieder YAGNI-Risiko —
solange du nichts ändern willst, brauchst du's nicht.

---

### 3.8 i18n / Lokalisierung

**Was.** Alles UI-User-Visible (`👥 Beteiligt: …` im Footer, sync.report-
Strings, Bootstrap-Prompts) per `gettext` lokalisierbar.

**Wert/Aufwand.** **Wert ★☆☆** (User-Base ist DE), **Komplexität S** für
String-Extraction, dann pro Sprache nochmal Aufwand.

**Empfehlung.** **No.** Nicht für eine Single-User-Single-Language-Bridge.

---

## 4 — Photo-Bridge (das ursprüngliche Mission-Ziel)

### 4.1 Foto-Upload via Daely-API

**Was.** Das ursprüngliche Mission-Ziel der Reverse-Engineering-Phase: das
15-Bilder-Limit umgehen, indem die Bridge automatisiert Photos in den Daely-
Frame uploaded — z. B. tägliche Rotation aus einem größeren Pool.

**Was wir wissen** (Stand `findings/03_PHOTO_LIMIT.md`):
- Endpoints für Photo-Upload existieren (POST mit Multipart), aber im RE
  nur statisch identifiziert, nicht live verifiziert
- Limit ist möglicherweise server-seitig — dann ist „Workaround" eher
  „delete-old + upload-new" (LRU-Style), nicht „mehr als 15 gleichzeitig"
- Server-seitig könnte's ein Quota-Limit sein, das die Upload-Calls hart
  ablehnt → dann ist die ganze Idee tot

**Wie** (skizziert):
1. Live-Read-Session: Liste aktueller Photos abfragen, ein Test-Upload mit
   anonymem Bild durchführen, Response prüfen → klärt server- vs.
   client-side
2. Wenn server-seitig hart-limitiert: nur Rotation („delete oldest, upload
   new") möglich. CLI: `bridge photos add <file>`, `bridge photos rotate`,
   `bridge photos sync-folder <path>`
3. Wenn nur client-seitig (App-UI verhindert mehr Uploads): einfach mehr
   gleichzeitig hochladen, falls Server's mitmacht

**Risiken (🔴 HOCH).**
- **Schreib-Calls gegen Daely-Production-Backend** — pro CLAUDE.md Regel 6
  jeweils Session-Freigabe nötig
- Falsche Implementation könnte legitime User-Uploads aus dem Frame löschen
  (DELETE-API ungeprüft)
- 15-Bilder-Limit-Umgehen könnte ToS-Verletzung sein (legitim diskutabel,
  aber Edge-Case)
- Photo-Daten = persönliche Familien-Bilder, hoher Sensibilitätsgrad

**Wert/Aufwand.** **Wert ★★☆** (war ursprüngliches Ziel, aber Widget-Use-Case
hat priorisiert), **Komplexität XL**: 1 Probe-Session + 1–2 Wochen Coding +
intensive Test-Phase mit Bilder-Backup-Strategie.

**Empfehlung.** *Maybe (later).* Wenn der User's irgendwann reaktiviert,
braucht's einen sehr bewussten Plan inkl. Backup-Strategie und Test-Frame
(zweites Tablet, falls möglich). Heute: nicht nötig.

---

### 4.2 Foto-Source-Adapter

**Was.** Wenn 4.1 vorhanden: woher kommen die Bilder? Optionen:
1. **Lokaler Ordner** auf Docker-Host (`./photos/incoming/`) — einfachste
   Variante, User legt Bilder rein, Bridge nimmt sie
2. **Google Photos** über Picker-API — komplex, OAuth-zusätzlich, Photos-API
   ist 2025 read-only-ish geworden
3. **iCloud Shared Album** — kein offizielles API
4. **Immich / PhotoPrism / Synology Photos** — Self-Hosted-Foto-Lösungen
   mit API; nische aber gut machbar

**Empfehlung.** Wenn 4.1 kommt: nur Variante 1 als MVP. Andere als
Plug-in-Architektur dokumentieren, nicht implementieren.

---

## 5 — CLI & Operations

### 5.1 `bridge resync <calendar_id>` als echter Command

**Was.** Aktueller Stand (siehe `cli.py` + Memory): „resync" ist ein Stub.
User muss SQL-Workaround machen (`UPDATE event_mapping SET last_seen_updated = NULL`).
Soll: ein echter Command der das automatisiert, optional per Daely-Calendar-ID
gefiltert.

**Wie.** Im CLI: argparse-Subcommand. Implementation: SQL-Update mit
optionalem WHERE auf `daely_calendar_id`, dann einen `incremental_sync`-Cycle.

**Risiken.** 🟡 mittel — re-patches alle Events (Schreib-Calls gegen Google).
Mit `--dry-run` mitigieren.

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~2h).

**Empfehlung.** **Go.** Es ist die direkte Antwort auf den 3f-Re-Color-
Workflow und entlastet den User vom SQL-Hack.

---

### 5.2 `bridge re-color` Convenience-Command

**Was.** Spezialisierte Variante von 5.1, nur für Color-Reset (statt
all-rows). Schreibt `last_seen_updated=NULL` ohne den User zu zwingen, sich
SQL zu merken.

**Wie.** Wrapper um 5.1 mit `--scope=colors` o. ä.

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~30 min wenn 5.1 schon da).

**Empfehlung.** **Go.** Bündel mit 5.1 in einem Release.

---

### 5.3 `bridge profile list/show` Inspection-Commands

**Was.** Read-only Subcommands für Profile-Inspektion: `bridge profile list`
zeigt UUIDs, Namen, ColorCodes, sortOrder; `bridge profile show <uuid>` zeigt
Details inkl. zugeordneter Calendar-Targets und Events.

**Wert/Aufwand.** **Wert ★☆☆** (Diagnose-Komfort), **Komplexität S** (~2h).

**Empfehlung.** *Maybe.* Bequem im Bug-Hunt-Fall, nicht blockierend.

---

### 5.4 `bridge doctor` Health-Diagnose-Command

**Was.** All-in-One-Health-Check: Daely-Auth ✓/✗, Google-Auth ✓/✗,
Profile-Endpoint ✓/✗, letzter erfolgreicher Sync vor X, DB-Konsistenz
(Mappings ohne Google-Pendant).

**Wie.** Sequenzielle Checks, farbiger Output, nicht-Null-Exit-Code bei
Failure (für Cron/Monitoring).

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~3h).

**Empfehlung.** *Maybe.* Sehr komfortabel, vor allem für künftige
Bug-Reports.

---

### 5.5 Web-UI (Bootstrap + Status-Dashboard)

**Was.** Statt CLI-Bootstrap und SSH-Tunnel-OAuth: Browser-UI auf der
Bridge selbst. Auf der Status-Seite: laufende Sync-Cycles, letzte Fehler,
Profile-Color-Übersicht, Manual-Resync-Button.

**Wie.** FastAPI + Jinja-Templates oder Streamlit. Auth: Reverse-Proxy
mit Basic-Auth oder OAuth-Wall.

**Risiken.** Auth-Modell muss ernsthaft sein, sobald die UI nicht-localhost
gebunden wird. Erweitert die Angriffsfläche signifikant.

**Wert/Aufwand.** **Wert ★★☆, Komplexität XL** (~2 Wochen inkl. Auth +
Doku-Update).

**Empfehlung.** **No (yet).** CLI deckt heute alles ab; Web-UI lohnt erst,
wenn die Bridge mehr User bekommt oder du regelmäßig non-Dev-Touchpoints
brauchst.

---

## 6 — CI/CD & Quality Gates

### 6.1 GitHub Action für `pytest` in CI

**Was.** Aktuell baut die GitHub-Action nur das Image (siehe
`.github/workflows/docker.yml`). Tests laufen nur lokal — bei einem
fehlerhaften Push würde das Image trotzdem gebaut und published.

**Wie.** Zweite GHA-Workflow `tests.yml`, läuft auf jeden PR + push:
- `actions/setup-python@v5`, Python 3.11+
- `pip install -e .[dev]` oder `pip install -r dev-requirements.txt`
- `pytest -q --cov`
- Failure-Gate vor Image-Build (z. B. `docker.yml` reagiert nur auf
  „tests-passed"-Workflow-Run)

**Wert/Aufwand.** **Wert ★★★, Komplexität S** (~2h). Schließt den größten
Quality-Gap im aktuellen Setup.

**Empfehlung.** **Go (now).** Niedrig-hängende Frucht mit hoher Hebelwirkung.

---

### 6.2 Coverage-Gate (Codecov o. ä.)

**Was.** Coverage-Report nach jedem Test-Run, Public-Badge im README,
PR-Comment bei Coverage-Drop.

**Wie.** Codecov-Action oder Coveralls; aktuelle Coverage ~91 % als
Baseline, Threshold sagen wir 85 %.

**Wert/Aufwand.** **Wert ★☆☆** (Coverage ist kein Quality-Garant per se),
**Komplexität S**.

**Empfehlung.** *Maybe.* Nice-to-have, niedrige Prio.

---

### 6.3 Integration-Tests gegen Mock-Daely-Server

**Was.** Heute sind alle Tests offline (respx-Stubs gegen `https://daely-connect.com`
und `https://www.googleapis.com`). Eine zusätzliche Integration-Layer wäre
ein lokaler Mock-Daely-Server (z. B. `wiremock` oder hand-rolled FastAPI),
gegen den die echte Bridge mit echten HTTP-Calls läuft.

**Wert/Aufwand.** **Wert ★★☆** (würde echtes httpx-Verhalten mit-testen,
inkl. TLS, Connection-Pooling), **Komplexität L** (~1 Woche).

**Empfehlung.** *Maybe.* Erst, wenn ein Bug auftaucht, der mit respx-Stubs
nicht reproduzierbar ist.

---

### 6.4 Release-Tagging-Workflow + Changelog

**Was.** `v0.1.0`, `v0.2.0`, … Tags + `CHANGELOG.md`. Image-Tags pro Release
(zusätzlich zu `:latest`). User kann pinnen, falls ein Release Bugs hat.

**Wie.** `release-please` oder hand-rolled. CHANGELOG nach Conventional-Commits-
Style aus den Commit-Messages generierbar.

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~3h).

**Empfehlung.** **Go.** Klein, hilft bei Update-Hygiene und Rollback-
Pfaden (siehe Update-Anleitung im README).

---

## 7 — Reverse-Sync (Google → Daely)

### 7.x Google-Events nach Daely zurückschreiben

**Was bewusst NICHT gemacht wird.** Daely hat **eingebaute** Google-/
CalDAV-Integration. Der User kann Google-Calendars direkt in Daely
mounten — bidirektional. Eine Bridge-implementierte Reverse-Richtung
würde mit Daely's eigenem Sync konkurrieren und Conflict-Resolution-
Logik erzeugen, die nie sauber wird.

Wenn jemand explizit einen reinen-Daely-Native-Use-Case will (Google nur
für Widgets, alles andere in Daely-App): Daely's Native-Integration nutzen
und die Bridge weglassen.

**Empfehlung.** **No** — explizit, dauerhaft, dokumentiert.

---

## 8 — Provider-Erweiterung

### 8.1 Apple Calendar (CalDAV) als Alternativ-Target

**Was.** Statt nur Google: optional ein CalDAV-Target (Apple, Nextcloud,
Mailcow, …).

**Wie.** Neuer `caldav_client.py` mit `caldav`-Pip-Package; Mapping-Layer
analog zu `google_client.py`. Config-Toggle `target: google | caldav |
both`.

**Wert/Aufwand.** **Wert ★☆☆** (du nutzt Google), **Komplexität L**.

**Empfehlung.** **No.** Spezialisierung lohnt sich erst bei Demand.

---

### 8.2 Microsoft Outlook als Alternativ-Target

Ähnliche Logik wie 8.1, mit MS-Graph-API. **Wert ★☆☆, Komplexität L,
Empfehlung No.**

---

### 8.3 Native `.ics`-Export-Endpoint

**Was.** Statt Provider-spezifisch zu pushen: HTTP-Endpoint, der den
Daely-Stand als `.ics`-Datei serviert. Damit kann jeder Calendar-Client
(Apple, MS, Google, Thunderbird, Notion, beliebig) per URL-Subscribe
einbinden.

**Wie.** Bridge-internen HTTP-Server (siehe 1.4), `/calendar.ics` als
Endpoint, generiert aus `bridge.db` oder live aus Daely.

**Vorteile.** Provider-agnostisch. Kein OAuth, keine API-Keys, keine
Quotas. Read-only by design, kein Write-Risiko.

**Nachteile.** Kein Push — Clients pollen den Endpoint (typisch
15–60 min Calendar-Refresh-Intervalle, schlechter als unsere 15-min-
Polling). Kein Profil-Color-Mapping — `.ics` hat zwar `COLOR`-Property,
aber kaum ein Client honoriert das.

**Wert/Aufwand.** **Wert ★★☆** (Provider-Freiheit), **Komplexität M**
(~2 Tage inkl. iCal-Konformität).

**Empfehlung.** *Maybe.* Interessanter Plan-B, falls die Google-API mal
zickt. Auch sympathisch als Public-Alternative für User, die kein
Google nutzen wollen.

---

## 9 — Integration / Ecosystem

### 9.1 Home Assistant Custom-Integration

**Was.** Native HACS-Integration, statt nur indirekt über Google-Calendar-
Integration. Würde Daely-Events direkt als HA-Calendar-Entity bereitstellen,
ohne Google im Loop.

**Wie.** `hacs.json` + `__init__.py` + Pythonscript der die Bridge-Logik
als HA-Component verpackt. Config-Flow per UI.

**Vorteile.** Eliminiert Google als Mittelsmann — wer HA-only läuft, spart
sich den Google-Setup. Calendar-Entity in HA-Automatisierungen direkt
verfügbar (`calendar.daely_family`).

**Nachteile.** Doppelte Codebase-Pflege (Bridge-Standalone + HA-Component).
Außer der User wechselt komplett zur HA-Variante.

**Wert/Aufwand.** **Wert ★★☆** (HA-Native fühlt sich sehr nach „daheim"
an), **Komplexität L** (~1 Woche).

**Empfehlung.** *Maybe.* Erwägen, sobald §8.3 (`.ics`-Export) gebaut ist —
HA hat einen `local_calendar`-Provider, der `.ics`-Files frisst → praktisch
free-of-cost-Integration ohne separate HA-Component.

---

### 9.2 Notification-Webhooks (Slack/Discord/Mail)

**Was.** Bei Sync-Errors oder Auth-Failures: Push an Slack/Discord/Mail.

**Wie.** Webhook-URL in Config; Mapper für Provider-Format.

**Risiken.** Daten-Exfiltration (Event-Inhalte landen in Slack-Logs).
Mitigation: nur Meta-Daten („Sync failed at Y:Z, 3 errors"), nie
Event-Titel.

**Wert/Aufwand.** **Wert ★☆☆** (Logs reichen aktuell), **Komplexität M**.

**Empfehlung.** **No.** Healthchecks.io o. ä. mit Health-Endpoint (1.4)
deckt das günstiger und out-of-the-box ab.

---

## 10 — Audit & Backup

### 10.1 Sync-History-Tabelle (Audit-Log)

**Was.** Jeder Sync-Cycle schreibt eine Row in `sync_history`: timestamp,
duration, inserts, patches, deletes, error-count. Per CLI abrufbar.

**Wie.** Neue Tabelle, Schema-Migration (siehe 1.3 als Voraussetzung).
Aufräumen via TTL (z. B. Rows älter als 90 Tage löschen).

**Wert/Aufwand.** **Wert ★★☆, Komplexität S** (~3h).

**Empfehlung.** **Go,** zusammen mit 1.3.

---

### 10.2 Lokale JSON-Backups der Daely-Events

**Was.** Pro Sync-Cycle (oder täglich) ein JSON-Snapshot der aktuellen
Daely-Events nach `./data/backups/<date>.json.gz`. Damit hat der User
ein Backup unabhängig von Daelys eigener Daten-Verfügbarkeit.

**Wie.** Anonymisierungs-Skript wiederverwenden (`scripts/anonymize_fixtures.py`)
für die committed-zur-Repo-Variante; das echte Backup ist nicht-anonymisiert.

**Wert/Aufwand.** **Wert ★☆☆, Komplexität S.**

**Empfehlung.** *Maybe.* Defensive Maßnahme. Wenn Daely mal Datenverlust
hat, wäre's Gold wert, aber heute ist's nicht akut.

---

## 11 — Was bewusst NICHT gemacht werden sollte

### 11.1 Multi-User-Mandantenfähigkeit (SaaS)

**Warum nicht.** Die Bridge ist als Single-User-Self-Hosted-Tool gebaut
(siehe `findings/06_BRIDGE_ARCHITECTURE.md`). Multi-Mandanten würde die
ganze Auth-, Storage-, Privacy- und Operations-Architektur ändern. Außerdem:
„Wir bieten einen Daely-Bridge-Service an" wäre rechtlich heikel (Daely
ist Drittpartei, Reverse-Engineering-Status ist privat-Use, Hosting wäre
ToS-Grauzone).

**Wenn jemand das will:** Eigenes Repo, eigenes Projekt, von Grund auf
anders strukturiert.

### 11.2 Auto-Conflict-Resolution Daely ↔ Google

Schon erklärt in §7. Single-Source-of-Truth-Prinzip beibehalten.

### 11.3 Modifizierte App auf das echte Tablet schicken

In CLAUDE.md Regel 5 hardcoded. Patched APKs nur im Emulator,
nie produktiv.

### 11.4 Daely-Backend-Endpoints brute-forcen

Regel 3. Punkt.

### 11.5 Eingebaute Daely-Photo-Bridge ohne Backup-Strategie

Foto-Daten sind nicht-reversibel persönlich. Wenn 4.1 je gebaut wird, dann
mit ausführlichen Test-Pfaden auf einem Test-Frame, nie direkt auf dem
echten Familien-Tablet.

---

## 12 — Empfohlene Prio-Reihenfolge

### In v0.1.0 erledigt (2026-05-08)

Robustheit-Plus + Operations-Komfort komplett umgesetzt:

- **§1.2** Retry-Loop, **§1.3** Schema-Migrations, **§1.4** Health-Endpoint
- **§5.1** `bridge resync`, **§5.2** `bridge re-color`,
  **§5.4** `bridge doctor`
- **§6.1** GitHub-Action `pytest`-Gate, **§6.4** Release-Tagging
- **§10.1** Sync-History-Tabelle

### Obsolet

- **§2.1** Device-Code-Flow für MFA — Daely bietet kein MFA an, ROPC läuft
  stabil. Sektion bleibt als Referenz, falls sich das je ändert.

### Langfristig / Maybe-never

Alles andere; in Reihenfolge persönlicher Neugierde, nicht nach UX-Wert:

1. §3.1 Recurring-Instance-Overrides — wenn Pain-Point auftaucht
2. §8.3 `.ics`-Export-Endpoint — wenn HA-Integration relevant wird
3. §5.3 `bridge profile list/show` — Diagnose-Komfort, sehr klein
4. §4.1 + §4.2 Photo-Bridge — wenn das ursprüngliche Mission-Ziel
   wieder hochkommt
5. §1.1 Echtzeit-Sync via SSE — wenn Polling-Latenz konkret stört
6. §6.3 Integration-Tests gegen Mock-Daely-Server — wenn ein Bug
   auftaucht, der mit respx-Stubs nicht reproduzierbar ist
7. §3.7 Konfigurierbare Description-Templates — falls jemand i18n braucht

### Nicht empfohlen

§1.6, §2.1 (obsolet), §2.2, §3.3, §3.4, §3.8, §5.5, §7.x, §8.1, §8.2,
§9.2, §11.x — siehe jeweilige Detail-Sektion.

---

## 13 — Schluss-Bemerkung

Die Bridge ist heute „done enough". Die Liste oben ist *Möglichkeiten*-
Katalog, kein Backlog: nichts davon ist akut nötig, damit der primäre
Use-Case funktioniert.

Wenn ich genau eine Sache als nächstes empfehlen würde, dann **§6.1
(CI-Tests)**: ein 2-Stunden-Job, der dauerhaft Quality-Slip-Risiko
eliminiert. Alles andere kann der Use-Case treiben.

> ⚠️ **Re-Verify-Reminder:** Vor der Implementierung jedes Punkts unter
> §1, §2, §4 nochmal die hard rules in `CLAUDE.md` checken
> (Live-Call-Freigaben, PII-Sweep, Schreib-Calls). Die Empfehlungen oben
> sind statische Bewertungen, keine pre-authorized Aktionen.
