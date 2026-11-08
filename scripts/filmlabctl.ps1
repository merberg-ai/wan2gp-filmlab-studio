<#
.SYNOPSIS
  filmlabctl (Windows side): control FilmLab without touching any other distro.
.EXAMPLE
  .\filmlabctl.ps1 status | start | stop | restart | logs
  'start' also holds a hidden keep-alive wsl.exe session, because WSL stops an
  idle distro ~15 s after its last session closes even with systemd units
  running (measured 2026-11-03). Not an autostart: it lasts until 'stop' or logoff.
  'stop' stops FilmLab services, ends the keep-alive, then terminates ONLY the
  FilmLab distro. Never uses 'wsl --shutdown'.
#>
param(
    [ValidateSet('status', 'start', 'stop', 'restart', 'restart-backend', 'logs')][string]$Command = 'status',
    [ValidateSet('backend', 'gateway')][string]$Service = 'backend',
    [int]$Lines = 80
)

$StateDir = Join-Path $env:LOCALAPPDATA 'FilmLab'
# The installer records the distro's name in %LOCALAPPDATA%\FilmLab\config.json; FilmLab is the default.
$Distro = 'FilmLab'
$ConfigFile = Join-Path $StateDir 'config.json'
if (Test-Path $ConfigFile) {
    $cfg = Get-Content $ConfigFile -Raw | ConvertFrom-Json
    if ($cfg.distro) { $Distro = $cfg.distro }
}
# This checkout, wherever it lives (C:\... -> /mnt/c/...).
$RepoWin = Split-Path $PSScriptRoot -Parent
$Inner = '/mnt/' + $RepoWin.Substring(0, 1).ToLower() + $RepoWin.Substring(2).Replace('\', '/') + '/scripts/filmlabctl.sh'
$KeepAlivePid = Join-Path $StateDir 'keepalive.pid'

function Get-KeepAlive {
    if (-not (Test-Path $KeepAlivePid)) { return $null }
    Get-Process -Id ([int](Get-Content $KeepAlivePid)) -ErrorAction SilentlyContinue |
        Where-Object ProcessName -eq 'wsl'
}

function Start-KeepAlive {
    if (Get-KeepAlive) { return }
    New-Item -ItemType Directory -Force $StateDir | Out-Null
    # Created through WMI rather than Start-Process, so it is not a child of whatever ran
    # filmlabctl: closing that terminal, or an app that kills its process tree on exit,
    # no longer ends the keep-alive (and with it, ~15 s later, the whole distro).
    $hidden = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = "wsl.exe -d $Distro --exec sleep infinity"; ProcessStartupInformation = $hidden }
    if ($r.ReturnValue -ne 0) { throw "could not start the keep-alive (Win32_Process.Create returned $($r.ReturnValue))" }
    Set-Content $KeepAlivePid $r.ProcessId
}

function Stop-KeepAlive {
    $p = Get-KeepAlive
    if ($p) { Stop-Process -Id $p.Id -Force }
    Remove-Item $KeepAlivePid -ErrorAction SilentlyContinue
}

# The Windows helper (filmlab-host.ps1): finds and starts WanGP, carries out the cockpit's Startup & WanGP
# settings. Started hidden through WMI like the keep-alive, so closing a terminal doesn't end it.
$HostPid = Join-Path $StateDir 'host.pid'
$HostScript = Join-Path $PSScriptRoot 'filmlab-host.ps1'

function Get-HostHelper {
    if (-not (Test-Path $HostPid)) { return $null }
    Get-Process -Id ([int](Get-Content $HostPid)) -ErrorAction SilentlyContinue |
        Where-Object { $_.ProcessName -match 'powershell|pwsh' }
}

function Start-HostHelper {
    if (Get-HostHelper) { return }
    New-Item -ItemType Directory -Force $StateDir | Out-Null
    $hidden = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$HostScript`" -Command run"
        ProcessStartupInformation = $hidden }
    if ($r.ReturnValue -ne 0) { Write-Warning "could not start the Windows helper (returned $($r.ReturnValue))" }
}

function Stop-HostHelper {
    $p = Get-HostHelper
    if ($p) { Stop-Process -Id $p.Id -Force }
    Remove-Item $HostPid -ErrorAction SilentlyContinue
}

function Set-HostDir {
    # Tell FilmLab's server (in the distro) where the helper's folder is: %LOCALAPPDATA%\FilmLab as a WSL path.
    $dir = '/mnt/' + $StateDir.Substring(0, 1).ToLower() + $StateDir.Substring(2).Replace('\', '/')
    wsl.exe -d $Distro -- bash -c "mkdir -p ~/.config/filmlab && echo 'FILMLAB_HOST_DIR=$dir' > ~/.config/filmlab/host.env" | Out-Null
}

function Invoke-Inner([string[]]$InnerArgs) {
    wsl.exe -d $Distro -- bash $Inner @InnerArgs 2>&1 | ForEach-Object { $_ -replace "`0", '' }
}

function Get-DistroState {
    $line = (wsl.exe -l -v) -replace "`0", '' | Where-Object { $_ -match "\s$Distro\s" }
    if ($line -match '(Running|Stopped)') { $Matches[1] } else { 'Missing' }
}

switch ($Command) {
    'stop' {
        if ((Get-DistroState) -eq 'Running') {
            Invoke-Inner @('stop')
        }
        Stop-KeepAlive
        Stop-HostHelper  # WanGP keeps running: stopping FilmLab doesn't stop it
        if ((Get-DistroState) -eq 'Running') { wsl.exe --terminate $Distro | Out-Null }
        "FilmLab distro: $(Get-DistroState)"
    }
    'logs' { Invoke-Inner @('logs', $Service, "$Lines") }
    { $_ -in 'start', 'restart', 'restart-backend' } {
        Start-KeepAlive
        Set-HostDir
        Start-HostHelper  # starts WanGP too if it isn't running (Settings > Startup & WanGP)
        Invoke-Inner @($Command)
        $helper = if (Get-HostHelper) { "helper pid $((Get-HostHelper).Id)" } else { 'helper starting' }
        "FilmLab distro: $(Get-DistroState) - keep-alive pid $((Get-KeepAlive).Id) - $helper"
    }
    default {
        Invoke-Inner @($Command)
        $ka = if (Get-KeepAlive) { "keep-alive pid $((Get-KeepAlive).Id)" } else { 'no keep-alive (distro will idle-stop)' }
        "FilmLab distro: $(Get-DistroState) - $ka"
    }
}
