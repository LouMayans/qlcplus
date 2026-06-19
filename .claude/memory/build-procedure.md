---
name: build-procedure
description: The exact verified recipe that builds + installs QLC+ on this Windows machine (MSYS2 MinGW64 + CMake → double-clickable C:\qlcplus)
metadata: 
  node_type: memory
  type: project
  originSessionId: 5dda5587-cb25-4c41-beb4-145ff75c5d67
---

End-to-end recipe that PRODUCED A WORKING INSTALL (verified 2026-06-17: `C:\qlcplus\qlcplus.exe` launches standalone, opens "Q Light Controller Plus - New Workspace"). Current upstream QLC+ uses **CMake**, not qmake — the old `make install` flow in the repo's `how to` file is obsolete. Related: [[priority-system-rebuild]].

## Environment
- MSYS2 MinGW64 at `C:\msys64` → gcc 14.1, Qt **5.15.14**, cmake 3.30, ninja 1.12.
- Run everything through the login shell so PATH/env are correct:
  `MSYSTEM=MINGW64 C:/msys64/usr/bin/bash.exe -lc '<cmd>'`
- Repo MSYS path (quote the space in "GIT Clones"):
  `/c/Users/Louma/Documents/GIT Clones/QLCProjectCloneOld/qlcplus`
- `-Werror -Wextra -Wall` is ON (variables.cmake) → ANY compiler warning fails the build. New code must be warning-clean under gcc 14.1.

## One-time setup (already done on this machine)
1. MSYS2 packages: `pacman -S --needed --noconfirm mingw-w64-x86_64-qt5-websockets unzip` (websockets needed by webaccess; unzip for the D2XX step).
2. FTDI **D2XX SDK** for the `dmxusb` plugin (USER NEEDS dmxusb — uses a USB-DMX dongle). The plugin hardcodes `C:/projects/D2XXSDK`. Recipe (from .github/workflows/build.yml):
   ```
   mkdir -p /c/projects/D2XXSDK && cd /c/projects/D2XXSDK
   wget https://qlcplus.org/misc/CDM-v2.12.36.20-WHQL-Certified.zip -O cdm.zip
   unzip -o cdm.zip && cd amd64
   gendef.exe - ftd2xx64.dll > ftd2xx.def
   dlltool -k --input-def ftd2xx.def --dllname ftd2xx64.dll --output-lib libftd2xx.a
   ```
   Produces ftd2xx.h, amd64/libftd2xx.a, amd64/ftd2xx64.dll.
3. Source fix committed on `priority-rebuild` (commit 89c450132): added `#include <QDebug>` to `plugins/dmxusb/src/ftd2xx-interface.cpp` (gcc14/Qt5.15 error "incomplete type QDebug"). Build-env fix, NOT part of the priority feature.

## Build + install (repeat each iteration)
From repo root in the MinGW64 shell:
```
cmake -G Ninja -S . -B build-mingw     # configure (once; default = Qt-Widgets UI / QLC+4. -Dqmlui=ON would build QML UI — NOT wanted)
ninja  -C build-mingw                   # build (app at build-mingw/main/qlcplus.exe)
ninja  -C build-mingw install           # install to C:\qlcplus
```
`ninja install` copies app + plugins + fixtures + libusb/audio DLLs + ftd2xx64.dll, but NOT the Qt runtime.

## Make C:\qlcplus double-clickable (one-time — already done; redo only if a new Qt module is pulled in)
windeployqt is INSUFFICIENT alone: it aborts (exit 1) on "libGLESv2.dll does not exist" (this Qt uses desktop GL, not ANGLE) BEFORE copying transitive deps + the platform plugin. All three steps needed:
1. `cd /c/qlcplus && windeployqt --no-translations qlcplus.exe qlcplusui.dll qlcplusengine.dll qlcpluswebaccess.dll Plugins/osc.dll Plugins/artnet.dll Plugins/e131.dll Plugins/midiplugin.dll Plugins/dmxusb.dll`
2. Copy Qt plugin folders from `C:/msys64/mingw64/share/qt5/plugins`: `platforms` (qwindows.dll — MANDATORY) plus `styles imageformats iconengines printsupport audio mediaservice bearer generic`.
3. Copy the full non-Qt dependency closure via ldd, from `/c/qlcplus`:
   ```
   ldd qlcplus.exe *.dll Plugins/*.dll Plugins/Audio/*.dll platforms/*.dll printsupport/*.dll imageformats/*.dll iconengines/*.dll styles/*.dll audio/*.dll mediaservice/*.dll bearer/*.dll \
     | grep "=> /mingw64/bin" | awk '{print $3}' | sort -u | while read d; do cp -u "$d" /c/qlcplus/; done
   ```
   Pulls in ICU trio (libicudt75/libicuin75/libicuuc75), libpcre2-16-0, libharfbuzz, libfreetype, libglib-2.0-0, zlib1, image libs (png/jpeg/tiff/webp/brotli), Qt5PrintSupport.dll, mingw runtime (libgcc_s_seh-1/libstdc++-6/libwinpthread-1). Re-run the ldd line → "remaining /mingw64/bin deps" must be 0. ~60 DLLs total in C:\qlcplus.

