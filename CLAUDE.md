# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this repo is

A personal fork of [QLC+](https://github.com/mcallegari/qlcplus) (lighting control software).
The goal of this work is to **re-implement a per-function "priority" system cleanly on top of
current upstream**. The original (buggy) implementation lives on the `master` branch; the
clean rebuild happens on the **`priority-rebuild`** branch, which is based directly on
upstream master plus a rebuild spec and a build fix.

- Active working branch: **`priority-rebuild`** (upstream master + `PRIORITY_SYSTEM_REBUILD_SPEC.md` + a `<QDebug>` build fix).
- `master`: the original buggy priority work — reference only, do not build on it.
- `upstream` remote = https://github.com/mcallegari/qlcplus.git.

## Project memory — IMPORTANT

Persistent project knowledge for this repo lives **in-repo and git-tracked** at
[.claude/memory/](.claude/memory/), indexed by [.claude/memory/MEMORY.md](.claude/memory/MEMORY.md).

**Always keep project memory here**, in the repo, so it is contained in git and travels with
the project — not only in the per-user `~/.claude` memory directory. When you learn something
durable about this project (build steps, decisions, gotchas), add/update a file under
`.claude/memory/` and link it from `.claude/memory/MEMORY.md`. Use the per-user `~/.claude`
memory only for cross-project facts; this repo's MEMORY.md there is just a pointer here.

Key memory files:
- [.claude/memory/build-procedure.md](.claude/memory/build-procedure.md) — how to build/install.
- [.claude/memory/priority-system-rebuild.md](.claude/memory/priority-system-rebuild.md) — the priority feature rebuild plan.
- [PRIORITY_SYSTEM_REBUILD_SPEC.md](PRIORITY_SYSTEM_REBUILD_SPEC.md) — the full, file-by-file rebuild specification.

## Building (verified working — full details in build-procedure.md)

Upstream migrated **qmake → CMake**; the old `make install` in the `how to` file is obsolete.
Build through the MSYS2 MinGW64 login shell
(`MSYSTEM=MINGW64 C:/msys64/usr/bin/bash.exe -lc '<cmd>'`), from the repo root:

```
cmake -G Ninja -S . -B build-mingw     # configure (once; default = Qt-Widgets UI = QLC+ 4)
ninja  -C build-mingw                   # build  (app: build-mingw/main/qlcplus.exe)
ninja  -C build-mingw install           # install to C:\qlcplus
```

- **`-Werror -Wextra -Wall` is ON** — any compiler warning fails the build. New code must be
  warning-clean under gcc 14.1.
- The `dmxusb` plugin needs the FTDI **D2XX SDK** at `C:/projects/D2XXSDK` (already set up).
- Making `C:\qlcplus` double-clickable needs Qt-runtime bundling beyond `ninja install` —
  see build-procedure.md (windeployqt + Qt plugin folders + the `ldd` dependency closure).
- For quick dev runs, the exe can be launched from inside the MinGW64 shell without bundling.

## Notes

- The `.claude/` directory and this `CLAUDE.md` are local fork tooling. If you ever prepare a
  clean PR to upstream QLC+, exclude them.
