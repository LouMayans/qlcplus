"""QLC+ workspace (.qxw): lossless load, typed views, and minimal-diff edits.

The file is parsed with lxml (DOCTYPE and whitespace kept). Serialization restores the
QLC+ conventions: double-quoted XML declaration, <!DOCTYPE Workspace>, 1-space indentation,
" />" for empty elements, LF line endings and a trailing newline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lxml import etree

from lightai.rig.qxf import kid, kids, local, text_of

NS = "http://www.qlcplus.org/Workspace"
WIDGET_TAGS = {
    "Frame",
    "SoloFrame",
    "Button",
    "Slider",
    "XYPad",
    "CueList",
    "SpeedDial",
    "Label",
    "Clock",
    "AudioTriggers",
    "Matrix",
}


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def _int(v, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


@dataclass
class FixturePatch:
    id: int
    name: str
    manufacturer: str
    model: str
    mode: str
    universe: int
    address: int
    channels: int


@dataclass
class FunctionInfo:
    id: int
    type: str
    name: str
    path: str = ""
    hidden: bool = False
    priority: int = 0
    refs: list = field(default_factory=list)
    fixtures: list = field(default_factory=list)
    values: dict = field(default_factory=dict)
    speed: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)


@dataclass
class WidgetInfo:
    id: int
    type: str
    caption: str
    function_id: Optional[int] = None
    parent_id: Optional[int] = None


@dataclass
class ChannelsGroupInfo:
    id: int
    name: str
    pairs: list


@dataclass
class FixtureGroupInfo:
    id: int
    name: str
    size: tuple
    heads: list


class Workspace:
    def __init__(self, tree: etree._ElementTree, path: Optional[Path] = None, spaced_empty: bool = True) -> None:
        self.spaced_empty = spaced_empty
        self.tree = tree
        self.path = Path(path) if path else None
        self.root = tree.getroot()
        if local(self.root) != "Workspace":
            raise ValueError("not a QLC+ workspace (root is not <Workspace>)")
        doctype = tree.docinfo.doctype or ""
        if "Workspace" not in doctype:
            raise ValueError("missing <!DOCTYPE Workspace>; QLC+ would reject this file")
        self.engine = kid(self.root, "Engine")
        if self.engine is None:
            raise ValueError("workspace has no <Engine>")

    @staticmethod
    def _style(data: bytes) -> bool:
        return data.count(b'" />') >= data.count(b'"/>')

    @classmethod
    def load(cls, path: Path) -> "Workspace":
        data = Path(path).read_bytes()
        return cls.from_bytes(data, path)

    @classmethod
    def from_bytes(cls, data: bytes, path: Optional[Path] = None) -> "Workspace":
        parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)
        return cls(etree.ElementTree(etree.fromstring(data, parser)), path, cls._style(data))

    def serialize(self) -> bytes:
        body = etree.tostring(self.tree, encoding="UTF-8", xml_declaration=False, doctype="<!DOCTYPE Workspace>")
        if self.spaced_empty:
            body = re.sub(rb"(?<=[^ ])/>", b" />", body)
        body = body.replace(b"\r\n", b"\n")
        if not body.endswith(b"\n"):
            body += b"\n"
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + body

    def fixtures(self) -> list:
        out = []
        for f in kids(self.engine, "Fixture"):
            out.append(
                FixturePatch(
                    id=_int(text_of(f, "ID"), -1),
                    name=text_of(f, "Name"),
                    manufacturer=text_of(f, "Manufacturer"),
                    model=text_of(f, "Model"),
                    mode=text_of(f, "Mode"),
                    universe=_int(text_of(f, "Universe")),
                    address=_int(text_of(f, "Address")),
                    channels=_int(text_of(f, "Channels")),
                )
            )
        return out

    def fixture_el(self, fid: int):
        for f in kids(self.engine, "Fixture"):
            if _int(text_of(f, "ID"), -1) == fid:
                return f
        return None

    def channels_groups(self) -> list:
        out = []
        for g in kids(self.engine, "ChannelsGroup"):
            nums = [_int(x) for x in (g.text or "").split(",") if x.strip()]
            out.append(ChannelsGroupInfo(_int(g.get("ID")), g.get("Name", ""), list(zip(nums[0::2], nums[1::2]))))
        return out

    def fixture_groups(self) -> list:
        out = []
        for g in kids(self.engine, "FixtureGroup"):
            size = kid(g, "Size")
            heads = [
                {"x": _int(h.get("X")), "y": _int(h.get("Y")), "fixture": _int(h.get("Fixture")), "head": _int(h.text)}
                for h in kids(g, "Head")
            ]
            out.append(
                FixtureGroupInfo(
                    _int(g.get("ID")),
                    text_of(g, "Name"),
                    (_int(size.get("X")) if size is not None else 0, _int(size.get("Y")) if size is not None else 0),
                    heads,
                )
            )
        return out

    def function_els(self) -> list:
        return kids(self.engine, "Function")

    def function_el(self, fid: int):
        for f in self.function_els():
            if _int(f.get("ID"), -1) == fid:
                return f
        return None

    def functions(self) -> list:
        return [parse_function(el) for el in self.function_els()]

    def max_function_id(self) -> int:
        ids = [_int(f.get("ID"), -1) for f in self.function_els()]
        return max(ids) if ids else -1

    def monitor(self):
        return kid(self.engine, "Monitor")

    def monitor_items(self) -> dict:
        mon = self.monitor()
        out: dict = {}
        if mon is None:
            return out
        for it in kids(mon, "FxItem"):
            if it.get("Head") is not None or it.get("Linked") is not None:
                continue
            out[_int(it.get("ID"), -1)] = {
                "x": float(it.get("XPos", "0") or 0),
                "y": float(it.get("YPos", "0") or 0),
                "rotation": _int(it.get("Rotation", it.get("YRot", "0"))),
                "gel": it.get("GelColor"),
            }
        return out

    def widgets(self) -> list:
        vc = kid(self.root, "VirtualConsole")
        out: list = []
        if vc is None:
            return out

        def walk(el, parent_id):
            for c in el:
                if not isinstance(c.tag, str):
                    continue
                name = local(c)
                if name in WIDGET_TAGS:
                    wid = _int(c.get("ID"), -1)
                    fid = None
                    if name == "Button":
                        f = kid(c, "Function")
                        fid = _int(f.get("ID"), -1) if f is not None else None
                    elif name == "Slider":
                        pb = kid(c, "Playback")
                        if pb is not None and kid(pb, "Function") is not None:
                            fid = _int(text_of(pb, "Function"), -1)
                    elif name == "CueList":
                        if kid(c, "Chaser") is not None:
                            fid = _int(text_of(c, "Chaser"), -1)
                    if fid is not None and fid < 0:
                        fid = None
                    out.append(WidgetInfo(wid, name, c.get("Caption", ""), fid, parent_id))
                    walk(c, wid)
                elif name not in ("Appearance", "WindowState", "Input", "Key", "Function", "Properties"):
                    walk(c, parent_id)

        walk(vc, None)
        return out

    def _indent_new(self, el, level: int) -> None:
        pad = "\n" + " " * (level + 1)
        children = [c for c in el if isinstance(c.tag, str)]
        if children:
            el.text = pad
            for c in children:
                self._indent_new(c, level + 1)
                c.tail = pad
            children[-1].tail = "\n" + " " * level

    def add_function(self, el) -> None:
        self._indent_new(el, 2)
        existing = self.function_els()
        if existing:
            anchor = existing[-1]
        else:
            anchors = [c for c in self.engine if isinstance(c.tag, str) and local(c) != "Monitor"]
            anchor = anchors[-1]
        el.tail = anchor.tail
        anchor.tail = "\n  "
        anchor.addnext(el)

    def replace_function(self, fid: int, el) -> None:
        old = self.function_el(fid)
        if old is None:
            raise KeyError(f"function {fid} not found")
        self._indent_new(el, 2)
        el.tail = old.tail
        old.getparent().replace(old, el)

    def remove_function(self, fid: int) -> None:
        old = self.function_el(fid)
        if old is None:
            raise KeyError(f"function {fid} not found")
        prev = old.getprevious()
        if prev is not None:
            prev.tail = old.tail
        old.getparent().remove(old)

    def set_fxitem(self, fid: int, **attrs) -> dict:
        mon = self.monitor()
        if mon is None:
            raise ValueError("project has no <Monitor> inside <Engine>; open the 2D monitor in QLC+ once and save")
        item = None
        for it in kids(mon, "FxItem"):
            if _int(it.get("ID"), -1) == fid and it.get("Head") is None and it.get("Linked") is None:
                item = it
                break
        before = dict(item.attrib) if item is not None else {}
        if item is None:
            item = etree.Element(q("FxItem"))
            item.set("ID", str(fid))
            item.set("XPos", "0")
            item.set("YPos", "0")
            last = kids(mon, "FxItem") or kids(mon, "Grid")
            if last:
                item.tail = last[-1].tail
                last[-1].tail = "\n   "
                last[-1].addnext(item)
            else:
                mon.append(item)
        for key in ("XRot", "YRot", "ZRot"):
            if key in item.attrib and "rotation" in attrs:
                del item.attrib[key]
        if "x" in attrs:
            item.set("XPos", _fmt(attrs["x"]))
        if "y" in attrs:
            item.set("YPos", _fmt(attrs["y"]))
        if "rotation" in attrs:
            rot = int(round(attrs["rotation"])) % 360
            if rot:
                item.set("Rotation", str(rot))
            elif "Rotation" in item.attrib:
                del item.attrib["Rotation"]
        if "gel" in attrs:
            if attrs["gel"]:
                item.set("GelColor", attrs["gel"])
            elif "GelColor" in item.attrib:
                del item.attrib["GelColor"]
        return {"before": before, "after": dict(item.attrib)}

    def add_fixture_element(self, fid: int, name: str, manufacturer: str, model: str, mode: str, universe: int, address: int, channels: int):
        el = new_element("Fixture")
        for tag, val in (("Manufacturer", manufacturer), ("Model", model), ("Mode", mode), ("ID", fid), ("Name", name),
                         ("Universe", universe), ("Address", address), ("Channels", channels)):
            sub(el, tag, text=val)
        self._indent_new(el, 2)
        fixtures = kids(self.engine, "Fixture")
        anchor = fixtures[-1] if fixtures else kid(self.engine, "InputOutputMap")
        if anchor is None:
            self.engine.insert(0, el)
            return el
        el.tail = anchor.tail
        anchor.tail = "\n  "
        anchor.addnext(el)
        return el

    def vc_root(self):
        vc = kid(self.root, "VirtualConsole")
        return kid(vc, "Frame") if vc is not None else None

    def max_widget_id(self) -> int:
        ids = [w.id for w in self.widgets() if 0 <= w.id < 4294967295]
        return max(ids) if ids else -1

    def lightai_frame(self, caption: str = "lightai", create: bool = True):
        root = self.vc_root()
        if root is None:
            raise ValueError("the project has no Virtual Console")
        for fr in kids(root, "Frame"):
            if fr.get("Caption") == caption:
                return fr
        if not create:
            return None
        bottom = 10
        for w in root:
            if not isinstance(w.tag, str) or local(w) not in WIDGET_TAGS:
                continue
            ws = kid(w, "WindowState")
            if ws is not None:
                bottom = max(bottom, int(float(ws.get("Y", "0"))) + int(float(ws.get("Height", "0"))) + 10)
        fr = new_element("Frame", {"Caption": caption, "ID": self.max_widget_id() + 1})
        app = sub(fr, "Appearance")
        for tag, val in (("FrameStyle", "Sunken"), ("ForegroundColor", "Default"), ("BackgroundColor", "Default"),
                         ("BackgroundImage", "None"), ("Font", "Default")):
            sub(app, tag, text=val)
        sub(fr, "WindowState", {"Visible": "False", "X": 10, "Y": bottom, "Width": 610, "Height": 215})
        for tag, val in (("AllowChildren", "True"), ("AllowResize", "True"), ("ShowHeader", "True"),
                         ("ShowEnableButton", "True"), ("Collapsed", "False"), ("Disabled", "False")):
            sub(fr, tag, text=val)
        self._indent_new(fr, 3)
        children = [c for c in root if isinstance(c.tag, str)]
        last = children[-1]
        fr.tail = last.tail
        last.tail = "\n   "
        last.addnext(fr)
        props = kid(kid(self.root, "VirtualConsole"), "Properties")
        size = kid(props, "Size") if props is not None else None
        if size is not None and int(size.get("Height", "0")) < bottom + 225:
            size.set("Height", str(bottom + 225))
        return fr

    def add_vc_button(self, function_id: int, caption: str, action: str = "Toggle") -> dict:
        for w in self.widgets():
            if w.type == "Button" and w.function_id == function_id:
                return {"existing": True, "widget_id": w.id, "caption": w.caption}
        fr = self.lightai_frame()
        buttons = kids(fr, "Button")
        n = len(buttons)
        col, row = n % 11, n // 11
        wid = self.max_widget_id() + 1
        b = new_element("Button", {"Caption": caption, "ID": wid, "Icon": ""})
        sub(b, "WindowState", {"Visible": "False", "X": 5 + col * 55, "Y": 40 + row * 55, "Width": 50, "Height": 50})
        app = sub(b, "Appearance")
        for tag, val in (("FrameStyle", "None"), ("ForegroundColor", "Default"), ("BackgroundColor", "Default"),
                         ("BackgroundImage", "None"), ("Font", "Default")):
            sub(app, tag, text=val)
        sub(b, "Function", {"ID": function_id})
        sub(b, "Action", text=action)
        sub(b, "Intensity", {"Adjust": "False"}, "100")
        self._indent_new(b, 4)
        ws = kid(fr, "WindowState")
        if ws is not None and row > 0:
            ws.set("Height", str(max(int(ws.get("Height", "215")), 40 + (row + 1) * 55 + 10)))
        kids_ = [c for c in fr if isinstance(c.tag, str)]
        last = kids_[-1]
        b.tail = last.tail
        last.tail = "\n    "
        last.addnext(b)
        return {"existing": False, "widget_id": wid, "caption": caption, "frame_id": int(fr.get("ID"))}

    def remove_buttons_for(self, function_ids: set) -> list:
        removed = []
        vc = kid(self.root, "VirtualConsole")
        if vc is None:
            return removed
        for b in list(vc.iter(q("Button"))):
            f = kid(b, "Function")
            if f is not None and int(f.get("ID", "-1")) in function_ids:
                prev = b.getprevious()
                if prev is not None:
                    prev.tail = b.tail
                b.getparent().remove(b)
                removed.append(int(b.get("ID", "-1")))
        return removed

    def rebind_buttons(self, old_id: int, new_id: int) -> int:
        n = 0
        vc = kid(self.root, "VirtualConsole")
        for b in (vc.iter(q("Button")) if vc is not None else []):
            f = kid(b, "Function")
            if f is not None and int(f.get("ID", "-1")) == old_id:
                f.set("ID", str(new_id))
                n += 1
        return n

    def readdress_fixture(self, fid: int, universe: int, address: int) -> dict:
        """Move a fixture to another 0-based universe / DMX address. Functions refer to fixtures by ID, so they stay intact."""
        el = self.fixture_el(fid)
        if el is None:
            raise KeyError(f"fixture {fid} not found")
        u, a = kid(el, "Universe"), kid(el, "Address")
        before = [int(u.text), int(a.text)]
        u.text, a.text = str(int(universe)), str(int(address))
        return {"fixture_id": fid, "before": before, "after": [int(universe), int(address)]}

    def rename_fixture(self, fid: int, name: str) -> dict:
        el = self.fixture_el(fid)
        if el is None:
            raise KeyError(f"fixture {fid} not found")
        n = kid(el, "Name")
        before = n.text
        n.text = name
        return {"before": before, "after": name}


def _fmt(v: float) -> str:
    s = f"{float(v):.6g}"
    return s


def parse_function(el) -> FunctionInfo:
    fi = FunctionInfo(
        id=_int(el.get("ID"), -1),
        type=el.get("Type", ""),
        name=el.get("Name", ""),
        path=el.get("Path", "") or "",
        hidden=el.get("Hidden") is not None,
        priority=_int(el.get("Priority")),
    )
    sp = kid(el, "Speed")
    if sp is not None:
        fi.speed = {k: _int(sp.get(k)) for k in ("FadeIn", "FadeOut", "Duration")}
    t = fi.type
    if t == "Scene":
        for fv in kids(el, "FixtureVal"):
            fid = _int(fv.get("ID"), -1)
            nums = [_int(x) for x in (fv.text or "").split(",") if x.strip()]
            fi.values[fid] = list(zip(nums[0::2], nums[1::2]))
            fi.fixtures.append(fid)
    elif t in ("Chaser", "Collection"):
        fi.refs = [_int(s.text, -1) for s in kids(el, "Step")]
        fi.extra["run_order"] = text_of(el, "RunOrder")
    elif t == "Sequence":
        fi.extra["bound_scene"] = _int(el.get("BoundScene"), -1)
        fi.refs = [fi.extra["bound_scene"]]
    elif t == "EFX":
        for fx in kids(el, "Fixture"):
            fi.fixtures.append(_int(text_of(fx, "ID"), -1))
        fi.extra["algorithm"] = text_of(el, "Algorithm")
    elif t == "Show":
        for tr in kids(el, "Track"):
            for sf in kids(tr, "ShowFunction"):
                fi.refs.append(_int(sf.get("ID"), -1))
    elif t == "RGBMatrix":
        fi.extra["fixture_group"] = _int(text_of(el, "FixtureGroup"), -1)
        alg = kid(el, "Algorithm")
        fi.extra["algorithm"] = (alg.text or alg.get("Type", "")) if alg is not None else ""
    return fi


def new_element(tag: str, attrib: Optional[dict] = None, text: Optional[str] = None):
    el = etree.Element(q(tag))
    for k, v in (attrib or {}).items():
        if v is not None:
            el.set(k, str(v))
    if text is not None:
        el.text = str(text)
    return el


def sub(parent, tag: str, attrib: Optional[dict] = None, text: Optional[str] = None):
    el = new_element(tag, attrib, text)
    parent.append(el)
    return el
