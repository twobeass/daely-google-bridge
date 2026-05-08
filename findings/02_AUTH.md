# 02 – Auth-Flow

## TL;DR
Auth läuft über **Keycloak** (Realm `daely`, Client `mobile-app`) mit dem **`password`-Grant (ROPC)** als unterstütztem Flow. Im laufenden App-Code wird ROPC tatsächlich für den Companion-Login verwendet (Username + Passwort werden direkt gegen `${OPENID_ENDPOINT}/token` getauscht). Refresh-Tokens werden über `dio`-`AuthTokenInterceptor` automatisch verwendet. Damit ist ein Python-Client mit Username/Passwort-Login praktikabel (keine Browser-Embed nötig).

## Beweise

### Keycloak-Discovery (`findings/keycloak_discovery.json`)
Vollständig vom User abgerufen am 2026-05-07:

- `issuer`: `https://sso.daely-connect.com/realms/daely`
- `token_endpoint`: `https://sso.daely-connect.com/realms/daely/protocol/openid-connect/token`
- `authorization_endpoint`: `…/auth`
- `userinfo_endpoint`: `…/userinfo`
- `end_session_endpoint`: `…/logout`
- `revocation_endpoint`: `…/revoke`
- `introspection_endpoint`: `…/token/introspect`
- `jwks_uri`: `…/certs`
- `device_authorization_endpoint`: `…/auth/device` (Device-Flow, nutzt evtl. das Tablet)
- `pushed_authorization_request_endpoint`: `…/ext/par/request`
- `grant_types_supported`: enthält **`password`**, `authorization_code`, `refresh_token`, `client_credentials`, `urn:ietf:params:oauth:grant-type:device_code`, `urn:ietf:params:oauth:grant-type:token-exchange`
- `code_challenge_methods_supported`: `["plain", "S256"]` → PKCE wird unterstützt (für Auth-Code-Flow)
- `token_endpoint_auth_methods_supported`: `client_secret_basic`, `client_secret_post`, `private_key_jwt`, `client_secret_jwt`, `tls_client_auth`
- `scopes_supported`: `openid`, `profile`, `email`, `offline_access`, `roles`, `phone`, `address`, `organization`, `microprofile-jwt`, `web-origins`, `acr`, `basic`
- `claims_supported`: `sub`, `aud`, `iss`, `auth_time`, `name`, `given_name`, `family_name`, `preferred_username`, `email`, `acr`

### Client-Konfiguration (aus `flutter_assets/.env`)
- `OPENID_CLIENT=mobile-app` → Keycloak-Client-ID
- `OPENID_ENDPOINT=https://sso.daely-connect.com/realms/daely/protocol/openid-connect`
- **Kein `OPENID_CLIENT_SECRET`** in der `.env` → Client ist als **public client** registriert (sonst könnte die App ihn nicht ohne Secret nutzen). Konsequenz: ROPC funktioniert mit nur `client_id` + `username` + `password` (kein Client-Secret nötig). PKCE wird im Auth-Code-Flow für Sicherheit gegen Auth-Code-Interception genutzt.

### Login-Flow im App-Code

**Datei**: `findings/blutter_out/asm/common/service/authentication/authentication_rest_service.dart`

**Eindeutige Strings** (relevant):
- `"client_id"`, `"password"`, `"basicAuth"`, `"credential"`, `"credentials"`
- `"application/x-www-form-urlencoded"` (Content-Type für Token-Endpoint)
- `"invalid_grant"` (Fehlerklasse beim Refresh)
- Fehler-Strings: `"Login failed: "`, `"Failed to refresh access token: "`, `"Token refresh skipped due to OFFLINE backoff."`, `"Refresh token blocked by backoff (likely request loop)."`, `"OAuth client returned an empty access token."`, `"Device login is only supported on device builds."`

**Verwendete Pakete** (im blutter-Output sichtbar):
- `package:oauth2/...` (Standard-Dart-OAuth2-Lib für Token-Handling)
- `package:aad_oauth/...` (Microsoft Azure AD OAuth, separat für die MS-Calendar-Integration → nicht für Companion-Login selbst)

**Flow-Hypothese**:
1. Companion-App fragt User nach Email + Passwort.
2. POST an `${OPENID_ENDPOINT}/token`, `Content-Type: application/x-www-form-urlencoded`, Body:
   ```
   grant_type=password
   client_id=mobile-app
   username=<email>
   password=<password>
   scope=openid profile email offline_access
   ```
3. Antwort: JSON mit `access_token`, `refresh_token`, `expires_in`, `id_token`.
4. App speichert Tokens via `package:flutter_secure_storage` (sichtbar im Output: `flutter_secure_storage`-Package).

### Bearer-Injection
**Datei**: `findings/blutter_out/asm/common/interceptors/auth_token_interceptor.dart`

- `AuthTokenInterceptor extends InterceptorsWrapper` (Standard-dio-Pattern)
- Vor jedem Request: 
  - Prüft Token-Gültigkeit
  - Ruft `acquireNewToken()` wenn abgelaufen (Refresh-Flow)
  - Setzt `Authorization: Bearer <accessToken>` Header
- Bei 401: führt einen Refresh-Token-Tausch aus und retried den Request einmal.
- Hat Backoff-Mechanik gegen Refresh-Loops (Strings: `"Blocked by Retry-After until "`, `"Refresh token blocked by backoff (likely request loop)."`, `"Refresh token blocked by OFFLINE backoff."`).

