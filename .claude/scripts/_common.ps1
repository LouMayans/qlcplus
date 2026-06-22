# Shared helpers for the lightshow camera/control scripts.
# Portable by design: no hardcoded machine paths. Repo root is derived from this script's
# location, tools are resolved from PATH (with optional config overrides), and machine-specific
# values live in the gitignored rig-capture.local.json. Dot-source this from the other scripts:
#   . "$PSScriptRoot/_common.ps1"
# Compatible with Windows PowerShell 5.1 and PowerShell 7+.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RigRoot       = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:RigScriptsDir = $PSScriptRoot
$script:RigConfigPath = Join-Path $PSScriptRoot 'rig-capture.config.json'
$script:RigLocalPath  = Join-Path $PSScriptRoot 'rig-capture.local.json'

function Get-Prop {
    # StrictMode-safe property read: returns $Default when the object or property is absent
    # (plain $obj.prop / $obj.PSObject.Properties['x'].Value would throw under Set-StrictMode).
    param($Obj, [Parameter(Mandatory)][string]$Name, $Default = $null)
    if ($null -eq $Obj) { return $Default }
    $p = $Obj.PSObject.Properties[$Name]
    if ($null -eq $p) { return $Default }
    return $p.Value
}

function Resolve-RigPath {
    # Turn a repo-relative path (as stored in config) into an absolute path under the repo.
    param([Parameter(Mandatory)][string]$Path)
    if ([IO.Path]::IsPathRooted($Path)) { return $Path }
    return (Join-Path $script:RigRoot $Path)
}

function Get-RigConfig {
    # Tracked base config, with the gitignored local overrides layered on top (local wins).
    if (-not (Test-Path $script:RigConfigPath)) {
        throw "Missing $script:RigConfigPath. This file is tracked in git and should be present."
    }
    $cfg = Get-Content -Raw $script:RigConfigPath | ConvertFrom-Json
    if (Test-Path $script:RigLocalPath) {
        $local = Get-Content -Raw $script:RigLocalPath | ConvertFrom-Json
        foreach ($p in $local.PSObject.Properties) {
            if ($null -ne $p.Value -and "$($p.Value)" -ne '') {
                $cfg | Add-Member -NotePropertyName $p.Name -NotePropertyValue $p.Value -Force
            }
        }
    }
    return $cfg
}

function Set-RigLocal {
    # Persist a key into the per-PC, gitignored local override file (creating it if needed).
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)]$Value)
    $local = [ordered]@{}
    if (Test-Path $script:RigLocalPath) {
        $existing = Get-Content -Raw $script:RigLocalPath | ConvertFrom-Json
        foreach ($p in $existing.PSObject.Properties) { $local[$p.Name] = $p.Value }
    }
    $local[$Name] = $Value
    ($local | ConvertTo-Json -Depth 6) | Set-Content -Encoding utf8 $script:RigLocalPath
    Write-Host "Saved '$Name' to $($script:RigLocalPath) (gitignored, per-PC)."
}

