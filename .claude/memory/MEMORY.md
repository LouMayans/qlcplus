# Project memory index

This folder is the **canonical, git-tracked project memory** for this QLC+ fork. Keep
project knowledge here (not only in the per-user `~/.claude` memory dir) so it travels with
the repo. One file per topic; this file is the index.

- [build-procedure.md](build-procedure.md) — the verified recipe to build + install QLC+ here (MSYS2 MinGW64 + CMake/Ninja → double-clickable `C:\qlcplus`); D2XX SDK setup, the `QDebug` build fix, `-Werror` gotcha, and the full DLL-bundling closure.
- [priority-system-rebuild.md](priority-system-rebuild.md) — the per-function priority feature: plan to re-implement it cleanly on the fresh upstream fork; see also `../../PRIORITY_SYSTEM_REBUILD_SPEC.md` at the repo root.
