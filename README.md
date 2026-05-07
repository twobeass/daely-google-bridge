# daely-google-bridge

> Spiegelt deinen [Dæly® Calendar](https://daely-shop.com) nach Google
> Calendar — damit Termine, die du auf dem Tablet eingibst, in jedem
> Google-Calendar-Widget, in Home Assistant und überall sonst auftauchen,
> wo du Google Calendar bereits nutzt.

```
   ┌──────────────────┐                     ┌──────────────────┐
   │  Dæly Calendar   │   one-way sync      │ Google Calendar  │
   │  (Familientablet)│   ──────────────►   │ (eigener         │
   └──────────────────┘                     │  Sub-Kalender)   │
       Source of Truth                      └──────────────────┘
                                              ↓        ↓        ↓
                                            Widget  HomeAssist  Phone
```

Die Bridge läuft als kleiner Docker-Container, fragt Daelys API in
konfigurierbarem Intervall ab und schreibt jedes Event in einen Google
Sub-Kalender — inklusive Profil-Footer („👥 Beteiligt: Anna, Bob") damit du
auf einen Blick siehst, wer mitgemeint ist.

## Warum gibt's das?

Wir haben ein Dæly Calendar in der Küche hängen — ein 15,6"-Touchscreen, der
den Kalender der ganzen Familie an einem Ort zeigt. Großartiges Wandgerät,
solide Companion-App. Was uns aber gefehlt hat:

1. **Google-Calendar-Widgets**. Mein Tag fängt mit einem Blick aufs
   Smartphone-Widget an. Die Termine vom Daely-Tablet tauchen da nicht auf,
   weil Daely seinen eigenen Backend-Speicher hat.
2. **Home-Assistant-Integration**. HA hat eine fertige Google-Calendar-
   Integration, mit der man Automationen an Termine hängen kann
   („wenn morgen Schwimmunterricht ist, leg den Schwimmrucksack-Reminder
   um 7:30 in die Sprechblase"). Ohne Daely → Google geht das nicht.
3. **Bestehende Workflows**. Mein Smartphone, mein Auto-Display, mein
   Google-Account-Setup mit anderen Familienmitgliedern — alles spricht
   bereits Google Calendar.

Diese Bridge erledigt genau eine Sache: jedes Event aus deinem **gemeinsamen
Daely-Familienkalender** wird in einen **dedizierten Google-Sub-Kalender**
gespiegelt (per Default „Daely – Family"). Der Sub-Kalender ist dann ganz
normal in Widgets, Home Assistant, Outlook-Sync etc. eingebettet.

Die Synchronisation ist **bewusst nur einseitig**: Daely → Google. Daely
bleibt die Source of Truth. (Den umgekehrten Weg deckt Daely selbst über
seine eingebaute Google-/CalDAV-Integration ab — ist Sache der offiziellen
App, nicht dieser Bridge.)

## Was du brauchst

- Ein **Dæly-Account** (Email + Passwort, **kein MFA** — siehe
  *Einschränkungen* unten).
- Ein **Google-Cloud-Projekt** mit aktivierter Calendar API und einem
  OAuth-2.0-*Desktop-Client*. Wie genau du das anlegst, steht im nächsten
  Abschnitt.
- Einen **Linux-Host mit Docker**: VPS, Raspberry Pi, alter Laptop, NAS mit
  Container-Support — alles geht. 24/7-Betrieb empfohlen, sonst verpasst die
  Bridge zwischenzeitliche Daely-Änderungen.
- Einen **SSH-Client** auf deinem lokalen Rechner (für Port-Forwarding beim
  einmaligen Bootstrap).

## Google Cloud Console: OAuth-Client einrichten

Einmalige Vorarbeit auf <https://console.cloud.google.com/> mit dem
Google-Account, in dessen Kalender die Daely-Events landen sollen.

### 1. Projekt anlegen

- Oben links auf das Projekt-Dropdown klicken → **„Neues Projekt"**
- Name: z. B. `daely-bridge`. Organisation kann leer bleiben.
- Ein paar Sekunden warten, dann oben links das neue Projekt auswählen.

### 2. Calendar API aktivieren

- Im oberen Suchfeld: **„Google Calendar API"** suchen → Treffer öffnen →
  **„Aktivieren"**.

### 3. OAuth-Consent-Screen konfigurieren

- Linkes Menü → **APIs & Dienste** → **OAuth-Consent-Screen**
- **User-Typ: „Extern"** auswählen → **„Erstellen"** (anders geht's bei
  Privat-Accounts nicht; ohne Google-Workspace-Org ist „Intern" nicht
  verfügbar).
- App-Daten:
  - **App-Name:** „Daely Google Bridge" (was auf der Consent-Seite steht)
  - **User-Support-Email:** deine Gmail
  - **Entwickler-Kontaktinfo:** deine Gmail
  - Rest leer lassen, **„Speichern und fortfahren"**.
- **Bereiche / Scopes:**
  - **„Bereiche hinzufügen"** → in der Liste oder per Filter
    `https://www.googleapis.com/auth/calendar` auswählen → **„Update"**.
  - **„Speichern und fortfahren"**.
- **Testnutzer:** deine eigene Gmail-Adresse als Test-User eintragen →
  **„Speichern und fortfahren"**.
- Zusammenfassung → **„Zurück zum Dashboard"**.

#### Empfehlung: Status auf „In Produktion" setzen

Im **Test**-Modus laufen Refresh-Tokens nach **7 Tagen ab** — die Bridge
muss dann re-bootstrapt werden. Für Dauerbetrieb:

- Auf der OAuth-Consent-Screen-Seite oben **„App veröffentlichen"** klicken.
- Du musst den Verifizierungs-Prozess **nicht** durchlaufen, solange du die
  App nicht öffentlich anbietest. Beim nächsten Login zeigt Google einmalig
  eine „App nicht verifiziert"-Warnung — Erweitert → Weiter, fertig.
- Refresh-Tokens halten dann unbefristet (bis du sie aktiv widerrufst).

### 4. OAuth-Client-ID + JSON erzeugen

- Linkes Menü → **APIs & Dienste** → **Anmeldedaten**
- **„Anmeldedaten erstellen"** → **„OAuth-Client-ID"**
- **Anwendungstyp:** unbedingt **„Desktop-App"** (nicht Web-App!)
- Name: `daely-bridge-desktop` o. Ä. → **„Erstellen"**
- Modal mit Client-ID + Secret → **„JSON herunterladen"**
- Datei in `google_oauth_client.json` umbenennen und sicher ablegen — die
  legst du gleich in das `secrets/`-Verzeichnis deines Docker-Setups.

Diese JSON enthält zwar `client_secret`, aber für „Desktop-App"-Clients ist
das laut Google-Doku per Design nicht hochsensibel (PKCE schützt die
Tokens). Trotzdem nicht in ein öffentliches Repo committen.

## Schnellstart

Auf dem Host, der die Bridge laufen lassen soll — **kein `git clone` nötig**,
nur zwei Files vom Repo runterladen:

```bash
# 1. Deploy-Ordner anlegen + Files holen
mkdir bridge && cd bridge
curl -O https://raw.githubusercontent.com/twobeass/daely-google-bridge/main/docker-compose.yml
curl -o config.yaml https://raw.githubusercontent.com/twobeass/daely-google-bridge/main/config.docker.example.yaml

# 2. Verzeichnisse für State + Secrets
mkdir -p data secrets
chmod 700 secrets

# 3. Konfig editieren + OAuth-Secrets reinkopieren
${EDITOR:-vi} config.yaml              # daely_email setzen, Rest kann bleiben
cp /pfad/zu/google_oauth_client.json secrets/
chmod 600 secrets/google_oauth_client.json

# 4. Container-Permissions setzen — uid 1100 muss data/ schreiben,
#    secrets/ lesen, UND config.yaml schreiben können (Bootstrap trägt
#    die Calendar-IDs nach erfolgreicher Authentifizierung dort ein).
sudo chown -R 1100:1100 data secrets
sudo chown 1100:1100 config.yaml

# 4. Image ziehen (vorgebaut auf ghcr.io, multi-arch amd64+arm64)
docker compose pull
```

Dann das **einmalige Bootstrap** — vorher in einem zweiten Terminal von
deinem lokalen Rechner aus den SSH-Tunnel öffnen, damit der OAuth-Redirect
durchkommt:

```bash
# auf deinem lokalen Rechner, separates Terminal:
ssh -L 8080:localhost:8080 user@docker-host
```

Auf dem Host:

```bash
docker compose run --rm --service-ports bootstrap
```

Das Skript:

1. Fragt dein Daely-Passwort ab (kein Echo).
2. Druckt eine Google-Consent-URL — die öffnest du **lokal im Browser**.
3. Nach deiner Zustimmung läuft der Redirect auf `localhost:8080` → durch
   den SSH-Tunnel → in den Container → fertig.
4. Legt einen Google-Sub-Kalender `Daely – Family` an, in den die Bridge
   ab jetzt schreibt.
5. Persistiert die Refresh-Tokens in `data/bridge.db` und schreibt die
   Calendar-ID in `config.yaml` (mit `.bak`-Backup).

Anschließend den **Daemon starten**:

```bash
docker compose up -d
docker compose logs -f bridge | jq -R 'fromjson? // .'
```

Die Bridge macht einen initialen Full-Sync und re-syncen dann alle
15 Minuten (in `config.yaml` einstellbar).

## Port-Konflikt: wenn 8080 auf der VM schon belegt ist

Der Bootstrap braucht für ~30 Sekunden einen lauschenden HTTP-Listener,
damit Googles OAuth-Consent-Flow den Token-Code zurückgeben kann. Default
ist Port **8080**. Wenn der schon von etwas anderem (Tomcat, Jenkins,
Portainer, …) belegt ist, kannst du auf einen freien Port umsteigen
(z. B. **8765**) — dann müssen aber **drei Stellen** zueinander passen:

| Stelle | Was | Wert (Beispiel) |
|---|---|---|
| `config.yaml` | `oauth_local_port:` — was der Bridge-Code dem Listener sagt | `8765` |
| `docker compose run` env | `OAUTH_LOCAL_PORT=` — was Docker als Host-Port veröffentlicht | `8765` |
| SSH-Tunnel auf deinem Rechner | `ssh -L PORT:localhost:PORT …` | `ssh -L 8765:localhost:8765 user@vm` |

Wenn alle drei dieselbe Zahl haben, läuft der Flow genauso wie mit 8080.
Eine Zeile reicht in der Praxis:

```bash
echo "oauth_local_port: 8765" >> config.yaml
OAUTH_LOCAL_PORT=8765 docker compose run --rm --service-ports bootstrap
# auf dem lokalen Rechner: ssh -L 8765:localhost:8765 user@vm-ip
```

### Warum nicht einfach `0.0.0.0`?

Google's OAuth-2.0-Policy verbietet `redirect_uri`-Hosts ungleich
`localhost` oder `127.0.0.1`. Würde die Bridge `redirect_uri=http://0.0.0.0:8765/`
schicken, kommt vom Consent-Screen ein **„Zugriff blockiert:
invalid_request"**. Daher bindet die Bridge im Docker-Container zwar auf
`0.0.0.0` (sonst kommt Docker's Port-Mapping nicht durch), aber der
`redirect_uri`-Host ist immer `localhost` — der SSH-Tunnel + Docker-NAT
löst das beim Callback wieder auf den richtigen Listener auf.

## Was die Bridge tut — und was nicht

✅ Liest den **gemeinsamen Daely-Familienkalender** (alle Events, an denen
   ein oder mehrere Profile teilnehmen) und spiegelt jedes Event nach
   Google.\
✅ Server-seitig expandierte Recurring-Events werden **dedupliziert**: nur
   das Master-Event mit der ursprünglichen `RRULE` landet bei Google,
   Google expandiert dann selbst.\
✅ Profil-UUIDs in `additionalParticipants` werden in Klartext-Namen
   aufgelöst und als **„👥 Beteiligt: …"-Footer** an die Description
   gehängt — damit du im Widget direkt siehst, wer der Termin betrifft.\
✅ Externe Daely-Kalender (Google/Apple/Microsoft, die schon via Daelys
   eigene Integration laufen) werden **übersprungen**, damit keine
   Sync-Loops entstehen.\
✅ **Löschungen** werden propagiert: was in Daely weg ist, fliegt beim
   nächsten Full-Sync auch aus Google raus.\
✅ Idempotent — jeder Sync konvergiert; Wiederholungen sind unkritisch.

❌ **Keine Rückrichtung**: Änderungen im Google-Sub-Kalender werden beim
   nächsten Sync überschrieben. Wenn du Events aus Google nach Daely
   bringen willst, ist das Daelys eingebaute Google-/CalDAV-Integration
   in der offiziellen App.\
❌ **Keine Photo-Sync**. Daelys 15-Bilder-Limit ist ein eigenes Thema und
   nicht Teil dieser Bridge.\
❌ **Kein MFA-Support**. Der Login nutzt ROPC (Resource Owner Password
   Credentials), das funktioniert mit Keycloak nicht bei aktiviertem MFA.
   Workaround: MFA für den Account aus, der die Bridge nutzt.

## Update auf eine neue Version

Im Deploy-Ordner:

```bash
docker compose pull
docker compose up -d
```

Die Bridge holt sich das neueste Image aus
[GitHub Container Registry](https://github.com/twobeass/daely-google-bridge/pkgs/container/daely-google-bridge),
deine `data/`- und `secrets/`-Verzeichnisse bleiben unangetastet.

Falls sich die `docker-compose.yml` mal ändern sollte (selten), einmal:

```bash
curl -O https://raw.githubusercontent.com/twobeass/daely-google-bridge/main/docker-compose.yml
docker compose up -d
```

## Selbst aus dem Source bauen (nur für Contributors)

Wer die Bridge selbst weiterentwickeln will:

```bash
git clone https://github.com/twobeass/daely-google-bridge.git
cd daely-google-bridge

# Image lokal bauen
docker build -t local/daely-google-bridge:dev daely-google-bridge/

# Im Deploy-Ordner stattdessen das lokale Image nutzen:
BRIDGE_IMAGE=local/daely-google-bridge:dev docker compose up -d
```

Die Test-Suite (`pytest -q` aus `daely-google-bridge/`) läuft komplett
offline — Daely und Google sind durchgehend gemockt.

## Repo-Struktur

```
.
├── README.md                 — dieses Dokument
├── LICENSE                   — MIT
├── daely-google-bridge/      — die Bridge selbst (Python-Package + Dockerfile)
│   ├── README.md             — entwicklerorientierte Tech-Doku (englisch)
│   ├── src/, tests/          — Code + 131 Tests, ~91 % Coverage
│   └── docker-compose.yml
├── findings/                 — 9 Markdown-Docs, dokumentieren das Reverse-
│                              Engineering der Daely-API. Nicht nötig zum
│                              Betrieb der Bridge — interessant, falls du
│                              die API selbst erweitern oder ähnliches Tool
│                              schreiben willst.
├── scripts/                  — Live-Read- und Anonymisierungs-Tools, die
│                              die Test-Fixtures erzeugen.
└── tests/fixtures_anonymized/ — anonymisierte Snapshots echter Daely-API-
                                 Antworten; die Test-Suite läuft komplett
                                 offline gegen diese Daten.
```

Tech-Doku zur Bridge:
[`daely-google-bridge/README.md`](daely-google-bridge/README.md) (englisch).
RE-Story:
[`findings/00_OVERVIEW.md`](findings/00_OVERVIEW.md).

## Wie die Bridge entstanden ist

Daelys offizielle App ist eine Flutter-Android-App (Dart-AOT-kompiliert),
also kein üblicher Decompiler-Workflow. Stattdessen:

1. **Statische Analyse** mit [blutter](https://github.com/worawit/blutter)
   gegen `libapp.so` — rekonstruiert Dart-Klassen, Felder, Enum-Werte und
   String-Konstanten. Bringt einen auf ~80 % der API-Form.
2. **Ein Live-Read-Skript** mit explizitem Pro-Call-Approval, das jeden
   Kandidaten-Endpoint genau einmal abfragt und die JSON-Antwort
   archiviert. Schließt die letzten Lücken.
3. **Anonymisierung**: ein eigener Script ersetzt UUIDs, Namen, Emails
   und Sync-Tokens durch deterministische Test-Werte, damit die
   Test-Suite gegen Real-Shape-Daten laufen kann, ohne dass jemals
   echte Daten committed werden.
4. **Bridge** Python-seitig outside-in: erst pure Funktionen (Mapper,
   Modelle), dann ein SQLite-Store, dann HTTP-Clients mit respx- und
   Mock-Tests, dann Sync-Orchestrator, dann OAuth + CLI, dann Docker
   inklusive GitHub-Actions-Pipeline für das Image.

Alle 131 Tests laufen offline. Keine Live-Calls in CI.

## Disclaimer

- Dieses Projekt ist **nicht von daely-shop.com oder Moonlight Studio
  affiliiert**. „Dæly" und die Produkt-Marken gehören ihren jeweiligen
  Eigentümern.
- Die Bridge benutzt nur Hardware und Accounts, die ihrem Betreiber gehören.
  Sie spricht dieselbe öffentliche API wie die offizielle App und nutzt
  keine Sicherheitslücken aus.
- Die Findings unter `findings/` beschreiben die API, wie sie aus einer
  legitim authentifizierten Session sichtbar war — keine Credentials,
  keine Tokens, keine personenbezogenen Daten.
- Wenn du das Tool gegen einen Account einsetzt, der dir nicht gehört, ist
  das deine Sache.
- Bridge ist AS-IS unter [MIT-Lizenz](LICENSE). Bevor du sie auf einen
  produktiv genutzten Google-Kalender los lässt: einmal `bridge run --once`
  laufen lassen und Output kontrollieren.

## Mitmachen

Issues und PRs willkommen. Die Test-Suite (`pytest -q` aus
`daely-google-bridge/`) ist der beste Einstieg — jede Verhaltensänderung hat
einen Test. Bei Netzwerk-Code bitte mocken; Live-Calls in CI sind in diesem
Repo bewusst nicht vorgesehen.

Falls dein Beitrag eine neue Test-Fixture braucht: vorher durch
`scripts/anonymize_fixtures.py` jagen — niemals rohe API-Responses
committen.

## Status

| Phase | Status | Inhalt |
|---|---|---|
| 3a | ✅ | Live-Read & Fixture-Anonymisierung |
| 3b | ✅ | Mapper + Store, pure Logik |
| 3c | ✅ | HTTP-Clients + Bootstrap-CLI |
| 3d | ✅ | Sync-Orchestrator + Scheduler |
| 3e | ✅ | Profil-Footer in Event-Description |
| 3f | ✅ | Dockerfile + Compose + ghcr.io-Publishing |
| nächste | — | `bridge resync <cal_id>` für Force-Re-Push einzelner Kalender |
