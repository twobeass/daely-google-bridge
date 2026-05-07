# 00 – APK-Inventur (Phase 0)

## TL;DR
Die Companion-App ist eine **Flutter-App** (Dart, AOT-kompiliert in `libapp.so`, 12 MB). Damit greift die Stop-Bedingung aus CLAUDE.md – Workflow für Phase 1 muss angepasst werden. **Trotzdem** liefert eine reine Strings-Analyse von `libapp.so` schon massive Treffer: API-Base-URL, 8 REST-Endpoints, Keycloak-SSO-Realm, HTTP-Lib (`dio`), und Hinweise auf den Speicherort der Gallery-Limit-Logik. Phase 1 ist damit teilweise vorgezogen.

## Beweise

### App-Metadata
- `aapt dump badging apk/daely.apk`:
  - `package: net.daely.familyplannerapp`
  - `versionName: 1.4.8`, `versionCode: 177244635`
  - `minSdk: 21`, `targetSdk: 35`
  - Permissions: CAMERA, INTERNET, ACCESS_NETWORK_STATE, POST_NOTIFICATIONS, RECEIVE_BOOT_COMPLETED, USE_EXACT_ALARM, VIBRATE, com.android.vending.CHECK_LICENSE
- Deep-Link-Scheme im Manifest: `daelycalendar://verified` (vermutlich OAuth-Callback)
- Keine Sonder-Permissions wie READ_CONTACTS oder Calendar-System-Permissions → App synct keinen lokalen Kalender, alles geht über Backend.

### XAPK-Splits
`apk/daely.xapk` enthält:
| Split | Größe | Inhalt |
|---|---|---|
| `net.daely.familyplannerapp.apk` (= `apk/daely.apk`) | 8.9 MB | Java/Kotlin Glue, Plugins, Manifest, Resources |
| `config.arm64_v8a.apk` | 24 MB | **`libapp.so` (12 MB Dart-AOT) + `libflutter.so` (11 MB Engine)** |
| `config.en.apk`, `config.fr.apk` | klein | Locale-Resources |
| `config.xxhdpi.apk` | 215 KB | Hochauflösende Drawables |

Wichtige Konsequenz: Die eigentliche App-Logik (HTTP-Calls, Token-Handling, Limit-Checks) liegt in `libapp.so`, nicht in der `daely.apk`. jadx auf `daely.apk` zeigt nur Flutter-Glue + Plugin-Code (z. B. ImagePicker, Local-Notifications, Google-Sign-In). Für die Mission ist `libapp.so` der primäre Untersuchungsgegenstand.

### Flutter-Bestätigung
- `config.arm64_v8a.apk` enthält `lib/arm64-v8a/libapp.so` und `lib/arm64-v8a/libflutter.so` → **Standard-Indikator für Flutter-AOT-Build**.
- AndroidManifest enthält `<meta-data android:name="flutterEmbedding" android:value="2"/>`.
- Strings in `libapp.so` enthalten Dart-Package-Pfade wie `package:familyplannerapp/...`, `package:dio/...`, `package:flutter/...`.
- jadx-Output unter `decompiled/jadx/sources/net/daely/familyplannerapp/` enthält praktisch nur `MainActivity.java` und `R.java` – Rest sind Library-Klassen → bestätigt, dass kein nennenswerter App-eigener Java-Code existiert.

### HTTP-Stack (aus `strings libapp.so`)
- **HTTP-Client: `dio`** (vollständige Klassenpfade gefunden: `package:dio/src/dio.dart`, `interceptor.dart`, `multipart_file.dart`, `form_data.dart`, ...).
- Auch eingelinkt: `package:http/...`, `package:http_parser/...`. `dio` ist aber der Haupt-Client.
- `package:cached_network_image/...` für Anzeige von Photos.

### API-Endpoints (Statisch aus `libapp.so`)
Folgende Pfade sind als Strings auffindbar (Base-URL siehe unten):

| Pfad | Vermutete Funktion |
|---|---|
| `/api/users` | User-CRUD |
| `/api/users/register` | Account-Anlage |
| `/api/users/forgot-password` | Passwort-Reset |
| `/api/users/send-verification` | E-Mail-Verifikation |
| `/api/device` | Tablet-Pairing/Setup (Setup-Code-Flow) |
| `/api/mobile-app` | App-Update-Check (siehe `update_check_rest_service.dart`) |
| `/api/external-accounts` | Verknüpfte OAuth-Accounts (Google/MS/Apple) |
| `/api/url-calendars` | **CalDAV-/ICS-URL-Kalender** (Mission-relevant!) |
| `/api/gallery` | **Foto-Endpoint** (Mission-relevant für 15-Limit) |
| `/api/groups` | Familien/Gruppen (für Sharing) |

### Backend-/SSO-Hosts
- API-Base: **`https://daely-connect.com`**
- SSO: **Keycloak**, Realm `daely`, Issuer `https://sso.daely-connect.com/realms/daely`
  - OIDC-Endpoint: `https://sso.daely-connect.com/realms/daely/protocol/openid-connect/...`
  - Keycloak ist offene OSS-Software → Auth-Flow ist Standard (Authorization Code Flow + PKCE oder Resource Owner Password – muss noch verifiziert werden).
