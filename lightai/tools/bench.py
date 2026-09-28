"""Thin launcher: `python tools/bench.py` == `python -m lightai bench`."""

import sys

from lightai.__main__ import main

sys.exit(main(["bench", *sys.argv[1:]]))
