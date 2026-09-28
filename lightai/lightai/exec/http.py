"""Structural reload of a project into a running QLC+.

Strategies
  post_loadProject  POST the .qxw bytes to /loadProject (stock web access). QLC+ forgets the
                    file name afterwards, so a later manual Save asks where to save.
  loadProjectFile   QLC+API|loadProjectFile|<path> (fork command, phase 1.5): QLC+ opens the
                    file itself and keeps its name.
  none              only write the file; the operator reloads it in QLC+.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from lightai.exec.wsclient import QlcClient, QlcError, tls_context


async def post_load_project(client: QlcClient, xml: bytes, timeout: float = 20.0) -> dict:
    base = client.http_base
    host = urlparse(base).hostname or client.host
    verify = tls_context(host, client.tls_server_name) if base.startswith("https") else False
    extensions = {"sni_hostname": client.tls_server_name} if client.tls_server_name else None
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(verify=verify, timeout=timeout) as http:
            resp = await http.post(
                f"{base}/loadProject",
                content=xml,
                headers={"Content-Type": "application/xml", **client.auth_header()},
                extensions=extensions,
            )
    except httpx.HTTPError as exc:
        raise QlcError(f"POST /loadProject failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code >= 400:
        raise QlcError(f"/loadProject returned HTTP {resp.status_code}: {resp.text[:200]}")
    loaded = await wait_project_loaded(client, timeout)
    return {"strategy": "post_loadProject", "http_status": resp.status_code, "loaded": loaded, "seconds": round(time.perf_counter() - t0, 2)}


async def wait_project_loaded(client: QlcClient, timeout: float = 20.0, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if await client.is_project_loaded():
                return True
        except QlcError:
            pass
        await asyncio.sleep(interval)
    return False


async def load_project_file(client: QlcClient, path: Path, timeout: float = 20.0, force: bool = False) -> dict:
    t0 = time.perf_counter()
    msg = f"QLC+API|loadProjectFile|{Path(path).resolve()}" + ("|force" if force else "")
    reply = await client.request(msg, timeout=timeout)
    parts = reply.split("|")
    if len(parts) < 3 or parts[2] != "OK":
        detail = "|".join(parts[2:]) or reply
        if "unsaved changes" in detail:
            return {"strategy": "loadProjectFile", "loaded": False, "blocked": True,
                    "note": "QLC+ has unsaved changes (for example from live retiming). The show file is already updated; "
                            "confirm the reload to load it (this discards the unsaved changes in QLC+)"}
        raise QlcError(f"loadProjectFile failed: {detail}")
    loaded = await wait_project_loaded(client, timeout)
    return {"strategy": "loadProjectFile", "loaded": loaded, "seconds": round(time.perf_counter() - t0, 2)}


async def supports_fork_commands(client: QlcClient) -> bool:
    """True when the running QLC+ has the lightai fork commands (loadProjectFile/saveProject)."""
    try:
        reply = await client.request("QLC+API|lightaiVersion", timeout=0.5)
        return len(reply.split("|")) >= 3 and reply.split("|")[2] != ""
    except QlcError:
        return False


async def fork_version(client: QlcClient) -> int:
    """0 on a stock build; 1 = lightai commands; 2 = also openProjectFile."""
    try:
        parts = (await client.request("QLC+API|lightaiVersion", timeout=0.5)).split("|")
    except QlcError:
        return 0
    return int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0


async def open_project_file(client: QlcClient, path: Path, force: bool = False, timeout: float = 20.0) -> dict:
    """Fork v2: make QLC+ open this show file (keeps the file name, so Save writes to it)."""
    t0 = time.perf_counter()
    msg = f"QLC+API|openProjectFile|{Path(path).resolve()}" + ("|force" if force else "")
    parts = (await client.request(msg, timeout=timeout)).split("|")
    if len(parts) < 3 or parts[2] != "OK":
        detail = "|".join(parts[2:])
        if "unsaved changes" in detail:
            return {"opened": False, "blocked": True, "note": "QLC+ has unsaved changes; confirm to discard them and open the main show"}
        raise QlcError(f"openProjectFile failed: {detail}")
    loaded = await wait_project_loaded(client, timeout)
    return {"opened": True, "loaded": loaded, "seconds": round(time.perf_counter() - t0, 2)}


async def project_file(client: QlcClient) -> Optional[dict]:
    """Which file the fork build has open, or None on a stock build."""
    if not await supports_fork_commands(client):
        return None
    parts = (await client.request("QLC+API|getProjectFile", timeout=1.0)).split("|")
    return {"path": parts[2] if len(parts) > 2 else "", "modified": len(parts) > 3 and parts[3] == "1"}


async def set_function_speed(client: QlcClient, fid: int, fade_in, fade_out, duration) -> dict:
    fmt = lambda v: "-" if v is None else str(int(v))  # noqa: E731
    reply = await client.request(f"QLC+API|setFunctionSpeed|{int(fid)}|{fmt(fade_in)}|{fmt(fade_out)}|{fmt(duration)}", timeout=1.0)
    parts = reply.split("|")
    if len(parts) > 3 and parts[3] == "ERR":
        raise QlcError(f"setFunctionSpeed {fid}: {'|'.join(parts[4:])}")
    return {"id": int(fid), "fade_in": int(parts[3]), "fade_out": int(parts[4]), "duration": int(parts[5])}


async def reload_project(client: QlcClient, path: Path, strategy: str = "auto", force: bool = False) -> dict:
    if strategy == "none":
        return {"strategy": "none", "loaded": False, "note": "file written; reload it in QLC+ (File > Open)"}
    if strategy in ("auto", "loadProjectFile") and await supports_fork_commands(client):
        info = await project_file(client)
        if info and info["path"] and Path(info["path"]).resolve() != Path(path).resolve():
            return {"strategy": "loadProjectFile", "loaded": False, "blocked": True,
                    "note": f"QLC+ has '{info['path']}' open but lightai edits '{path}'. Point project_path in "
                            f"%LIGHTAI_DATA%\\config.yaml at the file QLC+ runs, or open that file in QLC+."}
        return await load_project_file(client, path, force=force)
    if strategy == "loadProjectFile":
        raise QlcError("this QLC+ build has no loadProjectFile command (install the phase 1.5 fork build)")
    return await post_load_project(client, Path(path).read_bytes())
