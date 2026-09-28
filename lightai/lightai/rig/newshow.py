"""A new, empty show file next to the main show: the main show's DMX universes, outputs and inputs, nothing else."""

from __future__ import annotations

import os
import re
from pathlib import Path

from lxml import etree

NAME_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-().,'&+]{0,59}$")
RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.I)  # names Windows won't create


def show_path(folder: Path, name: str) -> Path:
    """'Friday Test' -> <folder>/Friday Test.qxw. A plain file name only: no folders or drive letters."""
    n = (name or "").strip()
    if n.lower().endswith(".qxw"):
        n = n[:-4].rstrip()
    if not NAME_OK.match(n) or n.endswith(".") or RESERVED.match(n):
        raise ValueError("use letters, digits, spaces and - _ ( ) . , ' & + (up to 60 characters)")
    return Path(folder) / f"{n}.qxw"


def create_empty_show(template: Path, dest: Path) -> Path:
    """Copy the template's I/O setup (universes with their outputs, inputs and feedback) into a new show with no
    fixtures, groups, functions, virtual console or simple desk. QLC+ opens it on the fixture manager."""
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"a show called '{dest.stem}' already exists")
    tree = etree.parse(str(template), etree.XMLParser(remove_blank_text=True))
    root = tree.getroot()
    for child in list(root):
        tag = etree.QName(child).localname if isinstance(child.tag, str) else ""
        if tag == "Engine":
            for c in list(child):
                if not isinstance(c.tag, str) or etree.QName(c).localname != "InputOutputMap":
                    child.remove(c)
        elif tag != "Creator":
            root.remove(child)
    root.set("CurrentWindow", "FixtureManager")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tree.write(str(tmp), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    os.replace(tmp, dest)
    return dest
