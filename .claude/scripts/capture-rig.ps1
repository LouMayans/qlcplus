<#
.SYNOPSIS
  Capture frames of the rig from the configured camera with ffmpeg and print the frame paths
  (one per line) so they can be opened/analyzed. Captures a short burst by default so movement
  and chases are visible across frames; -Single grabs one still.
.EXAMPLE
  .\capture-rig.ps1                                   # 6s burst at config fps -> scratch dir
  .\capture-rig.ps1 -Seconds 4 -Fps 4 -OutDir .claude/scratch/captures/test
  .\capture-rig.ps1 -Single -OutDir .claude/scratch/captures/blackout-ref
#>
param(
    [int]$Seconds,
    [int]$Fps,
    [string]$OutDir,
    [string]$Camera,
    [switch]$Single,
    [switch]$Clear
)

. "$PSScriptRoot/_common.ps1"

$frames = Invoke-RigCapture -Seconds $Seconds -Fps $Fps -OutDir $OutDir -Camera $Camera -Single:$Single -Clear:$Clear
if (-not $frames) { Write-Host "No frames captured."; return }
Write-Host ("Captured {0} frame(s):" -f @($frames).Count)
$frames | ForEach-Object { Write-Output $_ }
