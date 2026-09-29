"""A small fake of QLC+'s Web Access WebSocket, faithful to the replies lightai relies on.

Unknown QLC+API commands get an empty echo ("QLC+API|<cmd>|"), like the real dispatcher.
setFunctionStatus sends no reply, only a FUNCTION push. CH has no reply. The fork commands
(lightaiVersion, loadProjectFile, saveProject, getProjectFile, get/setFunctionSpeed) are
answered only when fork=True.
"""

from __future__ import annotations

import asyncio
import socket
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

import websockets

from lightai.rig.qxw import Workspace


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeQlc:
    def __init__(self, project: Optional[Path] = None, fork: bool = True) -> None:
        self.fork = fork
        self.project = Path(project) if project else None
        self.functions: dict = {}
        self.values: dict = defaultdict(int)
        self.override: set = set()
        self.gm = 255
        self.loaded_flag = False
        self.log: list = []
        self.loads = 0
        self.saves = 0
        self.modified = False
        if self.project:
            self.load(self.project)
        self.port = free_port()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.server = None

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/qlcplusWS"

    def load(self, path: Path) -> None:
        ws = Workspace.load(path)
        running = {i for i, f in self.functions.items() if f.get("running")}
        self.functions = {f.id: {"name": f.name, "type": f.type, "running": False, "speed": [f.speed.get("FadeIn", 0), f.speed.get("FadeOut", 0), f.speed.get("Duration", 0)]}
                          for f in ws.functions()}
        self.loaded_flag = True
        self.loads += 1
        del running

    def start(self) -> "FakeQlc":
        self.thread.start()

        async def serve():
            self.server = await websockets.serve(self.handler, "127.0.0.1", self.port)

        asyncio.run_coroutine_threadsafe(serve(), self.loop).result(5)
        return self

    def stop(self) -> None:
        async def close():
            if self.server:
                self.server.close()
                await self.server.wait_closed()

        try:
            asyncio.run_coroutine_threadsafe(close(), self.loop).result(5)
        except Exception:
            pass
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(5)

    async def handler(self, ws) -> None:
        try:
            async for msg in ws:
                self.log.append(msg)
                for out in self.handle(msg):
                    await ws.send(out)
        except websockets.exceptions.ConnectionClosed:
            pass

    def _channels(self, uni: int, start: int, count: int) -> str:
        parts = []
        for ch in range(start, start + count):
            addr = (uni - 1) * 512 + ch
            parts += [str(ch), str(self.values.get(addr, 0)), "", "1" if addr in self.override else "0"]
        return "|".join(parts)

    def handle(self, msg: str) -> list:
        p = msg.split("|")
        if p[0] == "QLC+API" and len(p) >= 2:
            cmd = p[1]
            head = f"QLC+API|{cmd}|"
            if cmd == "getFunctionsNumber":
                return [head + str(len(self.functions))]
            if cmd == "getFunctionsList":
                return [head + "|".join(f"{i}|{f['name']}" for i, f in self.functions.items())]
            if cmd == "getFunctionType" and len(p) > 2:
                f = self.functions.get(int(p[2]))
                return [head + (f["type"] if f else "Undefined")]
            if cmd == "getFunctionStatus" and len(p) > 2:
                f = self.functions.get(int(p[2]))
                return [head + ("Undefined" if f is None else ("Running" if f["running"] else "Stopped"))]
            if cmd == "setFunctionStatus" and len(p) > 3:
                f = self.functions.get(int(p[2]))
                if f is None:
                    return []
                on = p[3] != "0"
                if f["running"] != on:
                    f["running"] = on
                    return [f"FUNCTION|{p[2]}|{'Running' if on else 'Stopped'}"]
                return []
            if cmd == "getWidgetsNumber":
                return [head + "0"]
            if cmd == "getWidgetsList":
                return [head.rstrip("|")]
            if cmd == "isProjectLoaded":
                v = self.loaded_flag
                self.loaded_flag = False
                return [head + ("true" if v else "false")]
            if cmd == "getChannelsValues" and len(p) > 3:
                uni, start = int(p[2]), int(p[3])
                count = int(p[4]) if len(p) > 4 else 32
                return [head + self._channels(uni, start, count)]
            if cmd == "sdResetChannel" and len(p) > 2:
                addr = int(p[2])
                self.override.discard(addr)
                self.values[addr] = 0
                return ["QLC+API|getChannelsValues|" + self._channels(1, 1, 16)]
            if cmd == "sdResetUniverse" and len(p) > 2:
                uni = int(p[2])
                for addr in [a for a in self.override if (a - 1) // 512 == uni - 1]:
                    self.override.discard(addr)
                    self.values[addr] = 0
                return ["QLC+API|getChannelsValues|" + self._channels(uni, 1, 16)]
            if not self.fork:
                return [head]
            if cmd == "lightaiVersion":
                return [head + "2"]
            if cmd == "getProjectFile":
                return [head + f"{self.project or ''}|{1 if self.modified else 0}"]
            if cmd == "loadProjectFile" and len(p) > 2:
                target = Path(p[2])
                force = len(p) > 3 and p[3] == "force"
                if not target.is_absolute() or not target.is_file() or target.suffix.lower() != ".qxw":
                    return [head + "ERR|not an existing absolute .qxw path"]
                if self.project and target.resolve() != self.project.resolve():
                    return [head + "ERR|only the open project file can be reloaded"]
                if self.modified and not force:
                    return [head + "ERR|unsaved changes in QLC+"]
                self.project = target
                self.load(target)
                return [head + "OK"]
            if cmd == "openProjectFile" and len(p) > 2:
                target = Path(p[2])
                force = len(p) > 3 and p[3] == "force"
                if not target.is_absolute() or not target.is_file() or target.suffix.lower() != ".qxw":
                    return [head + "ERR|not an existing absolute .qxw path"]
                if self.modified and not force:
                    return [head + "ERR|unsaved changes in QLC+"]
                self.project = target
                self.modified = False
                self.load(target)
                return [head + "OK"]
            if cmd == "saveProject":
                if not self.project:
                    return [head + "ERR|project has no file name"]
                self.saves += 1
                return [head + f"OK|{self.project}"]
            if cmd == "getFunctionSpeed" and len(p) > 2:
                f = self.functions.get(int(p[2]))
                if f is None:
                    return [head + f"{p[2]}|ERR|no such function"]
                return [head + f"{p[2]}|" + "|".join(str(x) for x in f["speed"])]
            if cmd == "setFunctionSpeed" and len(p) > 5:
                f = self.functions.get(int(p[2]))
                if f is None:
                    return [head + f"{p[2]}|ERR|no such function"]
                for i, raw in enumerate(p[3:6]):
                    if raw.isdigit():
                        f["speed"][i] = int(raw)
                return [head + f"{p[2]}|" + "|".join(str(x) for x in f["speed"])]
            return [head]
        if p[0] == "CH" and len(p) >= 3:
            addr, v = int(p[1]), int(p[2])
            self.values[addr] = v
            self.override.add(addr)
            return []
        if p[0] == "GM_VALUE" and len(p) >= 2:
            self.gm = int(p[1])
            return []
        return []


class FakeQlcHttp:
    """A tiny fake of QLC+'s static-file HTTP side (the /stage, /stage-lib, /three, /gobos files
    the lightai stage proxy fetches) - a fixed table of path -> (status, content_type, body).
    An unlisted path answers 404, like a real QLC+ that doesn't have that file (or, for "/stage"
    itself, a stock build with no 3D stage at all)."""

    def __init__(self, routes: Optional[dict] = None) -> None:
        self.routes: dict = routes or {}
        self.requests: list = []  # (path, headers) for every request seen, newest last
        self.port = free_port()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a) -> None:  # quiet: pytest -q shouldn't get raw HTTP logs
                pass

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
                outer.requests.append((self.path, dict(self.headers)))
                route = outer.routes.get(self.path.split("?", 1)[0])
                if route is None:
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"not found")
                    return
                status, ctype, body = route
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FakeQlcHttp":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
