<#
.SYNOPSIS
  Start or stop a QLC+ function by its ID over the Web Access WebSocket API
  (QLC+API|setFunctionStatus|<id>|<1|0>). QLC+ must be running with -w.
.EXAMPLE
  .\trigger-function.ps1 -Id 45                # start function 45
  .\trigger-function.ps1 -Id 45 -State off     # stop it
  .\trigger-function.ps1 -List                 # print id|name|type for every function
#>
param(
    [int]$Id,
    [ValidateSet('on','off')][string]$State = 'on',
    [switch]$List,
    [switch]$Insecure
)

. "$PSScriptRoot/_common.ps1"

if ($List) {
    $reply = Get-QlcFunctions -Insecure:$Insecure
    if (-not $reply) { Write-Host "No reply (is QLC+ running with -w and a project loaded?)"; return }
    # reply: QLC+API|getFunctionsList|<id>|<name>|<type>|<id>|<name>|<type>|...
    $parts = $reply -split '\|'
    for ($i = 2; $i + 2 -lt $parts.Count; $i += 3) {
        Write-Output ("{0}`t{1}`t{2}" -f $parts[$i], $parts[$i+1], $parts[$i+2])
    }
    return
}

if (-not $PSBoundParameters.ContainsKey('Id')) { throw "Provide -Id <functionID> (or -List)." }

if ($State -eq 'on') { Start-QlcFunction -Id $Id -Insecure:$Insecure }
else                 { Stop-QlcFunction  -Id $Id -Insecure:$Insecure }
Write-Host "Function $Id -> $State"
