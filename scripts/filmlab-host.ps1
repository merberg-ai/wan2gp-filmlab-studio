<#
.SYNOPSIS
  FilmLab's Windows helper: finds WanGP, starts it when FilmLab needs it, and carries out what the cockpit's
  Settings > Startup & WanGP asks for.
.DESCRIPTION
  FilmLab's server runs in its WSL distro with Windows interop turned off, so it can't start Windows programs.
  This helper runs on the Windows side (filmlabctl start launches it, hidden, one at a time) and:
    - finds WanGP without fixed paths: the WanGP process that's running, the folder you chose in Settings,
      the folder saved before, or the usual places (each drive's Wan2GP / WanGP, your profile, Pinokio);
      its launcher (RUN-WAN2GP.cmd, run.bat, or python wgp.py with args.txt) and its ports
      (wgp_config.json's mcp_port, args.txt's --server-port);
    - starts WanGP in a minimized window when FilmLab starts and WanGP isn't running (Settings: on by default),
      and again if it stops (Settings: off by default);
    - writes its status to %LOCALAPPDATA%\FilmLab\host-status.json for the cockpit, and carries out the
      cockpit's requests from %LOCALAPPDATA%\FilmLab\requests (start WanGP, detect again, settings). It only
      ever starts the WanGP launcher it found; a request can't make it run anything else.
    - registers or removes the "FilmLab Studio" logon task (start FilmLab when you sign in to Windows).
.EXAMPLE
  .\filmlab-host.ps1 detect        # find WanGP and show what was found
  .\filmlab-host.ps1 status        # the helper's last status
  .\filmlab-host.ps1 start-wangp   # start WanGP now if it isn't running
#>
param([ValidateSet('run', 'detect', 'status', 'start-wangp')][string]$Command = 'run')

$ErrorActionPreference = 'Stop'
$StateDir = Join-Path $env:LOCALAPPDATA 'FilmLab'
$ConfigFile = Join-Path $StateDir 'config.json'
$StatusFile = Join-Path $StateDir 'host-status.json'
$RequestDir = Join-Path $StateDir 'requests'
$LogDir = Join-Path $StateDir 'logs'
$PidFile = Join-Path $StateDir 'host.pid'
$TaskName = 'FilmLab Studio'
$Ctl = Join-Path $PSScriptRoot 'filmlabctl.ps1'
$Tick = 5                 # seconds between checks
$RestartAfter = 60        # WanGP down this long (and the setting on): start it again
$StartGrace = 240         # after starting WanGP, give it this long before judging it again

# --- config and log ---------------------------------------------------------------------------------

function Write-Log([string]$m) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
    Add-Content -Path (Join-Path $LogDir 'host.log') -Value "$(Get-Date -Format s) $m"
}
function Load-Config {
    $h = @{}
    if (Test-Path $ConfigFile) {
        try { (Get-Content $ConfigFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $h[$_.Name] = $_.Value } }
        catch { Write-Log "config.json unreadable: $_" }
    }
    if ($null -eq $h.wangp_autostart) { $h.wangp_autostart = $true }
    if ($null -eq $h.wangp_restart) { $h.wangp_restart = $false }
    return $h
}
function Save-Config([hashtable]$c) {
    New-Item -ItemType Directory -Force $StateDir | Out-Null
    $tmp = "$ConfigFile.tmp"
    $c | ConvertTo-Json -Depth 5 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force $tmp $ConfigFile
}

# --- finding WanGP ----------------------------------------------------------------------------------

function Test-Port([int]$port) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $c.BeginConnect('127.0.0.1', $port, $null, $null)
        return ($ar.AsyncWaitHandle.WaitOne(800) -and $c.Connected)
    } catch { return $false } finally { $c.Close() }
}

function Get-WanGPProcess {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'wgp\.py' } | Select-Object -First 1
}

function Find-RootAbove([string]$file) {
    # the folder with wgp.py at or above a file (python.exe usually lives in app\env_conda or a venv inside it)
    if (-not $file) { return $null }
    $d = Split-Path $file -Parent
    for ($i = 0; $i -lt 6 -and $d; $i++) {
        if (Test-Path (Join-Path $d 'wgp.py')) { return $d }
        $d = Split-Path $d -Parent
    }
    return $null
}

