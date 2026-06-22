<#
.SYNOPSIS
  The verify/iterate loop in one command: start a QLC+ function, let it settle, capture a burst on
  camera, stop it (unless -LeaveOn), and print the frame paths. Open the frames to judge the look
  against intent, then edit the .qxw and re-run. QLC+ must run with -w.
.EXAMPLE
  .\observe-function.ps1 -Id 45                       # trigger 45, capture 6s burst, stop, list frames
  .\observe-function.ps1 -Id 80 -Seconds 8 -Fps 4 -LeaveOn
#>
param(
    [Parameter(Mandatory)][int]$Id,
    [int]$Seconds,
    [int]$Fps,
    [string]$OutDir,
    [int]$SettleMs,
    [switch]$LeaveOn,
    [switch]$Insecure
)

. "$PSScriptRoot/_common.ps1"

$cfg = Get-RigConfig
if (-not $SettleMs) { $SettleMs = [int]$cfg.settleMs; if (-not $SettleMs) { $SettleMs = 600 } }
if (-not $OutDir)   { $OutDir = Join-Path ($cfg.outDir) ("fn-$Id") }

Write-Host "Triggering function $Id ..."
Start-QlcFunction -Id $Id -Insecure:$Insecure
Start-Sleep -Milliseconds $SettleMs

try {
    $frames = Invoke-RigCapture -Seconds $Seconds -Fps $Fps -OutDir $OutDir -Clear
}
finally {
    if (-not $LeaveOn) { Stop-QlcFunction -Id $Id -Insecure:$Insecure; Write-Host "Stopped function $Id." }
    else { Write-Host "Left function $Id running." }
}

if (-not $frames) { Write-Host "No frames captured."; return }
Write-Host ("Captured {0} frame(s) for function {1}:" -f @($frames).Count, $Id)
$frames | ForEach-Object { Write-Output $_ }
