# Salesforce LWC ↔ QLC+ Integration Spec

Authoritative contract for controlling QLC+ from a **Salesforce Lightning Web Component (LWC)**
over the internet. The LWC agent should be able to build the whole control surface from this
document **without reading QLC+ source**.

## What this replaces

Previously a **TouchOSC** surface sent **OSC/UDP** to QLC+. A browser cannot send UDP, so OSC is
not usable from an LWC. Instead, the LWC talks to QLC+'s **built-in Web Access WebSocket API**,
which is the receiver *and* the converter — it directly drives the Virtual Console (VC) and
Functions. No cloud relay, no separate program: the bridge is inside QLC+.

## Architecture

```
Salesforce LWC (HTTPS page)
   new WebSocket("wss://<host>:<port>/qlcplusWS")   ← persistent, reused for all messages
        │  wss (TLS)  +  HTTP Basic auth
        ▼
Router port-forward → QLC+ machine
        ▼
QLC+  (launched with TLS Web Access; see prerequisites)
   = WebSocket receiver + command converter + TLS endpoint, in-process
```

## Connection

- **Endpoint:** `wss://<host>:<port>/qlcplusWS`
  - Default port **9999**. Path is exactly **`/qlcplusWS`**.
  - **Must be `wss://`** (TLS). A Salesforce page is served over HTTPS, and browsers block a
    plaintext `ws://` connection from an HTTPS page (mixed content). Plain `ws://` only works
    for same-origin local testing pages, not from Salesforce.
- **Auth:** QLC+ runs with web auth enabled (`-a <passwordfile>`), so it uses **HTTP Basic auth**.
  The browser performs the Basic handshake on the initial HTTP(S) request that upgrades to the
  WebSocket; supply credentials via the standard browser auth flow / a pre-authenticated session.
  A connection without valid credentials is rejected (401) and the socket never opens.
- **Lifecycle:** open **one** WebSocket on component load and **reuse it** for every message.
  Do not open a socket per action — a persistent connection avoids per-message TLS/TCP handshakes
  and is the single biggest latency win. Implement **reconnect with backoff** (e.g. 0.5s → 5s)
  and surface connection state in the UI. Re-request current state on reconnect (see feedback).

## Message protocol — client → QLC+

Messages are **plain text, pipe-delimited** (`|`). Send as WebSocket text frames.

### Virtual Console widgets (the main path) — `<widgetID>|<...>`
The first field is the numeric **widget ID**. Behavior depends on the widget's type in QLC+:

| Widget type | Send | Meaning |
|---|---|---|
| **Button** | `<widgetID>|255` | press (any non-zero value triggers press) |
| **Button** | `<widgetID>|0` | release |
| **Slider / fader** | `<widgetID>|<0-255>` | set fader to value (0–255) |
| **Cue list** | `<widgetID>|PLAY` / `|STOP` / `|PREV` / `|NEXT` | transport |
| **Cue list** | `<widgetID>|STEP|<index>` | jump to step index |
| **Frame** | `<widgetID>|NEXT_PG` / `|PREV_PG` | page navigation |
| **Animation/Matrix** | `<widgetID>|MATRIX_SLIDER_CHANGE|<value>` | matrix slider |
| **Clock** | `<widgetID>|S` (start/pause) / `|R` (reset) | clock control |

> Buttons and sliders are the two you need for "buttons + faders." Send slider moves
> **throttled** (~30–60 msgs/sec max) and **only on change** to keep it smooth without flooding.

### Functions by ID (trigger a scene/chaser/show directly, no VC widget needed)
- Start: `QLC+API|setFunctionStatus|<functionID>|1`
- Stop:  `QLC+API|setFunctionStatus|<functionID>|0`
- Query: `QLC+API|getFunctionStatus|<functionID>` → reply `QLC+API|getFunctionStatus|Running|Stopped`

