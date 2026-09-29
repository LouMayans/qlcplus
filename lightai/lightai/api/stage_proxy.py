"""Reverse proxy for QLC+'s browser 3D stage visualizer (`/stage`), served from the same
lightai port as the booth console, on a different path.

Nothing here talks to QLC+ until a browser actually asks for `/stage` or one of its assets, or
opens the `/qlcplusWS` bridge: the shared httpx client and every upstream WebSocket connection
are created lazily. The console at `/` never touches this module.

Routes registered on the app:
  GET  /stage, /stage.css, /stage-*.js, /stage-*.css, /stage-lib/...   -> proxied 1:1 from QLC+
  GET  /three/...                                                     -> proxied 1:1 from QLC+
  GET  /gobos/...                                                     -> proxied 1:1 from QLC+
  WS   /qlcplusWS                                                     -> bridged 1:1 to QLC+'s own /qlcplusWS

QLC+ is reached with the same URL/TLS/auth rules as `lightai.exec.wsclient.QlcClient`
(`candidate_urls`, `basic_auth_header`, `open_ws`, `tls_context`, `is_loopback`), so a club
instance with --web-cert/--web-key and Basic auth works here exactly as it does for the rest
of lightai.

Token: when the server runs with a token (non-loopback --host), the console can attach
`X-LightAI-Token`, but a plain page navigation or a WebSocket handshake cannot set headers, so
both `/stage` and `/qlcplusWS` accept `?token=...` in the query string instead, checked with
`hmac.compare_digest`. Sub-resources (js/css/three/stage-lib/gobos) are plain GETs the browser
fetches itself with no way to add the token either; they are not gated (nothing sensitive is
in them - the model/gobo library, not the show).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import time
from typing import AsyncIterator, Callable, Optional
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect
from websockets.exceptions import ConnectionClosed

from lightai.config import Config
from lightai.exec.wsclient import (
    QlcError,
    basic_auth_header,
    candidate_urls,
    is_loopback,
    load_credentials,
    open_ws,
    tls_context,
)

# Every path this proxy is willing to fetch from QLC+ starts with one of these.
_STAGE_PREFIX = "stage"
_PASSTHROUGH_PREFIXES = ("three/", "gobos/")
# Every browser requests this for any page, whether or not it's linked; QLC+ has one (it's one
# of its own common web files) so proxy it too, rather than let a plain page load show a 404 in
# the console for something the stage page itself never asked for.
_PASSTHROUGH_EXACT = ("favicon.ico",)

# Response headers copied through from QLC+'s reply; everything else (framing headers like
# Transfer-Encoding, Connection) is dropped so Starlette can re-frame the body itself.
_COPY_HEADERS = ("content-type", "cache-control", "etag", "last-modified", "expires")

_UNREACHABLE_HTML = """<!doctype html><meta charset="utf-8"><title>QLC+ unreachable</title>
<body style="font:15px system-ui,sans-serif;padding:48px;color:#333;max-width:640px">
<h1 style="margin-top:0">QLC+ isn't running on {host}:{port}</h1>
<p>The 3D stage view is served by QLC+ itself; lightai only proxies it. Start QLC+ (with the
3D stage fork build) on that host/port and reload this page.</p></body>"""

_NO_STAGE_HTML = """<!doctype html><meta charset="utf-8"><title>No 3D stage</title>
<body style="font:15px system-ui,sans-serif;padding:48px;color:#333;max-width:640px">
<h1 style="margin-top:0">This QLC+ build has no 3D stage</h1>
<p>QLC+ answered, but it doesn't know a <code>/stage</code> route. Install the fork build that
includes the 3D stage visualizer.</p></body>"""


def _verify_for(host: str, tls_server_name: Optional[str]):
    """httpx's `verify=`: False on loopback with no pinned name (the traffic never leaves the
    machine), otherwise a real TLS context - identical rule to QlcClient's own connections."""
    if tls_server_name is None and is_loopback(host):
        return False
    return tls_context(host, tls_server_name)


def _display_host_port(cfg: Config) -> tuple[str, int]:
    """The host:port to show the operator in a "QLC+ unreachable" message - the one actually
    being dialed, which is cfg.qlc_url's when that's set (not the qlc_host/qlc_port defaults)."""
    if cfg.qlc_url and cfg.qlc_url != "auto":
        u = urlparse(cfg.qlc_url)
        return u.hostname or cfg.qlc_host, u.port or cfg.qlc_port
    return cfg.qlc_host, cfg.qlc_port


def _http_bases(url: str, host: str, port: int) -> list[str]:
    """https/http equivalents of `candidate_urls`, secure scheme first."""
    if url and url != "auto":
        u = urlparse(url)
        scheme = "https" if u.scheme == "wss" else "http"
        return [f"{scheme}://{u.hostname}:{u.port}"]
    return [f"https://{host}:{port}", f"http://{host}:{port}"]


