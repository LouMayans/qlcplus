"""Show the 3D stage view on a safe copy of the Mayans show.

    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/demo_show.py start [--function 45] [--club-layout] [--no-browser]
    C:\\lightai-env\\venv\\Scripts\\python.exe tools/stagelib/demo_show.py stop

`start` makes a sanitized copy of the operator's current show (the file C:\\qlcplus\\show-path.txt
points at, else SaveFile/Main Project.qxw; no DMX inputs/outputs, so nothing reaches the real rig).
By default no stage file is written, so the page lays every fixture out itself (unsaved draft);
--club-layout instead writes an approximate club layout beside the copy (every fixture plus
DJ booth, dance floor, bar, tables and VIP booths). It then starts
the dev build C:\\qlcplus-dev\\qlcplus.exe on port 9998, runs a show function and opens /stage in the
default browser. It stays running until `stop`, which kills exactly that PID and restores the
QLC+ settings (recent files) the way lightai.devtools.QlcInstance does. Never touches port 9999.
"""
from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "lightai"))

from lightai import devtools  # noqa: E402
from lightai.rig.qxw import Workspace  # noqa: E402
import club_scene  # noqa: E402  (same dir: tools/stagelib/club_scene.py)

PORT = 9998
EXE = Path(r"C:\qlcplus-dev\qlcplus.exe")
LIVE_DIR = Path(r"C:\qlcplus").resolve()
WORKDIR = Path(r"C:\lightai-data\stage-demo")
STATE = WORKDIR / "demo-state.pkl"
SHOW_PATH_FILE = Path(r"C:\qlcplus\show-path.txt")


def current_show() -> Path:
    """The show the operator's start script opens (show-path.txt), else the repo's Main Project."""
    try:
        path = Path(SHOW_PATH_FILE.read_text(encoding="utf-8-sig").strip().strip('"'))
        if path.suffix.lower() == ".qxw" and path.exists():
            return path
    except OSError:
        pass
    return REPO / "SaveFile" / "Main Project.qxw"

def running_qlcplus() -> list:
    """[(pid, exe path)] of every running qlcplus.exe."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='qlcplus.exe'\" | "
                          "Select-Object ProcessId, ExecutablePath | ConvertTo-Json -Compress"],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        return []
    rows = json.loads(out)
    rows = rows if isinstance(rows, list) else [rows]
    return [(int(r["ProcessId"]), r.get("ExecutablePath") or "") for r in rows]


def close_other_instances() -> None:
    """Only one QLC+ at a time: close dev/test copies by PID; never touch the live rig's copy."""
    for pid, path in running_qlcplus():
        if Path(path).parent.resolve() == LIVE_DIR:
            sys.exit(f"the live QLC+ (PID {pid}, {path}) is running; close it yourself before starting the demo")
        print(f"closing QLC+ PID {pid} ({path}) so only one instance runs")
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    if STATE.exists():
        STATE.unlink()
    time.sleep(1.0)


def start(function_id: int, open_browser: bool, club_layout: bool, fresh: bool) -> None:
    close_other_instances()
    if devtools.port_open(PORT):
        sys.exit(f"port {PORT} is still in use by something else")
    if not EXE.exists():
        sys.exit(f"{EXE} not found (see .claude/memory/stage-visualizer.md for the dev copy)")
    WORKDIR.mkdir(parents=True, exist_ok=True)
    source = current_show()
    project = devtools.sanitize_project(source, WORKDIR / source.name)
    stage_path = project.with_name(project.stem + ".stage.json")
    # keep what the operator saved last time unless --fresh
    if fresh:
        for old in (stage_path, stage_path.with_name(stage_path.name + ".bak")):
            if old.exists():
                old.unlink()
    stage = club_scene.build_stage(project) if club_layout and not stage_path.exists() else None
    if stage:
        stage_path.write_text(json.dumps(stage, indent=2), encoding="utf-8")

    settings = devtools.snapshot_settings()
    proc = subprocess.Popen([str(EXE), "-w", "-wp", str(PORT), "--web-bind", "127.0.0.1", "-p", "-o", str(project)], cwd=str(EXE.parent),
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    STATE.write_bytes(pickle.dumps({"pid": proc.pid, "settings": settings}))
    deadline = time.time() + 60
    while not devtools.port_open(PORT):
        if proc.poll() is not None or time.time() > deadline:
            sys.exit("the dev QLC+ did not start")
        time.sleep(0.3)
    time.sleep(2.0)

    import asyncio
    import websockets

    async def run_function() -> None:
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/qlcplusWS") as ws:
            await ws.send(f"QLC+API|setFunctionStatus|{function_id}|1")
            await asyncio.sleep(0.5)

    asyncio.run(run_function())
    print(f"QLC+ dev instance: PID {proc.pid}, port {PORT}, project {project}")
    print(f"show: {source} (sanitized copy {project})")
    if stage:
        print(f"stage layout: {stage_path} ({len(stage['fixtures'])} fixtures placed, {len(stage['objects'])} objects)")
    elif stage_path.exists():
        print(f"kept your saved stage layout: {stage_path}")
    else:
        print("no stage file: the page places every fixture itself (unsaved until Save)")
    print(f"running function {function_id}")
    url = f"http://127.0.0.1:{PORT}/stage"
    print(f"open {url}")
    if open_browser:
        webbrowser.open(url)


def stop() -> None:
    if not STATE.exists():
        sys.exit("no demo is running (no state file)")
    state = pickle.loads(STATE.read_bytes())
    subprocess.run(["taskkill", "/PID", str(state["pid"]), "/T", "/F"], capture_output=True)
    snap = {k: {n: v for n, v in vals.items() if not n.startswith("recent")} for k, vals in state["settings"].items()}
    devtools.restore_settings_except_recent(snap)
    devtools.clean_recent_files()
    STATE.unlink()
    print(f"stopped PID {state['pid']} and restored QLC+ settings")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["start", "stop"])
    ap.add_argument("--function", type=int, default=45, help="function ID to start (45 = Auto Chaser Show 1)")
    ap.add_argument("--club-layout", action="store_true", help="write the approximate club layout instead of letting the page lay out")
    ap.add_argument("--fresh", action="store_true", help="discard the saved demo stage file and start over")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.action == "start":
        start(args.function, not args.no_browser, args.club_layout, args.fresh)
    else:
        stop()


if __name__ == "__main__":
    main()