## TLS web access (HTTPS/WSS) — extra runtime DLLs
The TLS feature (`--web-cert`/`--web-key`, see [[salesforce-qlcplus-integration]]) makes
QtNetwork load **OpenSSL at runtime**. These are NOT pulled by the normal closure (nothing
linked them before), so a standalone `C:\qlcplus` needs them copied explicitly or `https://`
silently fails (`QSslSocket::supportsSsl()`==false), while plain HTTP still works:
```
cp /mingw64/bin/libssl-3-x64.dll /mingw64/bin/libcrypto-3-x64.dll /c/qlcplus/
```
(Qt here is 5.15 → OpenSSL **3.x**: `libssl-3-x64.dll`, `libcrypto-3-x64.dll`.)
Verified 2026-06-19: clean-env `C:\qlcplus\qlcplus.exe -w --web-cert cert.pem --web-key key.pem`
serves HTTPS 200 + `wss://` TLSv1.3 (modules load from `C:\qlcplus`).

## Re-populating C:\qlcplus's Qt runtime the easy way
If `C:\qlcplus` is missing the Qt bundle (e.g. dir was recreated — seen 2026-06-19: only 16
DLLs, no `Qt5*.dll`/`platforms`), the user keeps a known-good standalone copy at
`C:\qlcplusTHISONEGOESTOCLUBWITHOUTHTTP` (the HTTP-less club build). Fastest fix — copy its
runtime in WITHOUT clobbering a freshly-built TLS app:
```
cp -rn /c/qlcplusTHISONEGOESTOCLUBWITHOUTHTTP/. /c/qlcplus/   # -n preserves new exe/dlls
```
Then add the OpenSSL DLLs (above). `cp -n` keeps your just-installed qlcplus.exe /
qlcpluswebaccess.dll (newer) and only fills in the missing Qt5*/plugins/mingw-runtime DLLs.

## Full rebuild from source if the deploy/build folders are lost
Everything needed to reconstruct lives in this branch:
1. Source (tracked) → build per this file (CMake/Ninja).
2. `ninja -C build-mingw install` → C:\qlcplus, then bundle the Qt runtime (from the user's
   known-good copy or windeployqt) **+ the OpenSSL DLLs** (TLS section above).
3. Deploy scripts are tracked at **`deploy/start-qlcplus.bat`** and **`deploy/setup-new-pc.bat`**
   (copy them into the install folder). Full venue/move steps: repo-root **`DEPLOYMENT.md`**.
4. Web/WSS + Salesforce contract: repo-root **`SALESFORCE_QLCPLUS_INTEGRATION.md`** +
   [[salesforce-qlcplus-integration]].
Not in git (regenerated, never committed): the TLS `cert.pem`/`key.pem` (issued by win-acme) and
the bundled Qt/OpenSSL DLLs.

## Dev iteration shortcut
After code changes, just `ninja -C build-mingw && ninja -C build-mingw install`. The bundled Qt runtime/plugins in C:\qlcplus persist, so no re-bundling needed (unless a new Qt module is introduced). Or run the exe straight from the MinGW64 shell (all DLLs/Qt plugins resolve from mingw64) — no bundling needed for quick dev runs.

## Verify a build is runnable (clean env, no mingw64 on PATH)
PowerShell: `Start-Process C:\qlcplus\qlcplus.exe -PassThru`; after ~8s check it's still running and `$p.MainWindowTitle` is "Q Light Controller Plus ...". (Don't trust "process alive" alone — a missing-DLL error dialog keeps the process alive too; check loaded modules / window title.)
