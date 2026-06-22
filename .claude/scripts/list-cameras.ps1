<#
.SYNOPSIS
  List the DirectShow video devices ffmpeg can capture from, and (optionally) record the chosen
  one into the per-PC rig-capture.local.json. Run this once on each machine.
.EXAMPLE
  .\list-cameras.ps1                      # list available video device names
  .\list-cameras.ps1 -Set "USB Video"     # save that device as this PC's camera
#>
param([string]$Set)

. "$PSScriptRoot/_common.ps1"

$ffmpeg = Resolve-RigTool -Kind ffmpeg
$tmp = [IO.Path]::GetTempFileName()
try {
    Start-Process -FilePath $ffmpeg `
        -ArgumentList @('-hide_banner','-list_devices','true','-f','dshow','-i','dummy') `
        -NoNewWindow -Wait -RedirectStandardError $tmp
    $err = Get-Content -Raw $tmp
} finally { Remove-Item $tmp -ErrorAction SilentlyContinue }

$names = [System.Collections.Generic.List[string]]::new()
foreach ($line in ($err -split "`r?`n")) {
    if ($line -match '"([^"]+)"\s*\(video\)') { $names.Add($Matches[1]) }
}

if ($names.Count -eq 0) {
    Write-Host "No DirectShow VIDEO devices found by ffmpeg. Plug in the webcam/capture card and retry."
    Write-Host "Raw ffmpeg device dump:`n$err"
    return
}

Write-Host "Video capture devices:"
$i = 0
foreach ($n in $names) { Write-Host ("  [{0}] {1}" -f $i, $n); $i++ }

if ($Set) {
    Set-RigLocal -Name 'camera' -Value $Set
} elseif ($names.Count -eq 1) {
    Set-RigLocal -Name 'camera' -Value $names[0]
} else {
    Write-Host "`nMultiple cameras found. Save one with:  .\list-cameras.ps1 -Set `"<exact name above>`""
}
