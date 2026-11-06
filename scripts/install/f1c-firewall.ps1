<#
.SYNOPSIS
  F1c: allow the FilmLab cockpit (TCP 19300) from the home LAN only. Run in an ELEVATED PowerShell.

.DESCRIPTION
  Adds one Hyper-V firewall rule for the WSL VM (mirrored networking):
    FilmLabStudioWSL  Inbound Allow TCP 19300, remote <-Lan>, WSL VMCreatorId, all profiles
  -WithHostRule also adds a Windows Defender Firewall rule (only if testing shows it is needed):
    FilmLab Studio LAN  Inbound Allow TCP 19300, remote <-Lan>, all profiles
  Only rules whose names start with "FilmLab" are ever created or removed; no other rule is touched.
  Every run is logged to %LOCALAPPDATA%\FilmLab\f1c-firewall.log.

.EXAMPLE
  .\f1c-firewall.ps1                 # add the Hyper-V rule (asks to confirm)
  .\f1c-firewall.ps1 -WithHostRule   # add the Hyper-V rule and the Defender rule
  .\f1c-firewall.ps1 -Remove         # rollback: delete all FilmLab rules
#>
[CmdletBinding()]
param(
    [switch]$WithHostRule,
    [switch]$Remove,
    [switch]$Yes,
    [string]$Lan = '192.168.1.0/24'   # the installer passes this PC's own subnet
)
$ErrorActionPreference = 'Stop'

$WslVmCreatorId = '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}'
$Port = 19300
$HvName = 'FilmLabStudioWSL'
$HvDisplay = 'FilmLab Studio - WSL LAN'
$HostName = 'FilmLabStudioLAN'
$HostDisplay = 'FilmLab Studio LAN'

$logDir = Join-Path $env:LOCALAPPDATA 'FilmLab'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'f1c-firewall.log'
function Log($msg) { $line = "{0:s} {1}" -f (Get-Date), $msg; Add-Content $log $line; Write-Host $msg }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Run this from an elevated PowerShell (Run as administrator).' }

function Show-State($label) {
    Log "--- $label"
    Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'FilmLab*' } |
        ForEach-Object { Log ("  hyper-v  {0} | {1} {2} {3} | remote {4} | enabled {5}" -f $_.Name, $_.Action, $_.Protocol, $_.LocalPorts, $_.RemoteAddresses, $_.Enabled) }
    Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like 'FilmLab*' } |
        ForEach-Object {
            $p = $_ | Get-NetFirewallPortFilter; $a = $_ | Get-NetFirewallAddressFilter
            Log ("  defender {0} | {1} {2} {3} | remote {4} | profile {5}" -f $_.DisplayName, $_.Action, $p.Protocol, $p.LocalPort, ($a.RemoteAddress -join ','), $_.Profile)
        }
}

function Confirm-Step($what) {
    if ($Yes) { return }
    $answer = Read-Host "$what  Proceed? [y/N]"
    if ($answer -notmatch '^(y|yes)$') { Log 'aborted by user'; exit 1 }
}

Show-State 'before'

if ($Remove) {
    Confirm-Step 'Remove all FilmLab firewall rules (Hyper-V and Defender).'
    Get-NetFirewallHyperVRule -ErrorAction SilentlyContinue | Where-Object Name -like 'FilmLab*' |
        ForEach-Object { Remove-NetFirewallHyperVRule -Name $_.Name; Log "removed hyper-v rule $($_.Name)" }
    Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object Name -like 'FilmLab*' |
        ForEach-Object { Remove-NetFirewallRule -Name $_.Name; Log "removed defender rule $($_.Name)" }
    Show-State 'after'
    exit 0
}

Write-Host ''
Write-Host "Will add Hyper-V rule  $HvName : Inbound Allow TCP $Port from $Lan (WSL VM, all profiles)"
if ($WithHostRule) { Write-Host "Will add Defender rule $HostName : Inbound Allow TCP $Port from $Lan (all profiles)" }
Confirm-Step 'Add the rule(s) above.'

if (Get-NetFirewallHyperVRule -Name $HvName -ErrorAction SilentlyContinue) {
    Log "hyper-v rule $HvName already exists; leaving it"
} else {
    New-NetFirewallHyperVRule -Name $HvName -DisplayName $HvDisplay -Direction Inbound `
        -VMCreatorId $WslVmCreatorId -Protocol TCP -LocalPorts $Port -RemoteAddresses $Lan `
        -Action Allow -Enabled True | Out-Null
    Log "added hyper-v rule $HvName"
}

if ($WithHostRule) {
    if (Get-NetFirewallRule -Name $HostName -ErrorAction SilentlyContinue) {
        Log "defender rule $HostName already exists; leaving it"
    } else {
        New-NetFirewallRule -Name $HostName -DisplayName $HostDisplay -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $Port -RemoteAddress $Lan -Profile Any | Out-Null
        Log "added defender rule $HostName"
    }
}

Show-State 'after'
Log "log: $log"
