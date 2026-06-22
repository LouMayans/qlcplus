<#
.SYNOPSIS
  Set a raw DMX channel directly via the QLC+ Simple Desk over the WebSocket API
  (CH|<absAddr 1-based>|<value>), or blackout a universe (-Reset). This is the primitive for
  testing what a channel physically does without authoring a scene. QLC+ must run with -w.

  Absolute address = Universe(0-based)*512 + Channel(0-based) + 1.
.EXAMPLE
  .\set-channel.ps1 -Universe 0 -Channel 5 -Value 255    # U1 ch5 (0-based) full
  .\set-channel.ps1 -Abs 9 -Value 128                    # absolute DMX address 9 -> 128
  .\set-channel.ps1 -Reset -Universe 0                   # clear all Simple Desk values on U1
#>
param(
    [int]$Universe = 0,
    [int]$Channel  = -1,
    [int]$Abs      = -1,
    [ValidateRange(0,255)][int]$Value,
    [switch]$Reset,
    [switch]$Insecure
)

. "$PSScriptRoot/_common.ps1"

if ($Reset) {
    Reset-QlcUniverse -Universe $Universe -Insecure:$Insecure
    Write-Host "Simple Desk reset on universe $Universe (DMX blackout of overrides)."
    return
}

if (-not $PSBoundParameters.ContainsKey('Value')) { throw "Provide -Value 0-255 (or -Reset)." }

Set-QlcChannel -Universe $Universe -Channel $Channel -Abs $Abs -Value $Value -Insecure:$Insecure
if ($Abs -ge 1) { Write-Host "CH abs $Abs -> $Value" }
else            { Write-Host "U$Universe ch$Channel (abs $($Universe*512 + $Channel + 1)) -> $Value" }
