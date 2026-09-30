"""Reference-show decoders: turn someone else's exported light show into our own small,
format-agnostic ``ReferenceShow`` model (see ``base.py``) for private, local mining -- never a
copy of their actual show, and never executed or replayed as-is. No show files are shipped here;
tests use tiny synthetic samples built by hand from the documented formats.

Picked and ranked by ``lightai/knowledge/research/format-survey.md``. Registered in that
survey's "first wave" order: xLights ``.xsq``, then Light-O-Rama ``.lms``/``.loredit``, then
Vixen 3 ``.tim``.

Freestyler's ``.chb`` was the survey's 4th candidate *if* it turned out to be text-readable. It
isn't: the survey's own sources describe it as a documented but strictly binary layout (step
count, fade multiplier, per-channel value/mode words, per the FreeStyler wiki). Per the task's
instruction ("a stub freestyler.py only if the survey shows its format is text-readable;
otherwise skip it with a note") it is skipped entirely rather than stubbed out -- this is that
note; there is no ``freestyler.py`` in this package.

GDTF/MVR and other 3D/fixture-geometry formats are a separate concern (scene assembly, not show
timing/cues) and are out of scope for this package.
"""

from __future__ import annotations

from .base import (
    Cue,
    Decoder,
    NotDecodable,
    ReferenceShow,
    Section,
    Song,
    decode_file,
    registered,
)
from . import xlights as _xlights  # noqa: F401  (imported for its register() side effect)
from . import lor as _lor  # noqa: F401
from . import vixen3 as _vixen3  # noqa: F401

__all__ = [
    "Cue", "Decoder", "NotDecodable", "ReferenceShow", "Section", "Song",
    "decode_file", "registered",
]
