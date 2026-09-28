@echo off
REM Start the lightai booth console (http://127.0.0.1:8765) next to QLC+.
REM Double-click, or run from a terminal. Close this window to stop it.
setlocal
cd /d "%~dp0.."
set "PY=C:\lightai-env\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo lightai's Python environment is missing: %PY%
    echo See lightai\README.md "Setup".
    pause
    exit /b 1
)
REM open the console once the server answers (loading the model takes a few seconds)
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 120;$i++){ try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/health -TimeoutSec 1 | Out-Null; break } catch { Start-Sleep -Milliseconds 500 } }; Start-Process http://127.0.0.1:8765/"
"%PY%" -m lightai serve %*
endlocal
