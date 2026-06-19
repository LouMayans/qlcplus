@echo off
REM ============================================================
REM  Launch QLC+ with web access. Portable: uses files in THIS
REM  folder, so it works no matter where the folder is copied.
REM
REM  Looks for (all optional, in this same folder):
REM    cert.pem / key.pem ... enable HTTPS/WSS (else plain HTTP)
REM    webpass.txt ......... enable login (recommended when public)
REM    Main Project.qxw .... project to auto-load (falls back to show.qxw)
REM ============================================================
setlocal
cd /d "%~dp0"

set "EXTRA="
if exist "%~dp0webpass.txt" set "EXTRA=%EXTRA% -a "%~dp0webpass.txt""
if exist "%~dp0Main Project.qxw" (
    set "EXTRA=%EXTRA% -o "%~dp0Main Project.qxw""
) else if exist "%~dp0show.qxw" (
    set "EXTRA=%EXTRA% -o "%~dp0show.qxw""
)

REM Prefer win-acme's PEM output (auto-refreshed on every renewal); QLC+ reloads
REM it live, so renewals need no restart. Fall back to cert.pem/key.pem.
set "CERT=%~dp0cert.pem"
set "KEY=%~dp0key.pem"
if exist "%~dp0lights.mayansvip.com-chain.pem" if exist "%~dp0lights.mayansvip.com-key.pem" (
    set "CERT=%~dp0lights.mayansvip.com-chain.pem"
    set "KEY=%~dp0lights.mayansvip.com-key.pem"
)

if exist "%CERT%" if exist "%KEY%" (
    echo Starting QLC+ with HTTPS/WSS on port 9999 ...
    start "" "%~dp0qlcplus.exe" -w -p --web-cert "%CERT%" --web-key "%KEY%"%EXTRA%
    goto :eof
)

echo No cert/key found - starting PLAIN HTTP on port 9999 ...
start "" "%~dp0qlcplus.exe" -w -p%EXTRA%
:eof
endlocal