function Find-WanGPFolders {
    $cands = @()
    foreach ($d in (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' })) {
        foreach ($sub in @('Wan2GP', 'WanGP', 'Wan2GP\app', 'WanGP\app', 'AI\Wan2GP', 'AI\Wan2GP\app')) { $cands += Join-Path $d.Root $sub }
    }
    $cands += Join-Path $env:USERPROFILE 'Wan2GP'
    $cands += Join-Path $env:USERPROFILE 'Wan2GP\app'
    $pk = Join-Path $env:USERPROFILE 'pinokio\api'
    if (Test-Path $pk) { Get-ChildItem $pk -Directory -Filter 'wan*' | ForEach-Object { $cands += (Join-Path $_.FullName 'app'); $cands += $_.FullName } }
    $seen = @{}; $found = @()
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c 'wgp.py'))) {
            $full = (Get-Item $c).FullName
            if (-not $seen.ContainsKey($full.ToLower())) { $seen[$full.ToLower()] = $true; $found += $full }
        }
    }
    return @($found)
}

function Get-Launcher([string]$wan) {
    $parent = Split-Path $wan -Parent
    foreach ($p in @((Join-Path $parent 'RUN-WAN2GP.cmd'), (Join-Path $wan 'RUN-WAN2GP.cmd'), (Join-Path $wan 'scripts\run.bat'),
                     (Join-Path $wan 'run.bat'), (Join-Path $parent 'run.bat'))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Get-WanGPPython([string]$wan) {
    foreach ($p in @('env_conda\python.exe', 'venv\Scripts\python.exe', '.venv\Scripts\python.exe', 'env\Scripts\python.exe')) {
        $f = Join-Path $wan $p
        if (Test-Path $f) { return $f }
    }
    return $null
}

function Get-WanGPInfo([string]$wan, $proc) {
    $info = [ordered]@{ path = $wan; launcher = (Get-Launcher $wan); python = (Get-WanGPPython $wan); mcp_port = 8000
                        webui_port = 7860; mcp_enabled = $null; args = '' }
    $w = Join-Path $wan 'wgp_config.json'
    if (Test-Path $w) {
        try {
            $j = Get-Content $w -Raw | ConvertFrom-Json
            if ($j.mcp_port) { $info.mcp_port = [int]$j.mcp_port }
            $info.mcp_enabled = [bool]$j.mcp_webui
        } catch { }
    }
    foreach ($af in @((Join-Path $wan 'args.txt'), (Join-Path $wan 'scripts\args.txt'))) {
        if (Test-Path $af) {
            $t = (Get-Content $af -Raw).Trim()
            $info.args = $t
            if ($t -match '--server-port\s+(\d+)') { $info.webui_port = [int]$Matches[1] }
            if ($t -match '--mcp-port\s+(\d+)') { $info.mcp_port = [int]$Matches[1] }
            if ($t -match '--mcp-webui') { $info.mcp_enabled = $true }
            break
        }
    }
    if ($proc -and $proc.CommandLine) {  # what the running WanGP was actually started with wins
        if ($proc.CommandLine -match '--server-port\s+(\d+)') { $info.webui_port = [int]$Matches[1] }
        if ($proc.CommandLine -match '--mcp-port\s+(\d+)') { $info.mcp_port = [int]$Matches[1] }
    }
    return $info
}

function Invoke-Detect([hashtable]$cfg) {
    # The folder you chose wins; else the running WanGP; else the one found before; else the usual places.
    $proc = Get-WanGPProcess
    $wan = $null; $how = $null
    if ($cfg.wangp_path_chosen -and (Test-Path (Join-Path $cfg.wangp_path_chosen 'wgp.py'))) { $wan = $cfg.wangp_path_chosen; $how = 'chosen in Settings' }
    if (-not $wan -and $proc) { $wan = Find-RootAbove $proc.ExecutablePath; if ($wan) { $how = 'the running WanGP' } }
    if (-not $wan -and $cfg.wangp_path -and (Test-Path (Join-Path $cfg.wangp_path 'wgp.py'))) { $wan = $cfg.wangp_path; $how = 'saved before' }
    if (-not $wan) { $all = Find-WanGPFolders; if ($all.Count) { $wan = $all[0]; $how = 'found on this PC' } }
    if (-not $wan) {
        $cfg.wangp_found_by = $null
        Write-Log 'WanGP not found'
        return $null
    }
    $info = Get-WanGPInfo $wan $proc
    $cfg.wangp_path = $wan; $cfg.wangp_port = $info.mcp_port; $cfg.wangp_webui_port = $info.webui_port
    $cfg.wangp_launcher = $info.launcher; $cfg.wangp_found_by = $how
    Save-Config $cfg
    return $info
}

# --- starting WanGP ---------------------------------------------------------------------------------

function Start-WanGP($info) {
    if (-not $info) { throw 'WanGP was not found on this PC; choose its folder in Settings > Startup & WanGP' }
    if ($info.launcher) {
        Write-Log "starting WanGP: $($info.launcher)"
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$($info.launcher)`"" `
            -WorkingDirectory (Split-Path $info.launcher -Parent) -WindowStyle Minimized | Out-Null
    } elseif ($info.python) {
        $a = @('wgp.py'); if ($info.args) { $a += ($info.args -split '\s+' | Where-Object { $_ }) }
        Write-Log "starting WanGP: $($info.python) $($a -join ' ')"
        Start-Process -FilePath $info.python -ArgumentList $a -WorkingDirectory $info.path -WindowStyle Minimized | Out-Null
    } else {
        throw "no way to start WanGP in $($info.path): no RUN-WAN2GP.cmd or run.bat, and no Python environment found"
    }
}

# --- the logon task -----------------------------------------------------------------------------------

function Get-LogonTask { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
function Set-LogonTask([bool]$on) {
    if ($on) {
        if (Get-LogonTask) { return }
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Ctl`" -Command start"
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
        $trigger.Delay = 'PT45S'
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Description 'Starts FilmLab Studio (and WanGP, if set so in FilmLab) when you sign in.' | Out-Null
        Write-Log 'logon task installed'
    } elseif (Get-LogonTask) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Log 'logon task removed'
    }
}

# --- the cockpit's requests -----------------------------------------------------------------------------

function Invoke-Requests([hashtable]$cfg, $state) {
    if (-not (Test-Path $RequestDir)) { return }
    foreach ($f in (Get-ChildItem $RequestDir -Filter '*.json' | Sort-Object Name)) {
        try {
            $r = Get-Content $f.FullName -Raw | ConvertFrom-Json
            Remove-Item $f.FullName -Force
            switch ($r.action) {
                'start-wangp' {
                    if (Test-Port $state.info.mcp_port) { $state.note = 'WanGP is already running' }
                    else { Start-WanGP $state.info; $state.started = Get-Date; $state.startedByUs = $true; $state.note = 'Starting WanGP' }
                }
                'detect' { $state.info = Invoke-Detect $cfg; $state.note = if ($state.info) { 'Detected again' } else { 'WanGP not found' } }
                'settings' {
                    foreach ($k in @('wangp_autostart', 'wangp_restart')) {
                        if ($null -ne $r.$k) { $cfg[$k] = [bool]$r.$k }
                    }
                    if ($r.PSObject.Properties.Name -contains 'wangp_path') {
                        $cfg.wangp_path_chosen = if ($r.wangp_path) { [string]$r.wangp_path } else { $null }
                    }
                    Save-Config $cfg
                    if ($null -ne $r.start_at_logon) { Set-LogonTask ([bool]$r.start_at_logon) }
                    $state.info = Invoke-Detect $cfg
                    $state.note = 'Settings saved'
                }
                default { Write-Log "unknown request $($r.action)" }
            }
            $state.error = $null
        } catch {
            $state.error = "$_"
            Write-Log "request $($f.Name) failed: $_"
            Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
        }
    }
}

function Write-Status([hashtable]$cfg, $state, [bool]$up, $proc) {
    $i = $state.info
    $s = [ordered]@{
        helper_pid = $PID; updated_at = (Get-Date).ToUniversalTime().ToString('o')
        wangp = [ordered]@{
            found = [bool]$i; path = $i.path; found_by = $cfg.wangp_found_by; launcher = $i.launcher; python = $i.python
            mcp_port = $i.mcp_port; webui_port = $i.webui_port; mcp_enabled = $i.mcp_enabled
            running = $up; process = [bool]$proc
            starting = [bool]($state.started -and -not $up -and ((Get-Date) - $state.started).TotalSeconds -lt $StartGrace)
            started_by_filmlab = [bool]$state.startedByUs
        }
        settings = [ordered]@{ wangp_autostart = [bool]$cfg.wangp_autostart; wangp_restart = [bool]$cfg.wangp_restart
                               start_at_logon = [bool](Get-LogonTask); wangp_path_chosen = $cfg.wangp_path_chosen }
        note = $state.note; error = $state.error
    }
    $tmp = "$StatusFile.tmp"
    $s | ConvertTo-Json -Depth 5 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force $tmp $StatusFile
}

# --- commands -------------------------------------------------------------------------------------------

New-Item -ItemType Directory -Force $StateDir, $RequestDir | Out-Null
switch ($Command) {
    'detect' {
        $info = Invoke-Detect (Load-Config)
        if ($info) { $info; "found: $((Load-Config).wangp_found_by)"; "MCP answering: $(Test-Port $info.mcp_port)" } else { 'WanGP not found' }
    }
    'status' { if (Test-Path $StatusFile) { Get-Content $StatusFile -Raw } else { 'no status yet (the helper has not run)' } }
    'start-wangp' {
        $info = Invoke-Detect (Load-Config)
        if ($info -and (Test-Port $info.mcp_port)) { 'WanGP is already running' } else { Start-WanGP $info; 'Starting WanGP' }
    }
    'run' {
        # one helper at a time
        if (Test-Path $PidFile) {
            $other = Get-Process -Id ([int](Get-Content $PidFile)) -ErrorAction SilentlyContinue
            if ($other -and $other.Id -ne $PID -and $other.ProcessName -match 'powershell|pwsh') { return }
        }
        Set-Content $PidFile $PID
        Write-Log "helper started (pid $PID)"
        $cfg = Load-Config
        $state = @{ info = (Invoke-Detect $cfg); started = $null; startedByUs = $false; note = $null; error = $null;
                    downSince = $null; seenUp = $false }
        try {
            if ($state.info -and $cfg.wangp_autostart -and -not (Test-Port $state.info.mcp_port) -and -not (Get-WanGPProcess)) {
                Start-WanGP $state.info; $state.started = Get-Date; $state.startedByUs = $true; $state.note = 'Started WanGP with FilmLab'
            }
        } catch { $state.error = "$_"; Write-Log "autostart failed: $_" }
        while ($true) {
            $cfg = Load-Config
            Invoke-Requests $cfg $state
            $up = [bool]($state.info -and (Test-Port $state.info.mcp_port))
            $proc = Get-WanGPProcess
            if ($up) { $state.downSince = $null; $state.seenUp = $true; $state.started = $null }
            elseif (-not $state.downSince) { $state.downSince = Get-Date }
            $graceOver = -not $state.started -or ((Get-Date) - $state.started).TotalSeconds -gt $StartGrace
            if (-not $up -and -not $proc -and $cfg.wangp_restart -and $state.seenUp -and $graceOver -and
                ((Get-Date) - $state.downSince).TotalSeconds -gt $RestartAfter) {
                try { Start-WanGP $state.info; $state.started = Get-Date; $state.startedByUs = $true; $state.note = 'WanGP stopped; started it again' }
                catch { $state.error = "$_"; Write-Log "restart failed: $_" }
            }
            try { Write-Status $cfg $state $up $proc } catch { Write-Log "status write failed: $_" }
            Start-Sleep -Seconds $Tick
        }
    }
}