- External-Account-OAuth-Provider:
  - Google: Scope `https://www.googleapis.com/auth/calendar`
  - Microsoft: `https://login.microsoftonline.com/common/oauth2/v2.0/authorize`, Scopes `Calendars.ReadWrite`, `User.Read`, `offline_access`
  - Apple: `https://appleid.apple.com/`

### Network Security Config
- **Keine** `res/xml/network_security_config.xml` in `decompiled/apktool/res/xml/` → Default-Policy gilt.
  - Default auf `targetSdk=35`: cleartext blockiert, **User-CAs werden NICHT für App-Traffic vertraut**.
  - Das heißt: für mitmproxy braucht es entweder gerooteten Emulator (User-CA → System-CA pushen), Frida-Bypass, oder eine modifizierte APK mit eigenem `network_security_config.xml`.
- **Kein offensichtliches Cert-Pinning gefunden** in den Strings (kein `okhttp3.CertificatePinner` o. ä.). Aber: Flutter-Apps können Pinning auf Dart-Ebene via `dio` + `BadCertificateCallback` implementieren – muss durch String-Suche in libapp.so noch verifiziert werden (Marker für Phase 1).

### Dart-Package-Layout (aus libapp.so-Strings)
Top-Level-Module der Companion-App:
- `package:familyplannerapp/featured/home/...` (Home-Screen, Setup, Device-Pairing)
- `package:familyplannerapp/featured/login/...` (Login + Init-State)
- `package:familyplannerapp/featured/setup/...`

**Auffällig**: Es gibt einen separaten **`package:common`**, der vermutlich auch auf der Tablet-Firmware läuft (Code-Sharing zwischen Companion-App und Geräte-Software). Enthält die API-Datenmodelle:
- `package:common/models/calendar/calendar.dart`
- `package:common/models/calendar/calendar_event.dart`
- `package:common/models/calendar/sync_token_pair.dart` ← **Sync-Tokens für inkrementelle Updates**
- `package:common/models/calendar/share_type/share_type.dart`
- `package:common/models/calendar/check_calendars_update_response.dart`
- `package:common/models/calendar/requests/create_calendar_request.dart`
- `package:common/models/calendar/requests/update_calendar_request.dart`
- `package:common/models/gallery_image/gallery_config.dart` ← **gallery_config – mutmaßlich der Ort des 15-Bilder-Limits**
- `package:common/models/oauth_exception/oauth_exception.dart`

Der DI-Container nutzt `package:familyplannerapp/injection/injection.dart` (das ist `injectable`/`get_it` – Standard-Pattern).

### Gallery- und Calendar-Hinweise
- Strings wie „All events from all calendars from this account will be removed from your D[aely]" → Endpoint `/api/external-accounts` triggert Cascade-Delete der Events.
- Assets `assets/icons/one-way-sync.svg`, `two-way-sync.svg` → es gibt **One-Way-** und **Two-Way-Sync** für externe Kalender (Mission-relevant: bei Two-Way kann unsere CalDAV-Bridge auch zurückschreiben).
- `SyncTokenPair` als Modell → das Backend macht inkrementellen Sync mit Token-Pärchen (Stand-Token + nächster).

## Interpretation

1. **Phase 1 ist teilweise abgekürzt**: Statt jadx + Retrofit-Annotations zu grep'en, sind die Endpoints direkt aus `libapp.so` extrahierbar. Wir haben Base-URL und Pfade. Was noch fehlt: HTTP-Methoden (GET/POST/PUT/DELETE), Request-Bodies, Auth-Header-Format, Query-Params.
2. **Auth ist Keycloak-Standard**: Damit ist der Auth-Flow gut dokumentierbar ohne weiteres RE. Ein Python-Client kann gegen `sso.daely-connect.com/realms/daely/protocol/openid-connect/token` sprechen – die `.well-known/openid-configuration` (öffentlich, kein API-Call gegen unser Backend) liefert alle Detail-Endpoints (Token, Authorize, JWKS).
3. **Foto-Limit**: Hardcoded `15` ist in den Strings nicht direkt sichtbar. Ein Limit von 15 als Integer-Konstante ist im AOT-Snapshot kein String → Standard-`strings`-Suche reicht nicht. Optionen:
   - Das Limit ist server-seitig in `/api/gallery` → Workaround nur über Architektur-Trick.
   - Das Limit ist in `gallery_config.dart` als Konstante → braucht Dart-AOT-Disassembly (`blutter`, `reFlutter`) oder Frida-Hook.
   - Das Limit ist eine UI-Check-Konstante in `featured/home/...` → wenn UI-only, dann reicht der Test-Client um es zu umgehen (Phase 4).
