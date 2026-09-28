"""Persistent WebSocket client for the QLC+ Web Access API (Qt Widgets build).

Wire protocol: text frames, fields separated by "|". Replies carry no request id, so this
client keeps exactly one request in flight and matches the reply by its prefix. Server
pushes (FUNCTION|<fid>|Running|Stopped, LOOP|STATE|..., ALERT|...) go to subscribers.

TLS: the club instance runs with --web-cert/--web-key, so every connection must be wss://.
url="auto" tries wss first and falls back to ws. On loopback without a configured
tls_server_name the certificate is not verified (the traffic never leaves the machine).
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import ssl
import sys
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import urlparse

import websockets
from websockets.exceptions import ConnectionClosed

from lightai.config import Config, load_config

# Commands that block or crash this QLC+ build; never sent.
FORBIDDEN_PREFIXES = ("QLC+CMD|opMode", "QLC+API|getWidgetSubIdList")

# Minimum field counts; QLC+ reads past the end of the list when fields are missing.
MIN_FIELDS = {
    "QLC+API|getFunctionType": 3,
    "QLC+API|getFunctionStatus": 3,
    "QLC+API|setFunctionStatus": 4,
    "QLC+API|getWidgetType": 3,
    "QLC+API|getWidgetFunction": 3,
    "QLC+API|getWidgetStatus": 3,
    "QLC+API|getChannelsValues": 4,
    "QLC+API|sdResetChannel": 3,
    "QLC+API|sdResetUniverse": 3,
    "QLC+API|loadProjectFile": 3,
    "QLC+API|setFunctionSpeed": 6,
    "CH": 3,
    "GM_VALUE": 2,
    "LOOP": 2,
}

Subscriber = Callable[[str], Optional[Awaitable[None]]]


class QlcError(Exception):
    """Protocol misuse, timeout, or closed connection."""


@dataclass
class Latency:
    samples: list = field(default_factory=list)
    keep: int = 5000

    def add(self, ms: float) -> None:
        self.samples.append(ms)
        if len(self.samples) > self.keep:
            del self.samples[: len(self.samples) - self.keep]

    def percentile(self, p: float) -> float:
        if not self.samples:
            return float("nan")
        ordered = sorted(self.samples)
        idx = min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1))))
        return ordered[idx]

    def summary(self) -> dict:
        return {
            "n": len(self.samples),
            "p50_ms": round(self.percentile(50), 3) if self.samples else None,
            "p95_ms": round(self.percentile(95), 3) if self.samples else None,
            "max_ms": round(max(self.samples), 3) if self.samples else None,
        }


def validate_message(msg: str) -> None:
    for bad in FORBIDDEN_PREFIXES:
        if msg.startswith(bad):
            raise QlcError(f"refusing to send {bad!r}: it can block or crash QLC+")
    parts = msg.split("|")
    key = "|".join(parts[:2]) if parts[0] == "QLC+API" else parts[0]
    need = MIN_FIELDS.get(key)
    if need and len(parts) < need:
        raise QlcError(f"{key} needs at least {need} fields, got {len(parts)}: {msg!r}")


def is_loopback(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def tls_context(host: str, server_name: Optional[str]) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if server_name is None and is_loopback(host):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def load_credentials(cfg: Config) -> tuple[Optional[str], Optional[str]]:
    p = cfg.data_dir / "qlc-credentials.json"
    if not p.exists():
        return None, None
    doc = json.loads(p.read_text(encoding="utf-8"))
    return doc.get("username"), doc.get("password")


class QlcClient:
    def __init__(
        self,
        url: str = "auto",
        host: str = "127.0.0.1",
        port: int = 9999,
        username: Optional[str] = None,
        password: Optional[str] = None,
        timeout: float = 0.5,
        tls_server_name: Optional[str] = None,
    ) -> None:
        self.url = url
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.timeout = timeout
        self.tls_server_name = tls_server_name
        self.latency = Latency()
        self.connected_url: Optional[str] = None
        self._ws = None
        self._reader: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()  # parallel commands must not each open (and tear down) a socket
        self._down_until = 0.0  # after a failed connect, fail fast for a few seconds instead of queueing 4 s attempts
        self._down_reason = ""
        self._pending: Optional[tuple[str, asyncio.Future]] = None
        self._subs: list[Subscriber] = []

    @classmethod
    def from_config(cls, cfg: Optional[Config] = None, timeout: float = 0.5) -> "QlcClient":
        cfg = cfg or load_config()
        user, pw = load_credentials(cfg)
        return cls(cfg.qlc_url, cfg.qlc_host, cfg.qlc_port, user, pw, timeout, cfg.tls_server_name)

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._reader is not None and not self._reader.done()

    @property
    def http_base(self) -> str:
        if not self.connected_url:
            raise QlcError("not connected")
        u = urlparse(self.connected_url)
        scheme = "https" if u.scheme == "wss" else "http"
        return f"{scheme}://{u.hostname}:{u.port}"

    def auth_header(self) -> dict[str, str]:
        if self.username is None:
            return {}
        token = base64.b64encode(f"{self.username}:{self.password or ''}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _candidates(self) -> list[str]:
        if self.url and self.url != "auto":
            return [self.url]
        return [
            f"wss://{self.host}:{self.port}/qlcplusWS",
            f"ws://{self.host}:{self.port}/qlcplusWS",
        ]

    async def _open(self, url: str):
        u = urlparse(url)
        kwargs: dict = dict(ping_interval=None, max_size=2**22, open_timeout=3, additional_headers=self.auth_header())
        if u.scheme == "wss":
            kwargs["ssl"] = tls_context(u.hostname or self.host, self.tls_server_name)
            if self.tls_server_name:
                kwargs["server_hostname"] = self.tls_server_name
        return await websockets.connect(url, **kwargs)

    async def ensure_connected(self) -> None:
        """Connect once, even when several commands arrive together (double-checked under a lock)."""
        if self.connected:
            return
        if time.monotonic() < self._down_until:
            raise QlcError(self._down_reason)
        async with self._connect_lock:
            if self.connected:
                return
            if time.monotonic() < self._down_until:
                raise QlcError(self._down_reason)
            try:
                await self._connect()
            except QlcError as exc:
                self._down_until = time.monotonic() + 3.0
                self._down_reason = f"QLC+ not reachable (retrying in a few seconds): {exc}"
                raise
            self._down_until = 0.0

    async def connect(self) -> None:
        async with self._connect_lock:
            await self._connect()

    async def _connect(self) -> None:
        errors = []
        old_ws, old_reader = self._ws, self._reader
        self._ws, self._reader, self.connected_url = None, None, None
        if old_reader is not None:
            old_reader.cancel()
        if old_ws is not None:  # close the previous socket instead of leaking it
            try:
                await old_ws.close()
            except Exception:
                pass
        for url in self._candidates():
            try:
                self._ws = await self._open(url)
                self.connected_url = url
                break
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
                if isinstance(exc, ConnectionRefusedError) or getattr(exc, "winerror", None) in (1225, 10061):
                    break  # nothing listens on that port: trying ws:// after wss:// only doubles the wait
        if self._ws is None:
            raise QlcError("cannot connect to QLC+ web access; " + " | ".join(errors))
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass
        self._ws = None
        self._reader = None

    async def __aenter__(self) -> "QlcClient":
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    def subscribe(self, cb: Subscriber) -> None:
        if cb not in self._subs:  # re-subscribing after an outage must not duplicate every push
            self._subs.append(cb)

    def unsubscribe(self, cb: Subscriber) -> None:
        if cb in self._subs:
            self._subs.remove(cb)

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = raw if isinstance(raw, str) else bytes(raw).decode("utf-8", "replace")
                pending = self._pending
                if pending is not None and not pending[1].done() and msg.startswith(pending[0]):
                    pending[1].set_result(msg)
                    continue
                for cb in list(self._subs):
                    try:
                        result = cb(msg)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        print(f"[wsclient] subscriber error: {exc}", file=sys.stderr)
        except ConnectionClosed:
            pass
        finally:
            pending = self._pending
            if pending is not None and not pending[1].done():
                pending[1].set_exception(QlcError("connection closed"))

    async def send(self, msg: str) -> None:
        validate_message(msg)
        if not self.connected:
            raise QlcError("not connected")
        t0 = time.perf_counter()
        ws = self._ws
        if ws is None:
            raise QlcError("not connected")
        try:
            await ws.send(msg)
        except ConnectionClosed as exc:
            raise QlcError(f"connection to QLC+ closed: {exc}") from exc
        self.latency.add((time.perf_counter() - t0) * 1000.0)

    async def request(self, msg: str, reply_prefix: Optional[str] = None, timeout: Optional[float] = None) -> str:
        validate_message(msg)
        if not self.connected:
            raise QlcError("not connected")
        if reply_prefix is None:
            parts = msg.split("|")
            if parts[0] != "QLC+API" or len(parts) < 2:
                raise QlcError("reply_prefix is required for non-API commands")
            reply_prefix = f"QLC+API|{parts[1]}|"
        async with self._lock:
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending = (reply_prefix, fut)
            t0 = time.perf_counter()
            ws = self._ws
            if ws is None:  # a concurrent reconnect replaced the socket
                self._pending = None
                raise QlcError("not connected")
            try:
                await ws.send(msg)
                reply = await asyncio.wait_for(fut, timeout or self.timeout)
            except asyncio.TimeoutError as exc:
                raise QlcError(f"no reply to {msg!r} within {timeout or self.timeout}s") from exc
            except ConnectionClosed as exc:
                raise QlcError(f"connection to QLC+ closed: {exc}") from exc
            finally:
                self._pending = None
            self.latency.add((time.perf_counter() - t0) * 1000.0)
            return reply

    @staticmethod
    def _pairs(reply: str, skip: int = 2) -> dict[int, str]:
        parts = reply.split("|")[skip:]
        out: dict[int, str] = {}
        for i in range(0, len(parts) - 1, 2):
            try:
                out[int(parts[i])] = parts[i + 1]
            except ValueError:
                continue
        return out

    async def barrier(self) -> None:
        """Round trip on the same socket: QLC+ has handled every earlier message when this returns."""
        await self.request("QLC+API|getFunctionsNumber")

    async def functions_number(self) -> int:
        return int((await self.request("QLC+API|getFunctionsNumber")).split("|")[2])

    async def functions(self) -> dict[int, str]:
        # an empty list comes back without the trailing '|'
        return self._pairs(await self.request("QLC+API|getFunctionsList", reply_prefix="QLC+API|getFunctionsList", timeout=2.0))

    async def function_type(self, fid: int) -> str:
        return (await self.request(f"QLC+API|getFunctionType|{int(fid)}")).split("|")[2]

    async def function_status(self, fid: int) -> str:
        return (await self.request(f"QLC+API|getFunctionStatus|{int(fid)}")).split("|")[2]

    async def set_function(self, fid: int, on: bool) -> None:
        await self.send(f"QLC+API|setFunctionStatus|{int(fid)}|{1 if on else 0}")

    async def running_functions(self, fids) -> list[int]:
        running = []
        for fid in fids:
            if await self.function_status(fid) == "Running":
                running.append(int(fid))
        return running

    async def widgets_number(self) -> int:
        return int((await self.request("QLC+API|getWidgetsNumber")).split("|")[2])

    async def widgets(self) -> dict[int, str]:
        return self._pairs(await self.request("QLC+API|getWidgetsList", reply_prefix="QLC+API|getWidgetsList", timeout=2.0))

    async def widget_type(self, wid: int) -> str:
        return (await self.request(f"QLC+API|getWidgetType|{int(wid)}")).split("|")[3]

    async def press_widget(self, wid: int, value: int = 255) -> None:
        await self.send(f"{int(wid)}|{max(0, min(255, int(value)))}")

    async def grand_master(self, value: int) -> None:
        await self.send(f"GM_VALUE|{max(0, min(255, int(value)))}")

    async def is_project_loaded(self) -> bool:
        return (await self.request("QLC+API|isProjectLoaded")).split("|")[2] == "true"

    @staticmethod
    def abs_address(universe_1based: int, channel_1based: int) -> int:
        if not 1 <= universe_1based <= 64:
            raise QlcError(f"universe out of range: {universe_1based}")
        if not 1 <= channel_1based <= 512:
            raise QlcError(f"channel out of range: {channel_1based}")
        return (universe_1based - 1) * 512 + channel_1based

    async def set_channel(self, universe_1based: int, channel_1based: int, value: int) -> None:
        value = max(0, min(255, int(value)))
        await self.send(f"CH|{self.abs_address(universe_1based, channel_1based)}|{value}")

    async def reset_channel(self, universe_1based: int, channel_1based: int) -> None:
        await self.request(
            f"QLC+API|sdResetChannel|{self.abs_address(universe_1based, channel_1based)}",
            reply_prefix="QLC+API|getChannelsValues|",
        )

    async def reset_universe(self, universe_1based: int) -> None:
        await self.request(
            f"QLC+API|sdResetUniverse|{int(universe_1based)}",
            reply_prefix="QLC+API|getChannelsValues|",
        )

    async def channel_values(self, universe_1based: int, start_1based: int = 1, count: int = 512) -> list[dict]:
        reply = await self.request(f"QLC+API|getChannelsValues|{int(universe_1based)}|{int(start_1based)}|{int(count)}")
        parts = reply.split("|")[2:]
        out: list[dict] = []
        for i in range(0, len(parts) - 3, 4):
            try:
                out.append(
                    {
                        "channel": int(parts[i]),
                        "value": int(parts[i + 1]),
                        "type": parts[i + 2],
                        "override": parts[i + 3] == "1",
                    }
                )
            except ValueError:
                continue
        return out


async def smoke(cfg: Config, probe: bool = False, iters: int = 50) -> dict:
    client = QlcClient.from_config(cfg)
    report: dict = {"auth": client.username is not None}
    pushes: list[str] = []
    async with client:
        report["url"] = client.connected_url
        client.subscribe(lambda m: pushes.append(m))
        report["functions_number"] = await client.functions_number()
        funcs = await client.functions()
        report["functions_listed"] = len(funcs)
        report["sample_functions"] = list(funcs.items())[:5]
        report["widgets_number"] = await client.widgets_number()
        client.latency.samples.clear()
        for _ in range(iters):
            await client.functions_number()
        report["rtt"] = client.latency.summary()
        report["rtt_gate_p95_under_50ms"] = report["rtt"]["p95_ms"] is not None and report["rtt"]["p95_ms"] < 50
        if probe:
            universe, channel = 2, 512
            first = lambda rows: rows[0] if rows else None  # noqa: E731
            before = first(await client.channel_values(universe, channel, 1))
            await client.set_channel(universe, channel, 10)
            await asyncio.sleep(0.15)
            during = first(await client.channel_values(universe, channel, 1))
            await client.reset_channel(universe, channel)
            await asyncio.sleep(0.15)
            after = first(await client.channel_values(universe, channel, 1))
            report["probe"] = {
                "channel": f"U{universe} ch{channel}",
                "before": before,
                "during": during,
                "after": after,
                "set_ok": bool(during) and during["value"] == 10 and during["override"],
                "override_cleared": bool(after) and not after["override"],
            }
        await asyncio.sleep(0.3)
        report["pushes_seen"] = pushes[:10]
    return report


def smoke_main(args) -> int:
    cfg = load_config()
    if args.url:
        cfg.qlc_url = args.url
    try:
        report = asyncio.run(smoke(cfg, probe=args.probe, iters=args.iters))
    except Exception as exc:
        print(f"SMOKE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"QLC+ at {report['url']}  (auth header: {'yes' if report['auth'] else 'no'})")
        print(
            f"functions: {report['functions_number']} reported, {report['functions_listed']} listed; "
            f"widgets: {report['widgets_number']}"
        )
        for fid, name in report["sample_functions"]:
            print(f"  {fid:>4}  {name}")
        rtt = report["rtt"]
        gate = "PASS" if report["rtt_gate_p95_under_50ms"] else "FAIL"
        print(f"round trip: n={rtt['n']} p50={rtt['p50_ms']} ms p95={rtt['p95_ms']} ms max={rtt['max_ms']} ms  [{gate}: p95 < 50 ms]")
        if "probe" in report:
            p = report["probe"]
            print(
                f"probe {p['channel']}: before={p['before']} during={p['during']} after={p['after']} "
                f"set_ok={p['set_ok']} cleared={p['override_cleared']}"
            )
        if report["pushes_seen"]:
            print("pushes:", *report["pushes_seen"], sep="\n  ")
    ok = report["rtt_gate_p95_under_50ms"] and (("probe" not in report) or (report["probe"]["set_ok"] and report["probe"]["override_cleared"]))
    return 0 if ok else 1
