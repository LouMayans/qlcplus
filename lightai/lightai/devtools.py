"""Isolated QLC+ test instance: a sanitized project copy with no DMX inputs/outputs, on its own port.

Used by the end-to-end tests so nothing ever reaches the club's Art-Net nodes or replaces
the show running on the live instance.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from lxml import etree

from lightai.rig.qxf import kid, kids

try:
    import winreg
except ImportError:
    winreg = None

SETTINGS_KEY = r"Software\qlcplus\Q Light Controller Plus"
TEST_PROJECT_DIR = Path(os.environ.get("LIGHTAI_DATA", r"C:\lightai-data")) / "test-project"
TEST_PROJECT_NAME = "lightai-test-rig.qxw"


def snapshot_settings() -> dict:
    """Copy of QLC+'s per-user registry settings (recent files etc.) so a test instance can't change them."""
    snap: dict = {}
    if winreg is None:
        return snap

    def walk(path: str) -> None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with key:
            vals = {}
            i = 0
            while True:
                try:
                    name, data, typ = winreg.EnumValue(key, i)
                except OSError:
                    break
                vals[name] = (data, typ)
                i += 1
            snap[path] = vals
            j = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, j)
                except OSError:
                    break
                walk(path + "\\" + sub)
                j += 1

    walk(SETTINGS_KEY)
    return snap


def clean_recent_files(markers: tuple = ("lightai-data", "lightai-test-rig"), keep: Optional[list] = None) -> list:
    """Drop test projects from QLC+'s recent-files list (recent0..recent9) and keep the real ones in order."""
    if winreg is None:
        return []
    path = SETTINGS_KEY + r"\workspace"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
    except OSError:
        return []
    removed = []
    with key:
        entries = []
        for i in range(10):
            try:
                val, typ = winreg.QueryValueEx(key, f"recent{i}")
            except OSError:
                continue
            if any(m.lower() in str(val).lower() for m in markers):
                removed.append(str(val))
            else:
                entries.append((val, typ))
        present = [str(v) for v, _ in entries]
        for val, typ in keep or []:
            if len(entries) >= 10:
                break
            if str(val) not in present and not any(m.lower() in str(val).lower() for m in markers):
                entries.append((val, typ))
                present.append(str(val))
                removed.append(f"(restored pushed-out entry) {val}")
        if not removed:
            return []
        for i in range(10):
            if i < len(entries):
                winreg.SetValueEx(key, f"recent{i}", 0, entries[i][1], entries[i][0])
            else:
                try:
                    winreg.DeleteValue(key, f"recent{i}")
                except OSError:
                    pass
    return removed


def restore_settings(snap: dict) -> list:
    """Undo any registry value a test instance added or changed; returns what was reverted."""
    changed = []
    if winreg is None or not snap:
        return changed
    now = snapshot_settings()
    for path, vals in now.items():
        before = snap.get(path, {})
        if vals == before:
            continue
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
        except OSError:
            continue
        with key:
            for name in vals:
                if name not in before:
                    winreg.DeleteValue(key, name)
                    changed.append(f"removed {path}\\{name}")
            for name, (data, typ) in before.items():
                if vals.get(name) != (data, typ):
                    winreg.SetValueEx(key, name, 0, typ, data)
                    changed.append(f"restored {path}\\{name}")
    return changed


DEFAULT_EXE = Path(r"C:\qlcplus\qlcplus.exe")


def sanitize_project(src: Path, dst: Path) -> Path:
    """Copy a .qxw with every universe's Input/Output/Feedback patch removed."""
    parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
    tree = etree.parse(str(src), parser)
    engine = kid(tree.getroot(), "Engine")
    iomap = kid(engine, "InputOutputMap") if engine is not None else None
    if iomap is not None:
        for uni in kids(iomap, "Universe"):
            for child in list(uni):
                if isinstance(child.tag, str) and etree.QName(child).localname in ("Input", "Output", "Feedback"):
                    uni.remove(child)
            if not [c for c in uni if isinstance(c.tag, str)]:
                uni.text = None
    dst.parent.mkdir(parents=True, exist_ok=True)
    from lightai.rig.qxw import Workspace

    ws = Workspace(tree, dst)
    dst.write_bytes(ws.serialize())
    return dst


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