async def _body_iter(resp: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk
    finally:
        await resp.aclose()


class StageProxy:
    """Lazy HTTP + WebSocket bridge from lightai's port to QLC+'s /stage. Holds one shared
    httpx.AsyncClient (created on first fetch) and remembers which scheme (https/http) answered
    last, for 30s, so a hot page doesn't re-probe both schemes on every asset request."""

    def __init__(self, cfg_getter: Callable[[], Config]) -> None:
        self._cfg_getter = cfg_getter
        self._client: Optional[httpx.AsyncClient] = None
        self._base: Optional[str] = None
        self._base_until = 0.0

    def _cfg(self) -> Config:
        return self._cfg_getter()

    def _client_for(self, cfg: Config) -> httpx.AsyncClient:
        if self._client is None:
            host = cfg.qlc_host
            if cfg.qlc_url and cfg.qlc_url != "auto":
                host = urlparse(cfg.qlc_url).hostname or host
            # "auto" tries https then http (like QlcClient); a wrong scheme against a plain HTTP
            # server otherwise hangs for the full read timeout doing a TLS handshake it can never
            # finish. A short connect timeout caps that at ~3s; once a base is cached (30s, see
            # _bases) later requests skip the probe and get the full read timeout for big files.
            timeout = httpx.Timeout(connect=3.0, read=30.0, write=30.0, pool=30.0)
            self._client = httpx.AsyncClient(verify=_verify_for(host, cfg.tls_server_name), timeout=timeout, follow_redirects=False)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _bases(self, cfg: Config) -> list[str]:
        if self._base and time.monotonic() < self._base_until:
            return [self._base]
        return _http_bases(cfg.qlc_url, cfg.qlc_host, cfg.qlc_port)

    async def fetch(self, path: str, query: str) -> httpx.Response:
        """GET `path` (e.g. "/stage.css") from QLC+, streaming the reply (stream=True: the
        caller must read/close it, which `_body_iter` does)."""
        cfg = self._cfg()
        headers = basic_auth_header(*load_credentials(cfg))
        client = self._client_for(cfg)
        errors: list[str] = []
        for base in self._bases(cfg):
            url = base + path + (f"?{query}" if query else "")
            try:
                req = client.build_request("GET", url, headers=headers)
                resp = await client.send(req, stream=True)
            except httpx.TransportError as exc:
                errors.append(f"{base}: {type(exc).__name__}: {exc}")
                continue
            self._base, self._base_until = base, time.monotonic() + 30.0
            return resp
        raise QlcError("; ".join(errors))

    async def open_upstream(self):
        """One fresh raw WebSocket to QLC+'s /qlcplusWS, for one browser socket to bridge to."""
        cfg = self._cfg()
        auth = basic_auth_header(*load_credentials(cfg))
        errors: list[str] = []
        for url in candidate_urls(cfg.qlc_url, cfg.qlc_host, cfg.qlc_port):
            try:
                return await open_ws(url, cfg.qlc_host, cfg.tls_server_name, auth)
            except Exception as exc:  # noqa: BLE001 - any of these means "try the next candidate"
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                if isinstance(exc, ConnectionRefusedError):
                    break
        raise QlcError("cannot connect to QLC+ web access; " + " | ".join(errors))


def _routable(full_path: str) -> bool:
    # never forward dot-segments/backslashes: the prefix check alone would let "stage/../x" reach any QLC+ path
    if "\\" in full_path or ".." in full_path.split("/"):
        return False
    return full_path.startswith(_STAGE_PREFIX) or full_path.startswith(_PASSTHROUGH_PREFIXES) or full_path in _PASSTHROUGH_EXACT


async def _pump(ws: WebSocket, upstream) -> None:
    """Relay text frames both ways until either side closes; then close the other."""

    async def browser_to_upstream() -> None:
        try:
            while True:
                msg = await ws.receive_text()
                await upstream.send(msg)
        except (WebSocketDisconnect, ConnectionClosed, RuntimeError):
            pass

    async def upstream_to_browser() -> None:
        try:
            async for msg in upstream:
                await ws.send_text(msg if isinstance(msg, str) else bytes(msg).decode("utf-8", "replace"))
        except (ConnectionClosed, RuntimeError):
            pass

    t1 = asyncio.create_task(browser_to_upstream())
    t2 = asyncio.create_task(upstream_to_browser())
    try:
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (t1, t2):
            t.cancel()
        with contextlib.suppress(Exception):
            await upstream.close()
        with contextlib.suppress(Exception):
            await ws.close()


def add_stage_routes(app: FastAPI, ai_getter: Callable[[], object], token: Optional[str]) -> StageProxy:
    """Registers the proxy routes. Call this LAST in create_app, after every other route, so an
    already-named lightai route (e.g. a future `/stage*`-shaped one) always wins over the
    catch-all below it."""

    proxy = StageProxy(lambda: ai_getter().cfg)  # type: ignore[attr-defined]

    def token_ok(supplied: Optional[str]) -> bool:
        return not token or hmac.compare_digest(supplied or "", token)

    async def proxy_get(full_path: str, request: Request) -> Response:
        if full_path == _STAGE_PREFIX and not token_ok(request.query_params.get("token")):
            return HTMLResponse("<h1>401</h1><p>missing or wrong ?token=</p>", status_code=401)
        query = urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "token"])
        try:
            resp = await proxy.fetch("/" + full_path, query)
        except QlcError:
            host, port = _display_host_port(ai_getter().cfg)  # type: ignore[attr-defined]
            return HTMLResponse(_UNREACHABLE_HTML.format(host=host, port=port), status_code=502)
        if resp.status_code == 404 and full_path == _STAGE_PREFIX:
            await resp.aclose()
            return HTMLResponse(_NO_STAGE_HTML, status_code=404)
        headers = {k: v for k, v in resp.headers.items() if k.lower() in _COPY_HEADERS}
        return StreamingResponse(_body_iter(resp), status_code=resp.status_code, headers=headers)

    @app.websocket("/qlcplusWS")
    async def stage_ws(websocket: WebSocket) -> None:
        if not token_ok(websocket.query_params.get("token")):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        try:
            upstream = await proxy.open_upstream()
        except QlcError:
            await websocket.close(code=1013)  # "try again later"
            return
        await _pump(websocket, upstream)

    @app.get("/{full_path:path}")
    async def stage_assets(full_path: str, request: Request) -> Response:
        if not _routable(full_path):
            raise HTTPException(404)
        return await proxy_get(full_path, request)

    return proxy