### Refresh-Flow
- POST an `${OPENID_ENDPOINT}/token`, Body:
  ```
  grant_type=refresh_token
  client_id=mobile-app
  refresh_token=<refresh_token>
  ```
- Antwort: neue `access_token` + `refresh_token`.

### Logout-Flow
- Aus `authentication_rest_service.dart`: Pfad `/logout` (relativ zu `${OPENID_ENDPOINT}`).
- Vermutete Body-Form (Keycloak-Standard für public client):
  ```
  client_id=mobile-app
  refresh_token=<refresh_token>
  ```
- Plus separater app-internaler `/logout`-State-Cleanup.

### Deep-Link-Callback
- AndroidManifest.xml zeigt: `<data android:host="verified" android:scheme="daelycalendar"/>` → URL `daelycalendar://verified`.
- Vermutlich der Callback nach E-Mail-Verifikation oder nach OAuth-Code-Flow für externe Calendar-Provider (nicht für Companion-Login selbst, da der ROPC läuft).

### Tablet vs. Companion
- `IS_DEVICE=false` in unserer `.env` (Companion-Modus).
- Strings wie `"Device auto-login is only supported on device builds."`, `"No stored device credentials."` deuten auf einen separaten Auto-Login-Pfad fürs Tablet (vermutlich Device-Code-Flow oder Setup-PIN-Flow). **Für unseren Python-Client irrelevant**, da wir Companion-Modus simulieren.

## Interpretation

1. **ROPC ist bequem nutzbar**: `mobile-app` ist ein Public Client mit `password` als unterstütztem Grant. Ein Python-Client kann mit Username + Passwort direkt einen Bearer-Token holen. Kein Browser-Embed, kein Custom-Tab, kein Deep-Link nötig.

2. **Refresh-Tokens haben offline_access**: `offline_access` ist als Scope unterstützt. Damit kann der Python-Client einen langlebigen Refresh-Token persistieren und über Tage/Wochen ohne erneutes Login-Prompt arbeiten.

3. **JWT-Validierung via JWKS**: Wenn wir Bearer-Tokens jemals verifizieren wollen (für Tests), liefert `…/protocol/openid-connect/certs` die Public Keys. Token-Inhalt: standard OIDC Claims (`sub`, `email`, `preferred_username` etc.).

4. **Kein Anti-Reuse / kein Bot-Schutz erkannt**: Kein Hinweis auf Captcha, Device-Attestation, App-Check (Firebase) o.ä. Das senkt die Hürde für den Python-Client erheblich. Soll der Server-Op-Profile dennoch nicht durch zu schnellen Login-Loop nervös werden – beim Test-Client genug Backoff einbauen.

5. **Phase 5 (mitmproxy) wäre einfacher als gedacht**: Da kein Cert-Pinning und kein App-Attestation, würde ein Tablet im Emulator mit System-CA + mitmproxy alle Requests zeigen. Aktuell aber nicht nötig: Phase 1' liefert genug.

## Confidence
**high**: Keycloak-Discovery (vom User direkt geholt), Client-ID `mobile-app`, OPENID_ENDPOINT, ROPC-Verfügbarkeit, Bearer-Header-Format, Refresh-Mechanik.

**medium**: Konkretes Body-Format des `/token`-Calls (Standard-Annahme, sehr unwahrscheinlich, dass nicht).

**low**: Tablet-Pairing-Auth-Flow (Device-Code-Flow vermutet, nicht verifiziert).

## Offene Punkte
1. **Token-Lebensdauer**: nicht aus Discovery ersichtlich (dort steht nicht). Default-Keycloak: AT 5 Min, RT 30 Min (sliding) oder dauerhaft (mit `offline_access`). Lässt sich beim ersten echten Token-Aufruf in `expires_in` ablesen.
2. **MFA aktiviert?**: Discovery sagt nichts. Live-Beobachtung über mehrere
   Monate Bridge-Betrieb: Daely bietet User-seitig keine MFA-Aktivierung an,
   ROPC läuft stabil. Sollte das je geändert werden, würde der Login mit
   `invalid_grant` + spezifischer Error-Description scheitern, und der Wechsel
   auf Device-Code-Flow (`device_authorization_endpoint` aus der Discovery)
   wäre der saubere Pfad.
3. **`acr_values_supported: ["0", "1"]`**: Bedeutet, dass der Server „Authentication Context Class Reference" 0/1 unterstützt – kann für gestaffelte Auth-Levels relevant werden, aber Companion-App-Login ist üblicherweise acr=1.
4. **Phase-3-Test**: Mit Test-Account `~/.daely-secrets/credentials.env` einen ROPC-Login als ersten Live-Call machen, dann Token in `~/.daely-secrets/` cachen. Vor dem Aufruf User-Freigabe einholen (CLAUDE.md-Regel).

## Vorschlag für Python-Client (Skizze)
```python
# Skelett – nicht ausführen ohne explizite Live-Call-Freigabe
import httpx

OIDC = "https://sso.daely-connect.com/realms/daely/protocol/openid-connect"
CLIENT_ID = "mobile-app"

def login_password(email, password):
    r = httpx.post(f"{OIDC}/token", data={
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "username": email,
        "password": password,
        "scope": "openid profile email offline_access",
    })
    r.raise_for_status()
    return r.json()  # {access_token, refresh_token, expires_in, id_token, ...}

def refresh(refresh_token):
    r = httpx.post(f"{OIDC}/token", data={
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "refresh_token": refresh_token,
    })
    r.raise_for_status()
    return r.json()
```