4. **Calendar-Sync**: `sync_token_pair`, `check_calendars_update_response`, `update_calendar_event_type` deuten auf eine professionelle inkrementelle Sync-API. `/api/url-calendars` ist exakt der Mission-relevante Endpoint, weil ICS/CalDAV-URLs hier vom Server gepullt werden – das ist ein potenzieller Single-Point, an dem das vom Hersteller behauptete Limit „nur 1 Kalender pro Provider" sitzt.

## Confidence
**high** für: Flutter-Build, API-Base-URL, Endpoint-Liste, SSO-Provider, HTTP-Lib (dio), package-Struktur. Direkt aus den binären Strings, eindeutig.

**medium** für: HTTP-Methoden, Auth-Header-Format, Cert-Pinning – noch nicht verifiziert, müssen statisch (deep dive in libapp.so) oder dynamisch (Phase 5) geklärt werden.

**low** für: Ort des 15-Bilder-Limits (client- vs. server-seitig). Reine Hypothesen.

## Offene Punkte / Marker für Phase 1

1. **Welche HTTP-Methode pro Endpoint?** Aus `dio.request(method: ...)` und Aufruf-Strings rauspuzzeln (häufig findet sich „POST"/„DELETE" als String in der Nähe der Pfade). Nächster Schritt: gezielte `strings`-Grep auf libapp.so um die gefundenen Pfade herum (mit Offset-Kontext via `objdump -dC` oder `radare2`).
2. **Auth-Flow**: Public-Discovery `https://sso.daely-connect.com/realms/daely/.well-known/openid-configuration` würde klären, ob ROPC erlaubt ist (würde Login mit user+pass im Test-Client ermöglichen ohne Browser). **ABER**: Das ist ein Live-Call gegen das SSO-Backend → vor Abruf den Nutzer fragen.
3. **Cert-Pinning**: In libapp.so nach `BadCertificateCallback`, `pinning`, `setupCertificatePinning` grep'en.
4. **Foto-Limit-Lokalisierung**: 
   - String-Suche um `gallery_config.dart` herum nach Zahlen-Erwähnungen.
   - Falls nicht findbar: `blutter` (Dart-AOT-Reverser) auf libapp.so → liefert lesbare Klassen-Definitionen inkl. Konstanten. Tool ist nicht installiert, müsste in Phase 5 nachgezogen werden.
5. **Endpoint-Bodies**: `_$XxxFromJson` / `_$XxxToJson`-Klassen mit `freezed`-Generator sind im Snapshot vorhanden. Diese listen alle Felder. Für Phase 1 reicht es, die `*.g.dart`-Strings zu sammeln und daraus die Modelle zu rekonstruieren.

## Phase-0-Status
- [x] APK existiert (8.9 MB base + 32 MB xapk)
- [x] `aapt dump badging` durchgelaufen, Versions-/Permissions-Daten dokumentiert
- [x] XAPK-Format identifiziert und Splits dokumentiert
- [x] Native Lib `libapp.so` extrahiert nach `/tmp/libapp.so`
- [x] Framework identifiziert: **Flutter (Dart-AOT)** – ⚠️ Stop-Kriterium aus CLAUDE.md getriggert
- [x] jadx-Output existiert unter `decompiled/jadx/` (Java-Glue, R.java, Plugin-Code; **kein App-Logik-Code**)
- [x] apktool-Output existiert unter `decompiled/apktool/`
- [x] Network-Security-Config geprüft: keine Datei vorhanden, Default-Policy gilt

## Empfehlung für nächsten Schritt
Phase 1 wie in CLAUDE.md spezifiziert (Retrofit-Grep) ist **nicht anwendbar**. Stattdessen wird ein **Phase 1' für Flutter** vorgeschlagen:

a) **Tiefere Strings-Analyse von libapp.so** mit Offset-Kontext (zerlegen in dio-Request-Builder-Aufrufe). Werkzeug: `radare2` oder `binutils`. Aufwand: 1-2h. Liefert: HTTP-Methoden + Body-Modelle für ~80% der Endpoints.

b) **Dart-AOT-Disassembly** mit [`blutter`](https://github.com/worawit/blutter) (Tool für genau diesen Use-Case, rekonstruiert Klassen + Methoden + Konstanten aus libapp.so). Liefert lesbare Pseudo-Source. Müsste installiert werden. Aufwand: 1h Setup + Analyse. Liefert: vermutlich auch das 15-Limit, falls es im Code steht.

c) **Keycloak `.well-known`** (1 GET gegen `sso.daely-connect.com`, kein Live-Test gegen Backend) für Auth-Flow-Details. Aufwand: 5 Min. Aber Live-Call → braucht User-Freigabe.

d) **Phase 5 (mitmproxy + Frida)** sofort vorziehen – das wäre der direkte Weg, alle Fragen auf einmal zu beantworten, aber genau das war laut CLAUDE.md bisher als Plan-B gedacht.

→ **Vorschlag**: a) + b) statisch durchziehen, dann Entscheidung über Live-Capture. Erwartet wird, dass damit Phase 1, 2 und Auth-Flow zu >90% statisch geklärt sind, ohne einen einzigen Backend-Call abzusetzen.
