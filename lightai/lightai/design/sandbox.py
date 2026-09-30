"""Preview a design in 3D without touching the real show or rig: a private QLC+ (the build with the 3D stage) plays
it on a sanitized copy of the show (DMX outputs removed) next to a copy of its stage file, on its own port. The
operator watches it in that QLC+'s /stage page. The sandbox stops by itself after 10 idle minutes."""

from __future__ import annotations

import asyncio
import copy
import shutil
import time
from pathlib import Path
from typing import Optional

IDLE_S = 600
DEV_EXE = Path(r"C:/qlcplus-dev/qlcplus.exe")
INSTALLED_EXE = Path(r"C:/qlcplus/qlcplus.exe")


def sandbox_exe(cfg) -> Path:
    """The configured build, else the first installed QLC+ that serves the 3D stage (Web/stage.html)."""
    exe = getattr(cfg, "sandbox_exe", None)
    if exe:
        return Path(exe)
    for cand in (INSTALLED_EXE, DEV_EXE):
        if cand.exists() and (cand.parent / "Web" / "stage.html").exists():
            return cand
    return INSTALLED_EXE


class Sandbox:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.port = int(getattr(cfg, "sandbox_port", 9997))
        self.dir = Path(cfg.data_dir) / "sandbox"
        self.inst = None
        self.show: Optional[Path] = None
        self.shows: list = []
        self.last_used = 0.0
        self.lock = asyncio.Lock()
        self._watchdog: Optional[asyncio.Task] = None

    @property
    def running(self) -> bool:
        return self.inst is not None

    async def preview(self, ai, design: dict, show_indexes: Optional[list] = None) -> dict:
        """Copy the show, add the design to the copy, (re)start the sandbox QLC+ on it and start the first show."""
        from lightai.compiler import write_design
        from lightai.design.composer import compose_design
        from lightai.design.spec import DesignResult
        from lightai.devtools import QlcInstance, sanitize_project
        from lightai.rig.model import Rig
        from lightai.rig.stage import stage_path

        async with self.lock:
            await asyncio.to_thread(self._stop)
            if self.dir.exists():
                shutil.rmtree(self.dir, ignore_errors=True)
            self.dir.mkdir(parents=True, exist_ok=True)
            src = Path(ai.cfg.project_path)
            show = sanitize_project(src, self.dir / src.name)
            if stage_path(src).exists():
                shutil.copy(stage_path(src), stage_path(show))
            scfg = copy.copy(ai.cfg)
            scfg.project_path, scfg.main_project_path = show, None
            rig = Rig.load(scfg)
            composed = compose_design(rig, DesignResult.model_validate(design), show_indexes=show_indexes,
                                      bpm=getattr(ai.session, "bpm", None))
            write_design(rig, composed, text="sandbox preview", source="sandbox")
            exe = sandbox_exe(ai.cfg)
            self.inst = await asyncio.to_thread(lambda: QlcInstance(show, port=self.port, exe=exe).start())
            self.show, self.shows = show, [{"main_id": c.main_id, "title": c.title} for c in composed]
            self.last_used = time.monotonic()
            await asyncio.sleep(1.5)
            if self.shows:
                await self.play(self.shows[0]["main_id"], locked=True)
            if self._watchdog is None or self._watchdog.done():
                self._watchdog = asyncio.create_task(self._idle_stop())
            has_stage = (exe.parent / "Web" / "stage.html").exists()
            return {"ok": True, "url": f"http://127.0.0.1:{self.port}/stage", "port": self.port, "shows": self.shows,
                    "exe": str(exe), "note": "a private copy of the show without DMX outputs; the real rig is not touched"
                                             + ("" if has_stage else "; this QLC+ build has no 3D stage page")}

    async def play(self, main_id: int, locked: bool = False) -> dict:
        from lightai.exec.wsclient import QlcClient

        if not self.running:
            raise RuntimeError("the sandbox isn't running; preview a design first")
        self.last_used = time.monotonic()
        client = QlcClient(f"ws://127.0.0.1:{self.port}/qlcplusWS", timeout=5.0)
        await client.connect()
        try:
            for s in self.shows:
                if s["main_id"] != main_id:
                    await client.set_function(s["main_id"], False)
            await client.set_function(main_id, True)
        finally:
            await client.close()
        return {"ok": True, "playing": main_id}

    def _stop(self) -> None:
        if self.inst is not None:
            try:
                self.inst.stop()
            finally:
                self.inst = None

    async def stop(self) -> dict:
        async with self.lock:
            await asyncio.to_thread(self._stop)
        return {"ok": True}

    async def _idle_stop(self) -> None:
        while self.running:
            await asyncio.sleep(30)
            if self.running and time.monotonic() - self.last_used > IDLE_S:
                await self.stop()
