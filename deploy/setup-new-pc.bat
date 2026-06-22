@echo off
REM ============================================================
REM  One-time setup on a NEW PC. RIGHT-CLICK > "Run as administrator".
REM  Opens the Windows Firewall so the web/WSS port (9999) and the
REM  Let's Encrypt validation port (80) can be reached from outside.
REM ============================================================
echo Adding Windows Firewall inbound rules...
netsh advfirewall firewall add rule name="QLC+ WebAccess 9999" dir=in action=allow protocol=TCP localport=9999
netsh advfirewall firewall add rule name="ACME HTTP 80"        dir=in action=allow protocol=TCP localport=80
echo.
echo === TLS certificate (Let's Encrypt via win-acme) ===
REM  Issues/refreshes the cert for lights.mayansvip.com straight into THIS folder
REM  and registers the auto-renewal to keep writing here -- so moving the folder
REM  and re-running this script automatically re-points the renewal.
REM  win-acme ships bundled in this folder as "win-acme\" (copied here by the
REM  build/install), so it always travels with the deployment. Needs DNS + port 80
REM  reachable at the time you run it.
set "FOLDER=%~dp0"
set "FOLDER=%FOLDER:~0,-1%"
set "WACS=%~dp0win-acme\wacs.exe"
if not exist "%WACS%" (
    echo   win-acme is missing from this folder ^("win-acme\wacs.exe"^).
    echo   It should have been bundled by the build/install. Re-run
    echo   "ninja -C build-mingw install" or copy the win-acme\ folder in here,
    echo   then re-run this script.
) else (
    echo   Running win-acme ^(PEM output -^> "%FOLDER%"^)...
    "%WACS%" --source manual --host lights.mayansvip.com --validation selfhosting --store pemfiles --pemfilespath "%FOLDER%" --installation none --friendlyname lights.mayansvip.com --accepttos --emailaddress loumayans@mayansvip.com
)
echo.
echo Setting the win-acme renewal task to run after a missed start (e.g. PC was off)...
powershell -NoProfile -Command "$t=Get-ScheduledTask -TaskName '*win-acme*' -ErrorAction SilentlyContinue; if($t){ $t.Settings.StartWhenAvailable=$true; Set-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Settings $t.Settings | Out-Null; Write-Host '  win-acme task updated (run-if-missed enabled).' } else { Write-Host '  win-acme task not found yet - run win-acme first, then re-run this.' }"
echo.
echo Done. If you saw "Access is denied", re-run this as Administrator.
pause
