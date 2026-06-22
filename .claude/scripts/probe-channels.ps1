<#
.SYNOPSIS
  Discovery engine for "learn venue": sweep a range of DMX channels one at a time, capturing the
  rig on camera at each state, so you can see what is physically connected to what. Mechanical
  capture only - inferring fixtures/positions/channel meaning and authoring .qxf files is done by
  reading the resulting frames. QLC+ must run with -w.

  For each channel in [From,To] it blacks out the universe, sets the channel to each value in
  -Values, settles, and grabs a still. A manifest maps every (channel,value) -> frame.

.EXAMPLE
  .\probe-channels.ps1 -Universe 0 -From 0 -To 15                       # what responds, full-on
  .\probe-channels.ps1 -Universe 0 -From 0 -To 0 -Values 0,64,128,192,255   # map one channel's range
  .\probe-channels.ps1 -Universe 0 -From 0 -To 31 -DryRun              # print the plan only
#>
param(
    [int]$Universe = 0,
    [Parameter(Mandatory)][int]$From,
    [Parameter(Mandatory)][int]$To,
    [int[]]$Values = @(255),
    [int]$SettleMs = 700,
    [string]$OutDir,
    [switch]$DryRun,
    [switch]$Insecure
)

. "$PSScriptRoot/_common.ps1"

$cfg = Get-RigConfig
if (-not $OutDir) { $OutDir = Join-Path ($cfg.outDir) ("probe-u$Universe") }
$absRoot = Resolve-RigPath $OutDir
New-Item -ItemType Directory -Force -Path $absRoot | Out-Null

Write-Host "Probe plan: universe $Universe, channels $From..$To, values [$($Values -join ',')], settle ${SettleMs}ms."
Write-Host "WARNING: this drives raw DMX directly. Movers may slew and lamps may strike. Make sure that's safe."
if ($DryRun) { Write-Host "(dry run - no DMX sent, no capture)"; return }

$manifest = [System.Collections.Generic.List[object]]::new()
try {
    for ($ch = $From; $ch -le $To; $ch++) {
        Reset-QlcUniverse -Universe $Universe -Insecure:$Insecure
        foreach ($v in $Values) {
            Set-QlcChannel -Universe $Universe -Channel $ch -Value $v -Insecure:$Insecure
            Start-Sleep -Milliseconds $SettleMs
            $stateDir = Join-Path $absRoot ("u{0}_ch{1:000}_v{2:000}" -f $Universe, $ch, $v)
            $frame = (Invoke-RigCapture -OutDir $stateDir -Single -Clear)
            $abs = $Universe * 512 + $ch + 1
            $manifest.Add([pscustomobject]@{ universe=$Universe; channel=$ch; absAddr=$abs; value=$v; frame=@($frame)[0] })
            Write-Host ("  ch{0,3} = {1,3}  ->  {2}" -f $ch, $v, @($frame)[0])
        }
    }
}
finally {
    Reset-QlcUniverse -Universe $Universe -Insecure:$Insecure
}

$manifestPath = Join-Path $absRoot 'manifest.json'
($manifest | ConvertTo-Json -Depth 5) | Set-Content -Encoding utf8 $manifestPath
Write-Host "`nDone. Manifest: $manifestPath"
Write-Host "Open the listed frames to infer which fixtures/attributes each channel drives, then record findings in the venue's observed.md and author any missing .qxf."