function Resolve-RigTool {
    # ffmpeg / qlcplus: explicit config override -> PATH -> common fallback locations -> error.
    param([Parameter(Mandatory)][ValidateSet('ffmpeg','qlcplus')][string]$Kind)
    $cfg = Get-RigConfig
    $exe = if ($Kind -eq 'ffmpeg') { 'ffmpeg' } else { 'qlcplus' }
    $override = Get-Prop $cfg $Kind
    if ($override -and (Test-Path $override)) { return (Resolve-Path $override).Path }

    $cmd = Get-Command $exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }

    $fallbacks = @()
    if ($Kind -eq 'ffmpeg') {
        $fallbacks = @(
            (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\ffmpeg.exe'),
            'C:\ffmpeg\bin\ffmpeg.exe'
        )
    } else {
        $fallbacks = @('C:\qlcplus\qlcplus.exe', (Join-Path $env:ProgramFiles 'QLC+\qlcplus.exe'))
    }
    foreach ($f in $fallbacks) { if ($f -and (Test-Path $f)) { return $f } }
    throw "Could not locate '$exe'. Put it on PATH, or set `"$Kind`" to its full path in rig-capture.local.json."
}

function Get-RigCamera {
    $cfg = Get-RigConfig
    $cam = Get-Prop $cfg 'camera'
    if (-not $cam) {
        throw "No camera configured. Run .\list-cameras.ps1 -Set '<device name>' to record this PC's camera in rig-capture.local.json."
    }
    return $cam
}

function Get-ActiveVenue {
    $cfg = Get-RigConfig
    $slug = $cfg.activeVenue
    $venue = Get-Prop (Get-Prop $cfg 'venues') $slug
    $venueQxw = Get-Prop $venue 'qxw'
    $qxw = if ($venueQxw) { Resolve-RigPath $venueQxw } else { $null }
    return [pscustomobject]@{
        Slug = $slug
        Qxw  = $qxw
        Dir  = (Join-Path $script:RigRoot ".claude/memory/lighting/venues/$slug")
    }
}

# ---------------------------------------------------------------------------
# QLC+ Web Access WebSocket control (source of truth: webaccess/src/webaccess.cpp).
# Endpoint: ws://<host>:9999/qlcplusWS (wss:// with TLS). QLC+ must run with -w.
# ---------------------------------------------------------------------------

function Send-QlcWs {
    # Send one or more pipe-delimited frames; optionally wait for and return one reply.
    param(
        [Parameter(Mandatory)][string[]]$Messages,
        [string]$Url,
        [switch]$Insecure,
        [int]$ReceiveMs = 0
    )
    $cfg = Get-RigConfig
    if (-not $Url) { $Url = Get-Prop $cfg 'wsUrl' }
    if (-not $Url) { $Url = 'ws://localhost:9999/qlcplusWS' }
    if (-not $Insecure -and (Get-Prop $cfg 'insecure' $false)) { $Insecure = [bool](Get-Prop $cfg 'insecure' $false) }

    if ($Insecure) { [System.Net.ServicePointManager]::ServerCertificateValidationCallback = { $true } }

    $ws = [System.Net.WebSockets.ClientWebSocket]::new()
    $auth = Get-Prop $cfg 'auth'
    $authUser = Get-Prop $auth 'user'
    if ($authUser) {
        $authPass = Get-Prop $auth 'pass' ''
        $token = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("${authUser}:${authPass}"))
        $ws.Options.SetRequestHeader('Authorization', "Basic $token")
    }
    $ct = [System.Threading.CancellationToken]::None
    try {
        $ws.ConnectAsync([Uri]$Url, $ct).GetAwaiter().GetResult()
        foreach ($m in $Messages) {
            $bytes = [Text.Encoding]::UTF8.GetBytes($m)
            $seg = [System.ArraySegment[byte]]::new($bytes)
            $ws.SendAsync($seg, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $ct).GetAwaiter().GetResult() | Out-Null
        }
        $reply = $null
        if ($ReceiveMs -gt 0) {
            $buf = [byte[]]::new(131072)
            $seg = [System.ArraySegment[byte]]::new($buf)
            $task = $ws.ReceiveAsync($seg, $ct)
            if ($task.Wait($ReceiveMs)) {
                $res = $task.GetAwaiter().GetResult()
                $reply = [Text.Encoding]::UTF8.GetString($buf, 0, $res.Count)
            }
        }
        return $reply
    }
    catch {
        throw "WebSocket send to '$Url' failed: $($_.Exception.Message). Is QLC+ running with -w?"
    }
    finally {
        try { $ws.CloseAsync([System.Net.WebSockets.WebSocketCloseStatus]::NormalClosure, '', $ct).GetAwaiter().GetResult() | Out-Null } catch {}
        $ws.Dispose()
    }
}

function Start-QlcFunction { param([Parameter(Mandatory)][int]$Id, [switch]$Insecure)
    Send-QlcWs -Messages "QLC+API|setFunctionStatus|$Id|1" -Insecure:$Insecure | Out-Null }

function Stop-QlcFunction  { param([Parameter(Mandatory)][int]$Id, [switch]$Insecure)
    Send-QlcWs -Messages "QLC+API|setFunctionStatus|$Id|0" -Insecure:$Insecure | Out-Null }

function Get-QlcFunctions  { param([switch]$Insecure)
    Send-QlcWs -Messages 'QLC+API|getFunctionsList' -ReceiveMs 4000 -Insecure:$Insecure }

function Reset-QlcUniverse { param([int]$Universe = 0, [switch]$Insecure)
    # sdResetUniverse expects a 1-based universe index.
    Send-QlcWs -Messages "QLC+API|sdResetUniverse|$($Universe + 1)" -Insecure:$Insecure | Out-Null }

function Set-QlcChannel {
    # Set a raw DMX channel via Simple Desk. Provide -Universe (0-based) + -Channel (0-based),
    # or -Abs (1-based absolute address). abs = Universe*512 + Channel + 1.
    param(
        [int]$Universe = 0,
        [int]$Channel  = -1,
        [int]$Abs      = -1,
        [Parameter(Mandatory)][ValidateRange(0,255)][int]$Value,
        [switch]$Insecure
    )
    if ($Abs -lt 1) {
        if ($Channel -lt 0) { throw "Provide -Channel (0-based) with -Universe, or an -Abs address." }
        $Abs = $Universe * 512 + $Channel + 1
    }
    Send-QlcWs -Messages "CH|$Abs|$Value" -Insecure:$Insecure | Out-Null
}

# ---------------------------------------------------------------------------
# Camera capture (ffmpeg DirectShow). Frames land under the gitignored scratch dir.
# ---------------------------------------------------------------------------

function Invoke-RigCapture {
    # Capture a burst (default) or a single still from the configured camera. Returns frame paths.
    param(
        [int]$Seconds,
        [int]$Fps,
        [string]$OutDir,
        [string]$Camera,
        [switch]$Single,
        [switch]$Clear
    )
    $cfg = Get-RigConfig
    if (-not $Seconds) { $Seconds = [int]$cfg.seconds; if (-not $Seconds) { $Seconds = 6 } }
    if (-not $Fps)     { $Fps     = [int]$cfg.fps;     if (-not $Fps)     { $Fps     = 3 } }
    if (-not $OutDir)  { $OutDir  = $cfg.outDir;       if (-not $OutDir)  { $OutDir  = '.claude/scratch/captures' } }
    if (-not $Camera)  { $Camera  = Get-RigCamera }

    $ffmpeg = Resolve-RigTool -Kind ffmpeg
    $abs = Resolve-RigPath $OutDir
    New-Item -ItemType Directory -Force -Path $abs | Out-Null
    if ($Clear) { Get-ChildItem -Path $abs -Filter 'frame_*.png' -ErrorAction SilentlyContinue | Remove-Item -Force }

    $pattern = Join-Path $abs 'frame_%03d.png'
    if ($Single) {
        $ffArgs = @('-hide_banner','-loglevel','error','-nostdin','-y','-f','dshow','-i',"video=$Camera",'-frames:v','1', (Join-Path $abs 'frame_001.png'))
    } else {
        $ffArgs = @('-hide_banner','-loglevel','error','-nostdin','-y','-f','dshow','-i',"video=$Camera",'-t',"$Seconds",'-vf',"fps=$Fps", $pattern)
    }
    & $ffmpeg @ffArgs
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg capture failed (exit $LASTEXITCODE). Check the camera name with .\list-cameras.ps1." }

    $frames = Get-ChildItem -Path $abs -Filter 'frame_*.png' | Sort-Object Name | ForEach-Object { $_.FullName }
    return $frames
}
