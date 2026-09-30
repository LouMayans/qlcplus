"""Open the lightai booth console in Chrome, starting the lightai server first when it isn't running.

The desktop and taskbar shortcuts run this with pythonw.exe (so it has no window of its own). The server starts in its
own minimized console window titled 'lightai server ... close this window to stop it'. Pass a path to open another
page, e.g. `open_lightai.pyw /stage` for the 3D stage.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LIGHTAI = Path(__file__).resolve().parents[1]
PYTHON = Path(r"C:\lightai-env\venv\Scripts\python.exe")
URL = "http://127.0.0.1:8765"
CHROMES = [Path(os.environ.get(v, "")) / "Google" / "Chrome" / "Application" / "chrome.exe"
           for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
SW_SHOWMINNOACTIVE = 7


def running() -> bool:
    try:
        with urllib.request.urlopen(URL + "/health", timeout=1.5):
            return True
    except OSError:
        return False


def message(text: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, "lightai", 0x40)


def start_server() -> None:
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWMINNOACTIVE  # minimized, and the console keeps the focus
    subprocess.Popen([str(PYTHON), "-m", "lightai", "serve"], cwd=str(LIGHTAI), creationflags=subprocess.CREATE_NEW_CONSOLE,
                     startupinfo=si, close_fds=True)


def maximize(title_part: str, timeout: float = 15.0) -> bool:
    """Maximize the browser window showing the console (a normal window: tabs and the address bar stay). A Chrome
    that is already running ignores --start-maximized for a new window, so the window itself is maximized."""
    user32 = ctypes.windll.user32
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found: list = []

    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_part in buf.value:
                found.append(hwnd)
        return True

    t0 = time.time()
    while time.time() - t0 < timeout:
        found.clear()
        user32.EnumWindows(proc(visit), 0)
        if found:
            for hwnd in found:
                user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            return True
        time.sleep(0.3)
    return False


def open_page(url: str) -> None:
    chrome = next((c for c in CHROMES if c.is_file()), None)
    if chrome is None:
        os.startfile(url)  # no Chrome: the default browser
    else:
        subprocess.Popen([str(chrome), "--new-window", "--start-maximized", url], close_fds=True)
    maximize("lightai booth console" if url.rstrip("/").endswith(":8765") else "3D Stage")


def main() -> int:
    page = sys.argv[1] if len(sys.argv) > 1 else "/"
    if not running():
        if not PYTHON.is_file():
            message(f"lightai's Python environment is missing:\n{PYTHON}\n\nSee lightai\\README.md, 'Setup'.")
            return 1
        start_server()
        t0 = time.time()
        while not running():  # loading the language model takes a few seconds
            if time.time() - t0 > 90:
                message("The lightai server didn't start within 90 seconds.\n\nLook at the 'lightai server' window on the "
                        "taskbar for the error, or run tools\\start-lightai.bat to see it.")
                return 1
            time.sleep(0.5)
    open_page(URL + (page if page.startswith("/") else "/" + page))
    return 0


if __name__ == "__main__":
    sys.exit(main())
