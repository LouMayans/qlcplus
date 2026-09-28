"""Safe project writes: timestamped backup, same-folder temp file, atomic replace, re-parse."""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from lightai.rig.qxw import Workspace


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup(path: Path, backups_dir: Path, keep: int = 50) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    dest = backups_dir / f"{path.stem}.{utc_stamp()}{path.suffix}"
    n = 1
    while dest.exists():
        dest = backups_dir / f"{path.stem}.{utc_stamp()}-{n}{path.suffix}"
        n += 1
    shutil.copy2(path, dest)
    olds = sorted(backups_dir.glob(f"{path.stem}.*{path.suffix}"), key=lambda p: p.stat().st_mtime)
    for old in olds[:-keep] if keep > 0 else []:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def atomic_write(path: Path, data: bytes, retries: int = 20, delay: float = 0.1) -> None:
    tmp = path.with_name(path.name + ".lightai.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    last = None
    for _ in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(delay)
    try:
        tmp.unlink()
    except OSError:
        pass
    raise PermissionError(f"could not replace {path} (locked by OneDrive or another program?): {last}")


def write_workspace(ws: Workspace, path: Path, backups_dir: Path, keep: int = 50) -> dict:
    data = ws.serialize()
    Workspace.from_bytes(data)
    bak = backup(path, backups_dir, keep) if path.exists() else None
    atomic_write(path, data)
    Workspace.load(path)
    return {"path": str(path), "backup": str(bak) if bak else None, "bytes": len(data)}
