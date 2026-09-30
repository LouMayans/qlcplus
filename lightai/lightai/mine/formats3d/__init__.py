"""3D-file decoders: MVR scenes (DIN SPEC 15801) and GDTF fixtures (DIN SPEC 15800).

Both formats are zip containers holding an XML description plus embedded fixtures/3D models.
See lightai/knowledge/research/format-survey.md for the background, and the MIT reference
parsers (open-stage/python-mvr, jackdpage/python-gdtf) and libMVRgdtf this package follows the
shape of without depending on them.

Safety: every member here is read out of the zip *in memory* via ``ZipFile.read`` -- nothing is
ever extracted to disk -- so there is no path for a zip-slip entry to land outside a temp dir.
`check_zip_safety` still refuses such an archive outright (rather than silently trusting it) and
caps total uncompressed size against a zip bomb, and `SAFE_XML_PARSER` disables external-entity/
network resolution for the untrusted XML both decoders parse.
"""

from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

from lxml import etree

MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # refuse a zip bomb before reading any content


class Formats3DError(Exception):
    """A malformed, unsafe (zip-slip / oversized) or otherwise unreadable MVR or GDTF archive."""


def _unsafe_name(name: str) -> bool:
    """True for an absolute path (POSIX or a Windows drive letter) or a ``..`` traversal segment."""
    norm = name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) > 1 and norm[1] == ":" and norm[0].isalpha()):
        return True
    return ".." in PurePosixPath(norm).parts


def check_zip_safety(zf: zipfile.ZipFile, max_total_bytes: int = MAX_UNCOMPRESSED_BYTES) -> list:
    """Validate every entry of an already-opened zip and return its infolist.

    Raises Formats3DError for a zip-slip entry (absolute path, drive letter, or a ``..``
    segment anywhere in the path) or a total uncompressed size over `max_total_bytes`.
    """
    infos = zf.infolist()
    total = 0
    for info in infos:
        if _unsafe_name(info.filename):
            raise Formats3DError(f"unsafe path in archive: {info.filename!r}")
        total += info.file_size
        if total > max_total_bytes:
            raise Formats3DError(f"archive exceeds the {max_total_bytes}-byte uncompressed size cap")
    return infos


# Untrusted XML: no external entity/network resolution, no relaxed huge-tree allowance.
SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


from .mvr import MvrFixture, MvrScene, MvrSceneObject, decode_mvr  # noqa: E402
from .gdtf import GdtfChannel, GdtfFixture, GdtfMode, GdtfPhysical, decode_gdtf  # noqa: E402
from .to_stage import mvr_to_stage  # noqa: E402

__all__ = [
    "Formats3DError",
    "MAX_UNCOMPRESSED_BYTES",
    "check_zip_safety",
    "SAFE_XML_PARSER",
    "MvrFixture",
    "MvrScene",
    "MvrSceneObject",
    "decode_mvr",
    "GdtfChannel",
    "GdtfFixture",
    "GdtfMode",
    "GdtfPhysical",
    "decode_gdtf",
    "mvr_to_stage",
]
