# Project memory index

This folder is the **canonical, git-tracked project memory** for this QLC+ fork. Keep
project knowledge here (not only in the per-user `~/.claude` memory dir) so it travels with
the repo. One file per topic; this file is the index.

- [build-procedure.md](build-procedure.md) — the verified recipe to build + install QLC+ here (MSYS2 MinGW64 + CMake/Ninja → double-clickable `C:\qlcplus`); D2XX SDK setup, the `QDebug` build fix, `-Werror` gotcha, and the full DLL-bundling closure.
- [priority-system-rebuild.md](priority-system-rebuild.md) — the per-function priority feature: **now IMPLEMENTED** on `priority-rebuild` (builds 426/426 warning-clean, installs, runs); records the key decisions/deviations and the pre-existing-vs-real test status. See also `../../PRIORITY_SYSTEM_REBUILD_SPEC.md` at the repo root.
- [salesforce-qlcplus-integration.md](salesforce-qlcplus-integration.md) — controlling QLC+ from a **Salesforce LWC** over the internet: the bridge is built INTO QLC+ by adding TLS/WSS to its Web Access WebSocket API (no cloud relay). LWC contract + reference page at repo root: `../../SALESFORCE_QLCPLUS_INTEGRATION.md`, `../../test-ws.html`.
