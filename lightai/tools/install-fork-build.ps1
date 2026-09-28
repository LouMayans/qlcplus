# Installs the lightai QLC+ fork binaries (loadProjectFile / saveProject / setFunctionSpeed ...)
# and the engine fixes (EFX movement with BEAM230 V3s in the look; P100 kills that really hold dimmers at 0)
# into an existing QLC+ install. Run it when the club is closed:
#   powershell -ExecutionPolicy Bypass -File lightai\tools\install-fork-build.ps1 [-Target C:\qlcplus]
# It refuses to run while QLC+ is open and keeps a backup of the replaced files.
param(
    [string]$Target = 'C:\qlcplus',
    [string]$Build = (Join-Path $PSScriptRoot '..\..\build-mingw')
)
$ErrorActionPreference = 'Stop'
$files = @(
    @{ src = 'main\qlcplus.exe'; dst = 'qlcplus.exe' },
    @{ src = 'engine\src\qlcplusengine.dll'; dst = 'qlcplusengine.dll' },
    @{ src = 'ui\src\qlcplusui.dll'; dst = 'qlcplusui.dll' },
    @{ src = 'webaccess\src\qlcpluswebaccess.dll'; dst = 'qlcpluswebaccess.dll' }
)
$running = Get-Process qlcplus -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$Target*" }
if ($running) { throw "QLC+ is running from $Target (PID $($running.Id -join ', ')). Save and close it first." }
foreach ($f in $files) {
    $s = Join-Path $Build $f.src
    if (-not (Test-Path $s)) { throw "missing build output $s (build with: ninja -C build-mingw -k 0 main/qlcplus.exe)" }
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $Target "backup-before-lightai-fork-$stamp"
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($f in $files) {
    $d = Join-Path $Target $f.dst
    if (Test-Path $d) { Copy-Item $d $backup }
    Copy-Item (Join-Path $Build $f.src) $d -Force
    Write-Output "installed $($f.dst)"
}
Write-Output "backup of the previous files: $backup"
Write-Output "start QLC+ as usual; lightai detects the fork (QLC+API|lightaiVersion) automatically."
Write-Output "to undo: close QLC+ and copy the files from $backup back into $Target"
