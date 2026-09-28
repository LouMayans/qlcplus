r"""Verify the two QLC+ engine fixes on an isolated QLC+ instance (port 9994, fresh test project; never port 9999).

Usage: C:\lightai-env\venv\Scripts\python tools\check_engine_fixes.py [path\to\qlcplus.exe]
(default: the fork test copy in C:\lightai-data\qlcplus-fork; pass C:\qlcplus\qlcplus.exe after installing the fork).

D1: an EFX that starts a contiguous-fine spot BEFORE a BEAM230 V3 (non-adjacent fine channels) must still move
    every spot's coarse pan/tilt. Stock QLC+ left those spots at coarse 0.
D2: a P100 kill over a P0 look must hold dimmers at 0. Stock QLC+ dropped the zero channels and the look won.
"""
import asyncio
import copy
import json
import shutil
import sys
import time
from pathlib import Path

from lxml import etree

from lightai.compiler import compile_look, write_look
from lightai.compiler.spec import LookParams
from lightai.config import load_config
from lightai.devtools import QlcInstance, make_test_project
from lightai.exec.wsclient import QlcClient
from lightai.rig.model import Rig

TMP = Path(r"C:/lightai-data/tmp/engine-check")
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r"C:/lightai-data/qlcplus-fork/qlcplus.exe")
PORT = 9994


def reorder_efx(proj: Path, rig: Rig) -> list:
    """Put the V3s (non-adjacent fine channels) LAST in every EFX: the order that used to break."""
    tree = etree.parse(str(proj))
    root = tree.getroot()
    ns = root.nsmap.get(None)
    q = (lambda t: f"{{{ns}}}{t}") if ns else (lambda t: t)
    orders = []
    for fn in root.iter(q("Function")):
        if fn.get("Type") != "EFX":
            continue
        fxs = [c for c in fn if c.tag == q("Fixture")]
        at = list(fn).index(fxs[0])
        for c in fxs:
            fn.remove(c)
        fxs.sort(key=lambda c: 1 if rig.fixtures[int(c.find(q("ID")).text)].key.endswith("V3") else 0)
        for i, c in enumerate(fxs):
            fn.insert(at + i, c)
        orders.append([int(c.find(q("ID")).text) for c in fxs])
    tree.write(str(proj), xml_declaration=True, encoding="UTF-8", doctype="<!DOCTYPE Workspace>")
    return orders


async def sample(c: QlcClient, fx, roles: list, n: int = 12, dt: float = 0.1) -> list:
    out = []
    for _ in range(n):
        vals = await c.channel_values(fx.universe + 1, fx.address + 1, fx.channels)
        out.append({r: vals[fx.roles[r]]["value"] for r in roles if r in fx.roles and fx.roles[r] < len(vals)})
        await asyncio.sleep(dt)
    return out


async def run(cfg, rig, ids: dict) -> dict:
    c = QlcClient(f"ws://127.0.0.1:{PORT}/qlcplusWS", timeout=2.0)
    await c.connect()
    res: dict = {}
    spot_ids = ids["spots"]
    # D1
    await c.set_function(ids["circle"], True)
    await asyncio.sleep(0.6)
    d1 = {}
    for fid in spot_ids:
        fx = rig.fixtures[fid]
        s = await sample(c, fx, ["pan", "tilt"], n=8, dt=0.08)
        pans = [x.get("pan", 0) for x in s]
        tilts = [x.get("tilt", 0) for x in s]
        d1[fid] = {"key": fx.key, "pan": pans, "tilt": tilts,
                   "moves": (max(pans) - min(pans) > 3 or max(tilts) - min(tilts) > 3) and max(pans) > 20 and max(tilts) > 20}
    await c.set_function(ids["circle"], False)
    await asyncio.sleep(0.5)
    res["D1"] = {"pass": all(v["moves"] for v in d1.values()), "stuck": [f for f, v in d1.items() if not v["moves"]], "detail": d1}
    # D2
    probe = [spot_ids[0]] + ids["washes"][:1]
    await c.set_function(ids["wash"], True)
    await asyncio.sleep(0.6)
    before = {f: (await sample(c, rig.fixtures[f], ["dimmer"], n=1))[0].get("dimmer") for f in probe}
    await c.set_function(ids["kill"], True)
    await asyncio.sleep(0.8)
    during = {f: [x.get("dimmer") for x in await sample(c, rig.fixtures[f], ["dimmer"], n=5, dt=0.1)] for f in probe}
    await c.set_function(ids["kill"], False)
    await asyncio.sleep(0.6)
    after = {f: (await sample(c, rig.fixtures[f], ["dimmer"], n=1))[0].get("dimmer") for f in probe}
    await c.set_function(ids["wash"], False)
    res["D2"] = {"pass": all(v > 200 for v in before.values()) and all(all(x == 0 for x in v) for v in during.values()) and all(v > 200 for v in after.values()),
                 "before_kill": before, "during_kill": during, "after_kill": after}
    await c.close()
    return res


def main() -> int:
    if TMP.exists():
        shutil.rmtree(TMP)
    cfg = copy.copy(load_config())
    cfg.data_dir = TMP / "data"
    cfg.data_dir.mkdir(parents=True)
    cfg.learned_path = TMP / "learned.yaml"
    cfg.project_path = make_test_project(TMP / "proj")
    rig = Rig.load(cfg)
    order = [f for f in (rig.stage_order or []) if f in set(rig.zones["spots"])] or sorted(rig.zones["spots"])
    spots = order + [f for f in sorted(rig.zones["spots"]) if f not in order]
    washes = sorted(rig.zones["washes"])
    ids = {"spots": spots, "washes": washes}
    for key, p in (("circle", LookParams(recipe="circle", targets=spots, colors=["green"], bpm=128)),
                   ("wash", LookParams(recipe="color_wash", targets=spots + washes, colors=["red"])),
                   ("kill", LookParams(recipe="blackout_kill", targets=spots + washes, priority=100))):
        look = compile_look(rig, p)
        write_look(rig, look)
        ids[key] = look.main_id
    ids["efx_order"] = reorder_efx(cfg.project_path, rig)
    inst = QlcInstance(cfg.project_path, port=PORT, exe=EXE).start()
    try:
        time.sleep(1.5)
        res = asyncio.run(run(cfg, rig, ids))
    finally:
        inst.stop()
    res["exe"] = str(EXE)
    res["efx_order"] = ids["efx_order"]
    (TMP / "result.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print("exe", EXE)
    print("EFX fixture order used:", ids["efx_order"])
    print("D1 all spots move:", res["D1"]["pass"], "stuck:", res["D1"]["stuck"])
    for f, v in res["D1"]["detail"].items():
        print(f"   fx{f:<3} {v['key']:<22} pan {v['pan'][:5]} tilt {v['tilt'][:5]}")
    print("D2 kill holds dimmers at 0:", res["D2"]["pass"], "before", res["D2"]["before_kill"], "during", res["D2"]["during_kill"], "after", res["D2"]["after_kill"])
    return 0 if res["D1"]["pass"] and res["D2"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
