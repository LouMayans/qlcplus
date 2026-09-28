@echo off
REM ============================================================
REM  Launch QLC+ with web access. Portable: uses files in THIS
REM  folder, so it works no matter where the folder is copied.
REM
REM  Looks for (all optional, in this same folder):
REM    cert.pem / key.pem ............. enable HTTPS/WSS (else plain HTTP)
REM    webpass.txt ................... enable login: -wa turns auth ON, -a names the file
REM    show-path.txt ................. one line: the full path of the show to open instead,
REM       e.g. the git repo's SaveFile\Main Project.qxw, so QLC+ and lightai use the same file
REM    SaveFile\Main Project.qxw .... the project's canonical show, auto-loaded
REM       (falls back to a "Main Project.qxw" beside this script, then show.qxw)
REM ============================================================
setlocal
cd /d "%~dp0"

set "EXTRA="
if exist "%~dp0webpass.txt" set "EXTRA=%EXTRA% -wa -a "%~dp0webpass.txt""
set "SHOW="
if exist "%~dp0show-path.txt" set /p SHOW=<"%~dp0show-path.txt"
if defined SHOW if not exist "%SHOW%" (
    echo show-path.txt names a missing file: "%SHOW%" - opening the local show instead
    set "SHOW="
)
if not defined SHOW if exist "%~dp0SaveFile\Main Project.qxw" set "SHOW=%~dp0SaveFile\Main Project.qxw"
if not defined SHOW if exist "%~dp0Main Project.qxw" set "SHOW=%~dp0Main Project.qxw"
if not defined SHOW if exist "%~dp0show.qxw" set "SHOW=%~dp0show.qxw"
if defined SHOW set "EXTRA=%EXTRA% -o "%SHOW%""

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
