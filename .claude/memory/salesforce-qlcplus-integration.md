---
name: salesforce-qlcplus-integration
description: How Salesforce LWC controls QLC+ over the internet (TLS Web Access WebSocket); the bridge is built INTO QLC+, not a cloud relay.
metadata:
  type: project
---

Goal: drive QLC+ lighting from a **Salesforce LWC** over the internet, replacing a TouchOSC
control surface, with lowest latency. A browser cannot send raw UDP, so **OSC is not usable
from an LWC**.

**Key decision:** the receiver + converter is **built into QLC+** (its existing Web Access
WebSocket API on port 9999, endpoint `/qlcplusWS`, pipe-delimited `widgetID|value` /
`QLC+API|...` protocol). No cloud relay and no separate program — guaranteed running whenever
QLC+ runs. The only missing piece was **TLS**: a Salesforce HTTPS page can only open `wss://`,
but QLC+'s embedded server spoke plaintext only. So we **added HTTPS/WSS support to QLC+'s
Web Access** (`webaccess/src/qhttpserver/` socket layer → `QSslSocket`; `QWebSocketServer`
SecureMode; new `--web-cert` / `--web-key` args plumbed via `main/main.cpp` → `WebAccess` →
`WebAccessBase`). Backward compatible: no cert/key → plaintext HTTP as before.

**Why:** constraints were (1) control surface must be Salesforce, (2) nothing extra may run on
the lighting machine besides QLC+, (3) QLC+ is remote (port-forwarding available), (4) buttons +
live faders.

**Status: IMPLEMENTED + locally verified** (priority-rebuild branch). Full app builds
warning-clean under `-Werror`. Local milestones all passed against `qlcplus.exe -w` on 9999:
plain HTTP+WS round-trip; HTTPS (TLSv1.3) with a self-signed cert whose key is **PKCS#8**
(`BEGIN PRIVATE KEY` — same format Let's Encrypt emits, so real certs load); `wss://` WebSocket
upgrade (101) + API round-trip; with a minimal loaded project: function/widget discovery,
`setFunctionStatus` start (FUNCTION|Running feedback), button press, slider set + readback;
feedback frames flow back. Edge cases pass: invalid cert path and cert-without-key both fall
back to plain HTTP without crashing; with `-wa`, `wss` rejects missing/wrong credentials (401)
and accepts correct ones (101). Cert/key must be an **unencrypted PEM** (loader tries RSA→EC→DSA);
the WebSocket runs in SecureMode automatically when the socket is a QSslSocket.
**Cert auto-reload**: `QHttpServer` watches the cert/key files (QFileSystemWatcher + 2s debounce,
bounded retries) and rebuilds the QSslConfiguration on change, pushing it to `CustomTcpServer` so
NEW connections use the renewed cert **without a restart** (verified live: same PID served two
different certs as files were swapped). So renewals (win-acme PEM store overwriting the files) are
zero-touch — no conversion, no restart.
**Must set `QSslSocket::setPeerVerifyMode(VerifyNone)` on each accepted socket** — Qt's default
server mode (AutoVerifyPeer→QueryPeer) sends a TLS CertificateRequest, which makes browsers
(esp. phones) prompt the user to choose a *client* certificate. VerifyNone stops that prompt.
**Built-in web UI assets hardcoded `ws://`** (`webaccess/res/websocket.js`, `simpledesk.js`,
`simpledesk-v5.js`, `keypad.html`, `Test_Web_API.html`, and inline in `commonjscss.h`) — over an
HTTPS page the browser blocks plaintext `ws://` (mixed content), so the page loads but the
control WebSocket never connects ("must connect to QLC+ WebSocket first"). Fixed to choose
`wss://` when `window.location.protocol === 'https:'`. (Salesforce LWC is unaffected — it dials
`wss://` directly — but the built-in web UI needs this to work remotely.) The res files are
served from disk (`C:\qlcplus\Web\`), so deploying = copy the file; no rebuild.

**Installed + verified standalone at `C:\qlcplus`** (2026-06-19): `ninja -C build-mingw install`,
then Qt runtime filled from the user's good copy and OpenSSL DLLs added (see [[build-procedure]]).
Clean-env launch serves HTTPS 200 + `wss://` TLSv1.3. Test files copied there too:
`C:\qlcplus\{cert.pem,key.pem,minimal.qxw}`. PowerShell 5.1's `Invoke-WebRequest` fails the
self-signed TLS1.3 handshake (old .NET client) — not a server issue; use system `curl -k` or a
browser to test HTTPS.

**How to apply / where things live:**
- Full LWC-facing API contract + the standalone reference page: `SALESFORCE_QLCPLUS_INTEGRATION.md`
  and `test-ws.html` at the repo root.
- Launch for TLS: `qlcplus -w --web-cert fullchain.pem --web-key privkey.pem -a passwd`.
- Cert: domain + Let's Encrypt recommended (browser-trusted from any device); self-signed only
  for pre-trusted fixed devices.
- Build/test per [[build-procedure]] (MSYS2 MinGW64, CMake/Ninja, `-Werror`).
- Sibling feature work pattern: [[priority-system-rebuild]].
