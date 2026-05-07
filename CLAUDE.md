# Daely Calendar – Reverse Engineering Project

## Mission

Wir analysieren die Companion-App des **Dæly® Calendar** (15,6" smarter Familienkalender, Hersteller daely-shop.com, Servers in DE). Ziel ist es, die HTTP/HTTPS-API zwischen App und Backend so weit zu verstehen, dass wir einen eigenen Python-Client bauen können, der die Beschränkungen der offiziellen App umgeht (vor allem: 15-Bilder-Limit, Kalender-Sync-Limits).

Der Nutzer ist legitimer Eigentümer der Hardware und legitimer Account-Inhaber. Es geht um eigene Daten und um Interoperabilität, nicht um Angriff auf das Backend.

**App package:** `net.daely.familyplannerapp`
**Companion-App-Entwickler:** Moonlight Studio
**Plattform:** Android (Tablet läuft Android 11)

## Harte Regeln (NICHT verletzen)

1. **Niemals Tests gegen das Production-Backend ohne explizite Freigabe des Nutzers in der jeweiligen Session.** Auch keine "kleinen Probe-Calls". Vor jedem Live-Call: Nutzer fragen.
2. **Keine User-Credentials, Tokens, Auth-Header oder Personal Data committen** – auch nicht in `findings/`. Solche Werte stets durch `<REDACTED>` ersetzen, Originale in `~/.daely-secrets/` außerhalb des Repos.
3. **Kein Brute-Force, kein Account-Enumeration, keine DoS-artigen Tests.** Wenn ein Endpoint Rate Limits zu haben scheint, dokumentieren und stoppen.
4. **Bei Sicherheitslücken im Backend** (Auth-Bypass, IDOR, etc.): NICHT ausnutzen, sondern in `findings/SECURITY_RESPONSIBLE_DISCLOSURE.md` dokumentieren und Nutzer informieren.
5. **Keine modifizierte App auf das echte Tablet schicken.** Patched APKs ausschließlich im Emulator (Stufe 2).

## Verzeichnisstruktur

```
~/projects/daely-re/
├── CLAUDE.md                  # diese Datei
├── apk/                       # Original-APK (vom Nutzer bereitgestellt)
├── decompiled/                # jadx- und apktool-Output
├── findings/                  # alle Erkenntnisse als Markdown
│   ├── 00_OVERVIEW.md
│   ├── 01_ENDPOINTS.md
│   ├── 02_AUTH.md
│   ├── 03_PHOTO_LIMIT.md
│   ├── 04_CALENDAR_SYNC.md
│   └── 99_OPEN_QUESTIONS.md
├── scripts/                   # ausführbare Test- und Analyseskripte
└── client/                    # finaler Python-API-Client
```

## Tooling (bereits installiert)

- `jadx` – Decompiler. Standardaufruf: `jadx -d decompiled/jadx apk/daely.apk`
- `apktool` – Resources/Manifest/Smali. Aufruf: `apktool d -o decompiled/apktool apk/daely.apk`
- `androguard` – Python-API für scriptbares Auswerten
- `mitmproxy` – HTTPS-Capture (für Stufe 2)
- `frida-tools` – Runtime-Hooking (für Stufe 2)
- `ripgrep` (`rg`) – Code-Suche
- Python 3.12, `pip install --user` für Test-Skripte (httpx, pydantic empfohlen)

## Phasen-Plan

### Phase 0: APK-Inventur
- [ ] Existenz prüfen: `ls -lh apk/` – falls leer, Nutzer um Upload bitten und stoppen
- [ ] `aapt dump badging apk/daely.apk | head -30` (Package-Name, Version, Permissions)
- [ ] `androguard analyze` – Übersicht: Activities, Services, verwendete Libs
- [ ] In `findings/00_OVERVIEW.md` dokumentieren: Versionsnummer, MinSDK/TargetSDK, Größe, verwendete Frameworks (Retrofit? OkHttp? Ktor? Volley? Flutter?), Hinweise auf Obfuskation (ProGuard/R8)

### Phase 0a: APK-Format-Check
- [ ] `file apk/*` ausführen
- [ ] Falls XAPK/APKM/APKS: entpacken mit `unzip`, prüfen welche Dateien enthalten sind
- [ ] `base.apk` als `apk/daely.apk` ablegen, splits dokumentieren in `findings/00_OVERVIEW.md`
- [ ] Falls native Libs (`*.so`) in den Splits: in 00_OVERVIEW vermerken (Hinweis auf C++/JNI-Logik)
- [ ] Falls Hauptpaket nicht eindeutig identifizierbar: APKEditor-Merge anstoßen

### Phase 1: Statische API-Extraktion
- [ ] Volle Decompilation mit jadx
- [ ] Suche nach Base-URLs:
  - `rg -n 'https?://[^\s"<>]+' decompiled/jadx | sort -u`
  - `rg -nP 'BASE_URL|baseUrl|API_URL|HOST|ENDPOINT' decompiled/jadx`
- [ ] Retrofit-Interfaces finden:
  - `rg -nP '@(GET|POST|PUT|DELETE|PATCH)\s*\(' decompiled/jadx`
  - `rg -nP '@(Header|Path|Query|Body|Field|Multipart|Part)\s*\(' decompiled/jadx`
- [ ] Auth-Mechanismus erkennen: `rg -niP 'authorization|bearer|jwt|x-api-key|sessionid|cookie|interceptor' decompiled/jadx`
- [ ] Network-Security-Config aus `decompiled/apktool/res/xml/network_security_config.xml` lesen → klärt Cert Pinning
- [ ] Ergebnis: `findings/01_ENDPOINTS.md` als strukturierte Liste (Methode | Pfad | Body-Typ | Auth-Required | Notes)

### Phase 2: Limit-Logik
- [ ] Foto-Upload-Pfad lokalisieren (suche nach `multipart`, `image/jpeg`, `Photo`, `Picture`, `Slideshow`, `Frame`)
- [ ] Auf hardcoded `15` oder `MAX_*` grep'en: `rg -nP '\b15\b|MAX_PHOTOS|MAX_IMAGES|PHOTO_LIMIT|PICTURE_LIMIT' decompiled/jadx`
- [ ] Klären: Limit client-seitig (UI-Check) oder server-seitig (Backend rejected)?
- [ ] Analog für Kalender-Sync: `rg -niP 'caldav|ics|sync|calendar.*account|max.*calendar' decompiled/jadx`
- [ ] Ergebnis: `findings/03_PHOTO_LIMIT.md` und `findings/04_CALENDAR_SYNC.md` mit konkreten Code-Stellen (Datei:Zeile + relevantes Snippet) und Hypothese, wie das Limit getestet werden kann

### Phase 3: Test-Client (nur lesend)
- [ ] In `client/` einen schlanken httpx-basierten Python-Client bauen
- [ ] Auth-Flow nachbilden, **vom Nutzer** seinen echten Refresh-Token oder Login-Credentials aus `~/.daely-secrets/credentials.env` einlesen lassen
- [ ] Erste Calls: nur **lesende** Endpoints (GET /me, GET /calendar, GET /photos)
- [ ] Vor jedem ersten Aufruf eines neuen Endpoints: Nutzer fragen, dann max. 1 Call, dann Ergebnis dokumentieren

### Phase 4: Hypothesen verifizieren
- [ ] Wenn Phase 2 zeigt: Limit ist client-seitig → testweise 16. Foto via Test-Client hochladen → Nutzer fragen, ob das ok ist
- [ ] Wenn server-seitig → in `findings/99_OPEN_QUESTIONS.md` dokumentieren, dass Workaround nur über CalDAV-Bridge / Architektur-Trick geht (siehe Brainstorm in der Chat-History)

### Phase 5 (optional, nur wenn Phase 1 nicht ausreicht)
Setup von budtmo/docker-android + mitmproxy + objection patchapk für Live-Traffic-Capture. **Erst nach explizitem Go vom Nutzer.**

## Output-Format für findings/

Jedes Markdown soll diese Struktur haben:
```
# <Titel>
## TL;DR
1-3 Sätze.
## Beweise
- Datei: zeile-genaue Referenz auf decompiled/...
- Code-Snippet (ggf. gekürzt)
## Interpretation
## Confidence
high | medium | low – mit Begründung warum
## Offene Punkte
```

## Selbst-Stop-Kriterien

Stoppe und frage den Nutzer, wenn:
- Phase 0 ergibt: APK ist mit Flutter/React Native gebaut (dann komplett anderer Workflow nötig)
- ProGuard so aggressiv obfusziert, dass keine URL-Strings findbar sind → Phase 5 vorschlagen
- Eine Phase >2h Tool-Time braucht → Zwischenstand melden
- Etwas widerspricht der Mission (z. B. Backend-Endpoint sieht nach Admin-API aus)
- Du dich beim Reframen einer harten Regel ertappst → Stop, fragen

## Was der Nutzer macht (nicht du)

- APK in `apk/daely.apk` ablegen
- `~/.daely-secrets/credentials.env` mit echten Credentials befüllen (wird nie ins Repo committet)
- Live-Test-Freigaben einzeln erteilen
- Entscheidung über Phase 5

## Aktueller Stand

Phase 0 noch nicht begonnen. Beginne mit `ls -lh apk/`. Wenn leer: warte auf Nutzer.