### Simple Desk (direct DMX, optional)
- `CH|<absoluteChannel>|<0-255>` — set one DMX channel (1-based channel number).
- `GM_VALUE|<0-255>` — Grand Master level.

### Discovery (how to learn the IDs)
- `QLC+API|getFunctionsList` → reply `QLC+API|getFunctionsList|<id>|<name>|<id>|<name>|...`
- `QLC+API|getWidgetsList`   → reply `QLC+API|getWidgetsList|<id>|<caption>|<id>|<caption>|...`
- `QLC+API|getWidgetType|<widgetID>` → reply includes the type string (Button/Slider/CueList/...)
- `QLC+API|getWidgetStatus|<widgetID>` → current state of that widget

## Message protocol — QLC+ → client (feedback)

QLC+ pushes state so the LWC can reflect reality (button lit, fader position). Frames are also
pipe-delimited; key ones:

- `<widgetID>|BUTTON|<value>` — value 255=Active, 127=Monitoring, 0=Inactive.
- `<widgetID>|SLIDER|<value>|<displayValue>` — fader moved (by anyone).
- `QLC+API|<command>|<payload...>` — replies to the `QLC+API|...` queries above.
- `ALERT|<message>` — error/notice strings.

Parse by splitting on `|` and switching on field positions. On connect/reconnect, call the
discovery + `getWidgetStatus` commands to sync initial UI state.

## How to obtain widget / function IDs

1. **Live (recommended during dev):** open `https://<host>:<port>` in a browser (the QLC+ web UI),
   open devtools → Network → the `qlcplusWS` WebSocket → Messages, and interact with the VC; the
   frames reveal the widget IDs and value formats.
2. **Programmatic:** send `QLC+API|getWidgetsList` and `QLC+API|getFunctionsList` and parse the
   replies.

The QLC+ operator should build a **Virtual Console layout** mirroring the old TouchOSC controls
(buttons, faders, cue lists), then share the resulting widget/function IDs with the LWC.

## Salesforce-side requirements (LWC agent)

- **CSP Trusted Sites:** Setup → Security → **Trusted URLs**, add `https://<host>` (the QLC+
  domain) with the **`connect-src`** directive enabled. By default LWC blocks all outbound
  WebSocket/fetch; without this the `new WebSocket(...)` is blocked.
- Use **`wss://`** only (never `ws://`) — see Connection.
- Reuse a single persistent socket; throttle fader sends; debounce reconnects.

## Latency expectations

- Dominant cost is the internet round-trip browser → QLC+ (geography-bound), typically
  **~50–200 ms**. Direct-to-QLC+ (no cloud hop) is the lowest-latency design available.
- Persistent WebSocket removes per-message handshake cost (built into this design).
- **Buttons feel snappy. Live faders are usable but slightly laggy** vs same-LAN TouchOSC — this
  is inherent to remote-over-internet control and cannot be removed in code. Throttle +
  send-on-change keeps faders smooth.

## Local reference implementation

[`test-ws.html`](test-ws.html) (repo root) is a minimal, **Salesforce-free** page that opens the
same `wss://.../qlcplusWS` socket and sends `widgetID|value` and `QLC+API|...` messages with
buttons and a slider. It is the canonical reference for the LWC's connection + message logic and
the tool used to validate the QLC+ side before the LWC exists.

## Prerequisites owned by the QLC+ side (out of scope for the LWC agent)

These are handled separately (see the project plan / `.claude/memory/`):

1. **TLS Web Access in QLC+** — QLC+ launched with `-w --web-cert <fullchain.pem> --web-key
   <privkey.pem> -a <passwordfile>` so it serves `https`/`wss` on 9999. (Code change in this
   repo's `webaccess/`.)
2. **Certificate** — a browser-trusted cert (recommended: a domain name + Let's Encrypt) so the
   `wss://` connection is accepted from any device.
3. **Router** — port-forward the chosen port to the QLC+ machine; lock it down (web auth + strong
   password, optional source-IP restriction).
