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
  OAuth-2.0-*Desktop-Client*. Lade die `client_secret_*.json` herunter und
  benenne sie in `google_oauth_client.json` um.
- Einen **Linux-Host mit Docker**: VPS, Raspberry Pi, alter Laptop, NAS mit
  Container-Support — alles geht. 24/7-Betrieb empfohlen, sonst verpasst die
  Bridge zwischenzeitliche Daely-Änderungen.
- Einen **SSH-Client** auf deinem lokalen Rechner (für Port-Forwarding beim
  einmaligen Bootstrap).

## Schnellstart

Auf dem Host, der die Bridge laufen lassen soll:

```bash
# 1. Projekt klonen
git clone https://github.com/twobeass/daely-google-bridge.git
cd daely-google-bridge/daely-google-bridge

# 2. Verzeichnisse vorbereiten
mkdir -p data secrets
sudo chown -R 1100:1100 data           # Container läuft als uid 1100
chmod 700 secrets

# 3. Konfig + OAuth-Secrets reinkopieren
cp config.docker.example.yaml config.yaml
${EDITOR:-vi} config.yaml              # daely_email setzen, Rest kann bleiben
cp /pfad/zu/google_oauth_client.json secrets/
chmod 600 secrets/google_oauth_client.json

# 4. Image holen (vorgebaut, kein lokaler Build nötig)
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

```bash
docker compose pull
docker compose up -d
```

Die Bridge holt sich das neueste Image aus
[GitHub Container Registry](https://github.com/twobeass/daely-google-bridge/pkgs/container/daely-google-bridge),
deine `data/`- und `secrets/`-Verzeichnisse bleiben unangetastet.

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