class QlcInstance:
    def __init__(self, project: Path, port: int = 9998, exe: Path = DEFAULT_EXE, extra_args: Optional[list] = None, env: Optional[dict] = None) -> None:
        self.project = Path(project)
        self.port = port
        self.exe = Path(exe)
        self.extra_args = extra_args or []
        self.env = env
        self.proc: Optional[subprocess.Popen] = None

    def start(self, timeout: float = 45.0) -> "QlcInstance":
        if int(self.port) == 9999:
            raise RuntimeError("port 9999 belongs to the production QLC+; test instances must use another port")
        if port_open(self.port):
            raise RuntimeError(f"port {self.port} is already in use; refusing to start a test instance there")
        self._lock(timeout)
        self.settings = snapshot_settings()
        args = [str(self.exe), "-w", "-wp", str(self.port), "--web-bind", "127.0.0.1", "-p", "-o", str(self.project), *self.extra_args]
        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        self.proc = subprocess.Popen(args, cwd=str(self.exe.parent), env=env)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._unlock()
                raise RuntimeError(f"QLC+ test instance exited early with code {self.proc.returncode}")
            if port_open(self.port):
                time.sleep(1.5)
                return self
            time.sleep(0.3)
        self.stop()
        raise TimeoutError(f"QLC+ test instance did not open port {self.port} within {timeout}s")

    def stop(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        self.proc = None
        snap = {k: {n: v for n, v in vals.items() if not n.startswith("recent")} for k, vals in getattr(self, "settings", {}).items()}
        wk = getattr(self, "settings", {}).get(SETTINGS_KEY + r"\workspace", {})
        keep = [wk[f"recent{i}"] for i in range(10) if f"recent{i}" in wk]
        high = {n: v for n, v in wk.items() if n.startswith("recent") and n[6:].isdigit() and int(n[6:]) >= 10}
        extra = restore_settings_except_recent({SETTINGS_KEY + r"\workspace": high}) if high else []
        self.reverted = restore_settings_except_recent(snap) + extra + [f"recent files: {r}" for r in clean_recent_files(keep=keep)]
        self._unlock()

    def _lock(self, timeout: float) -> None:
        """One test QLC+ at a time machine-wide: overlapping instances rewrite each other's recent-files list."""
        import msvcrt

        path = Path(os.environ.get("LIGHTAI_DATA", r"C:\lightai-data")) / "qlc-test-instance.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lockfh = open(path, "a+b")
        deadline = time.time() + max(60.0, timeout * 4)
        while True:
            try:
                self._lockfh.seek(0)
                msvcrt.locking(self._lockfh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.time() > deadline:
                    self._lockfh.close()
                    self._lockfh = None
                    raise TimeoutError("another QLC+ test instance is running (lock held); try again later")
                time.sleep(0.5)

    def _unlock(self) -> None:
        import msvcrt

        fh = getattr(self, "_lockfh", None)
        if fh is None:
            return
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()
        self._lockfh = None

    def __enter__(self) -> "QlcInstance":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def fresh_copy(src: Path, workdir: Path) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    dst = workdir / src.name
    if dst.exists():
        dst.unlink()
    for extra in ("lightai-looks.json",):
        p = workdir / extra
        if p.exists():
            p.unlink()
    backups = workdir / "backups"
    if backups.exists():
        shutil.rmtree(backups, ignore_errors=True)
    return sanitize_project(src, dst)


def restore_settings_except_recent(snap: dict) -> list:
    changed = []
    if winreg is None or not snap:
        return changed
    now = snapshot_settings()
    for path, vals in now.items():
        before = snap.get(path, {})
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE)
        except OSError:
            continue
        with key:
            for name, (data, typ) in before.items():
                if vals.get(name) != (data, typ):
                    winreg.SetValueEx(key, name, 0, typ, data)
                    changed.append(f"restored {name}")
    return changed


def make_test_project(workdir: Path = TEST_PROJECT_DIR, template: Optional[Path] = None) -> Path:
    """A fresh, clearly named test project: the rig from the Blank Rig Template, no functions, no DMX I/O."""
    from lightai.config import REPO_ROOT

    template = template or (REPO_ROOT / "SaveFile" / "Blank Rig Template.qxw")
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    return sanitize_project(template, workdir / TEST_PROJECT_NAME)
