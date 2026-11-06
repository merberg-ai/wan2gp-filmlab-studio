<#
.SYNOPSIS
  FilmLab Studio setup: a guided installer for Windows 11 with an NVIDIA GPU.

.DESCRIPTION
  Detects what is already there, explains what is missing, and asks before every change.
    1. This PC: Windows, GPU, VRAM and driver, CPU virtualization, memory, disk space, free ports, Git.
    2. WSL 2 with mirrored networking (instructions if missing; setup waits until it is there).
    3. Ollama and the director's model (instructions if missing; you sign in to Ollama yourself).
    4. WanGP: finds an existing install, or clones and installs it with WanGP's own installer;
       turns on its MCP server (loopback only, no file-system reads) and starts it.
    5. The FilmLab Linux distro, the director (OpenClaw, locked down), the backend and the cockpit.
    6. Your owner login.
    7. Optional: download the models FilmLab uses now; phone/LAN access over HTTPS; start at logon.
    8. A ZIP download is connected to the GitHub repository, so it can be updated later.
  Where setup waits for something you install yourself (WSL, Ollama, Git), it re-checks every few seconds
  on its own and picks up newly installed programs without a new terminal.
  Every step checks first, so running setup again picks up where it stopped. Choices are kept in
  %LOCALAPPDATA%\FilmLab\config.json and a log in %LOCALAPPDATA%\FilmLab\setup.log.
  Setup never runs 'wsl --shutdown' or changes Windows settings without asking first.

.EXAMPLE
  .\Setup-FilmLab.cmd                       (double-click, from the FilmLab folder)
  .\scripts\install\Setup-FilmLab.ps1 -CheckOnly    only report what is there and what is missing
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Repo = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$StateDir = Join-Path $env:LOCALAPPDATA 'FilmLab'
$ConfigFile = Join-Path $StateDir 'config.json'
$LogFile = Join-Path $StateDir 'setup.log'
New-Item -ItemType Directory -Force $StateDir | Out-Null

$DefaultModel = 'gemma4:31b-cloud'
$WanGPRepo = 'https://github.com/deepbeepmeep/Wan2GP.git'
$FilmLabRepo = 'https://github.com/merberg-ai/wan2gp-filmlab-studio.git'
$CockpitPort = 19300

# --- output and questions -----------------------------------------------------------------------

function Log([string]$m) { Add-Content -Path $LogFile -Value ("{0:s} {1}" -f (Get-Date), $m) }
function Say([string]$m, [string]$c = 'Gray') { Write-Host $m -ForegroundColor $c; Log $m }
function Ok([string]$m) { Say "  [ok]   $m" 'Green' }
function Warn([string]$m) { Say "  [!]    $m" 'Yellow' }
function Bad([string]$m) { Say "  [x]    $m" 'Red' }
function Info([string]$m) { Say "         $m" 'Gray' }
function Head([string]$m) { Write-Host ''; Say ("== $m " + ('=' * [Math]::Max(3, 70 - $m.Length))) 'Cyan' }

function Ask-YesNo([string]$q, [bool]$default = $true) {
    $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
    while ($true) {
        $a = (Read-Host "  ? $q $hint").Trim().ToLower()
        Log "Q: $q -> '$a'"
        if ($a -eq '') { return $default }
        if ($a -in @('y', 'yes')) { return $true }
        if ($a -in @('n', 'no')) { return $false }
    }
}

function Ask-Text([string]$q, [string]$default = '') {
    $shown = if ($default) { " [$default]" } else { '' }
    $a = (Read-Host "  ? $q$shown").Trim()
    Log "Q: $q -> '$a'"
    if ($a -eq '') { return $default }
    return $a
}

function Ask-Choice([string]$q, [string[]]$options) {
    # options like 'U=Use this one' ; returns the letter
    foreach ($o in $options) { $k, $t = $o -split '=', 2; Write-Host "      [$k] $t" }
    $keys = $options | ForEach-Object { ($_ -split '=', 2)[0].ToLower() }
    while ($true) {
        $a = (Read-Host "  ? $q").Trim().ToLower()
        Log "Q: $q -> '$a'"
        if ($keys -contains $a) { return $a }
    }
}

# Programs installed while setup runs (Git, Ollama) only appear on a NEW process's PATH; re-read it from the registry.
function Update-Path {
    $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $u = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$m;$u"
}

function Stop-Setup {
    Say 'Setup stopped. Run Setup-FilmLab.cmd again when you are ready: it continues from where it stopped.' 'Yellow'
    exit 1
}

# Instructions for something setup won't install itself, then wait until it's there: setup checks again on its own
# every few seconds (so the user just follows the instructions), Q stops it.
function Wait-Until([string]$what, [scriptblock]$check, [string[]]$help, [int]$every = 5) {
    Update-Path
    if (& $check) { Ok "$what is ready."; return }
    Bad "$what isn't ready yet."
    foreach ($h in $help) { Info $h }
    Say "  Waiting: setup checks again every $every s and continues by itself. Press Q to stop for now." 'White'
    $ticks = 0
    while ($true) {
        Start-Sleep -Milliseconds 500
        try {
            if ([Console]::KeyAvailable -and ([Console]::ReadKey($true).KeyChar -in @('q', 'Q'))) { Write-Host ''; Stop-Setup }
        } catch { }
        $ticks++
        if ($ticks % ($every * 2) -eq 0) {
            Update-Path
            if (& $check) { break }
            Write-Host '.' -NoNewline
        }
    }
    Write-Host ''
    Ok "$what is ready."
}

function Load-Config {
    if (Test-Path $ConfigFile) {
        $o = Get-Content $ConfigFile -Raw | ConvertFrom-Json
        $h = @{}; $o.PSObject.Properties | ForEach-Object { $h[$_.Name] = $_.Value }; return $h
    }
    return @{}
}
function Save-Config([hashtable]$c) { $c | ConvertTo-Json | Set-Content -Path $ConfigFile -Encoding UTF8 }

# --- detection ------------------------------------------------------------------------------------

function Clean([object[]]$lines) { ($lines | ForEach-Object { "$_" -replace "`0", '' }) }

function Get-Hardware {
    $os = Get-CimInstance Win32_OperatingSystem
    $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $hw = [ordered]@{
        os = "$($os.Caption) (build $($os.BuildNumber))"; build = [int]$os.BuildNumber
        cpu = "$($cpu.Name.Trim()) ($($cpu.NumberOfCores) cores)"
        ram_gb = [Math]::Round($os.TotalVisibleMemorySize / 1MB, 0)
        gpus = @(); nvidia = $false; vram_gb = 0; driver = ''
        # WSL needs CPU virtualization. With a hypervisor already running, the firmware flag can read false.
        virtualization = [bool]((Get-CimInstance Win32_ComputerSystem).HypervisorPresent -or $cpu.VirtualizationFirmwareEnabled)
    }
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        $rows = & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null
        foreach ($r in $rows) {
            $p = $r -split ',\s*'
            if ($p.Count -ge 3) {
                $hw.gpus += "$($p[0]) ($([Math]::Round([double]$p[1] / 1024, 0)) GB, driver $($p[2]))"
                $hw.nvidia = $true
                $hw.vram_gb = [Math]::Max($hw.vram_gb, [Math]::Round([double]$p[1] / 1024, 0))
                $hw.driver = $p[2]
            }
        }
    }
    if (-not $hw.gpus) { $hw.gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) }
    $hw.disks = @(Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Free -gt 0 -and $_.Root -match '^[A-Z]:\\$' } |
        ForEach-Object { [pscustomobject]@{ drive = $_.Name; free_gb = [Math]::Round($_.Free / 1GB, 0) } })
    return $hw
}

function Get-Wsl {
    $info = @{ installed = $false; version = ''; distros = @(); mirrored = $false }
    try {
        $out = Clean (& wsl.exe --version 2>$null)
        if ($LASTEXITCODE -eq 0 -and $out) {
            $info.installed = $true
            $line = $out | Where-Object { $_ -match '^WSL[^:]*:\s*([0-9.]+)' } | Select-Object -First 1
            if ($line -match ':\s*([0-9.]+)') { $info.version = $Matches[1] }
            $info.distros = @(Clean (& wsl.exe -l -q 2>$null) | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
        }
    } catch { }
    $cfg = Join-Path $env:USERPROFILE '.wslconfig'
    if (Test-Path $cfg) { $info.mirrored = [bool](Select-String -Path $cfg -Pattern '^\s*networkingMode\s*=\s*mirrored' -Quiet) }
    return $info
}

function Test-Http([string]$url) {
    try { $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 4; return $true } catch { return $false }
}

function Test-Mcp([int]$port) {
    $body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"filmlab-setup","version":"1"}}}'
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/mcp" -Method Post -Body $body -UseBasicParsing -TimeoutSec 5 `
            -ContentType 'application/json' -Headers @{ Accept = 'application/json, text/event-stream' }
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Get-Ollama {
    $o = @{ installed = $false; running = $false; models = @() }
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $guess = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
        if (Test-Path $guess) { $cmd = $guess }
    }
    if ($cmd) { $o.installed = $true; $o.exe = if ($cmd -is [string]) { $cmd } else { $cmd.Source } }
    $o.running = Test-Http 'http://127.0.0.1:11434/api/version'
    if ($o.running) {
        try { $o.models = @((Invoke-RestMethod 'http://127.0.0.1:11434/api/tags' -TimeoutSec 5).models | ForEach-Object { $_.name }) } catch { }
    }
    return $o
}

function Find-WanGP([hashtable]$cfg) {
    $found = @()
    $cands = @()
    if ($cfg.wangp_path) { $cands += $cfg.wangp_path }
    foreach ($d in (Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Root -match '^[A-Z]:\\$' })) {
        foreach ($sub in @('Wan2GP', 'WanGP', 'wan2gp\app', 'Wan2GP\app', 'AI\Wan2GP')) { $cands += Join-Path $d.Root $sub }
    }
    $cands += Join-Path $env:USERPROFILE 'Wan2GP'
    $pk = Join-Path $env:USERPROFILE 'pinokio\api'
    if (Test-Path $pk) { Get-ChildItem $pk -Directory -Filter 'wan*' | ForEach-Object { $cands += (Join-Path $_.FullName 'app'); $cands += $_.FullName } }
    $seen = @{}
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c 'wgp.py'))) {
            $full = (Get-Item $c).FullName.TrimEnd('')
            if (-not $seen.ContainsKey($full.ToLower())) { $seen[$full.ToLower()] = $true; $found += $full }
        }
    }
    return @($found)
}

function Get-Launcher([string]$wan) {
    $parent = Split-Path $wan -Parent
    foreach ($p in @((Join-Path $parent 'RUN-WAN2GP.cmd'), (Join-Path $wan 'scripts\run.bat'), (Join-Path $wan 'run.bat'))) {
        if (Test-Path $p) { return $p }
    }
    return $null
}

function Get-LanAddress {
    $cfgs = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback' }
    foreach ($c in $cfgs) {
        $ip = $c.IPv4Address | Select-Object -First 1
        if ($ip) {
            $bytes = ([System.Net.IPAddress]$ip.IPAddress).GetAddressBytes()
            $prefix = [int]$ip.PrefixLength
            $mask = [uint32]([Math]::Pow(2, 32) - [Math]::Pow(2, 32 - $prefix))
            $n = ([uint32]$bytes[0] -shl 24) -bor ([uint32]$bytes[1] -shl 16) -bor ([uint32]$bytes[2] -shl 8) -bor [uint32]$bytes[3]
            $net = $n -band $mask
            $netIp = '{0}.{1}.{2}.{3}' -f (($net -shr 24) -band 255), (($net -shr 16) -band 255), (($net -shr 8) -band 255), ($net -band 255)
            return @{ ip = $ip.IPAddress; cidr = "$netIp/$prefix"; alias = $c.InterfaceAlias }
        }
    }
    return $null
}

function ConvertTo-WslPath([string]$p) { '/mnt/' + $p.Substring(0, 1).ToLower() + $p.Substring(2).Replace('\', '/') }

function Invoke-Distro([string]$distro, [string]$cmd, [switch]$Root) {
    # Runs a bash command in the FilmLab distro, streaming its output; returns the exit code.
    $wargs = @('-d', $distro)
    if ($Root) { $wargs += @('-u', 'root') }
    $wargs += @('--', 'bash', '-lc', $cmd)
    Log "wsl $($wargs -join ' ')"
    & wsl.exe @wargs 2>&1 | ForEach-Object { $l = "$_" -replace "`0", ''; Write-Host "         $l"; Log "  | $l" }
    return $LASTEXITCODE
}

# --- the steps ------------------------------------------------------------------------------------

Write-Host ''
Say 'FilmLab Studio setup' 'White'
Say "FilmLab folder: $Repo"
Say "Log: $LogFile"
$cfg = Load-Config

Head '1. This PC'
$hw = Get-Hardware
Info "Windows: $($hw.os)"
Info "CPU:     $($hw.cpu)"
Info "Memory:  $($hw.ram_gb) GB"
foreach ($g in $hw.gpus) { Info "GPU:     $g" }
Info ('Disks:   ' + (($hw.disks | ForEach-Object { "$($_.drive): $($_.free_gb) GB free" }) -join ', '))
$blocking = $false
if ($hw.build -lt 22621) { Bad 'Windows 11 22H2 or newer is needed (WSL mirrored networking).'; $blocking = $true } else { Ok 'Windows 11' }
if (-not $hw.nvidia) { Bad 'No NVIDIA GPU found (nvidia-smi). FilmLab uses WanGP on an NVIDIA GPU.'; $blocking = $true }
elseif ($hw.vram_gb -lt 12) { Warn "$($hw.vram_gb) GB of VRAM: the video models want 16 GB or more; small shots may still work." }
else { Ok "NVIDIA GPU with $($hw.vram_gb) GB VRAM" }
if ($hw.nvidia -and $hw.driver -and ([double]($hw.driver -replace '[^0-9.].*$', '')) -lt 580) {
    Warn "NVIDIA driver $($hw.driver): WanGP's recommended build (CUDA 13) needs 580 or newer."
    Info 'Update it from https://www.nvidia.com/drivers (or the NVIDIA app) before installing WanGP.'
}
if (-not $hw.virtualization) {
    Bad 'CPU virtualization looks off: WSL needs it.'
    Info "Turn on 'Intel VT-x' / 'AMD SVM' (or 'Virtualization Technology') in your PC's BIOS/UEFI settings, then restart."
}
if ($hw.ram_gb -lt 32) { Warn "$($hw.ram_gb) GB of memory: 32 GB or more is recommended (64 GB for long shots)." } else { Ok "$($hw.ram_gb) GB of memory" }
$best = $hw.disks | Sort-Object free_gb -Descending | Select-Object -First 1
if ($best.free_gb -lt 150) { Warn "The most free space is $($best.free_gb) GB on $($best.drive):. Plan on about 150 GB (models alone are 50-100 GB)." }
else { Ok "$($best.free_gb) GB free on $($best.drive):" }
if ($Repo -match '\\OneDrive|\\Downloads\\|\\AppData\\Local\\Temp') {
    Warn "FilmLab is in $Repo. That folder stays in use (WSL reads it directly): a synced or temporary folder is slow"
    Info 'or gets cleaned up. Moving it to e.g. C:\FilmLab before going further is better (then run setup from there).'
}
$hasGit = [bool](Get-Command git -ErrorAction SilentlyContinue)
$isCheckout = Test-Path (Join-Path $Repo '.git')

$wsl = Get-Wsl
$ollama = Get-Ollama
$wans = Find-WanGP $cfg
$wanPort = if ($cfg.wangp_port) { [int]$cfg.wangp_port } else { 8000 }
$mcp = Test-Mcp $wanPort
Head 'What is installed'
if ($wsl.installed) { Ok "WSL $($wsl.version)" } else { Bad 'WSL is not installed' }
if ($wsl.mirrored) { Ok 'WSL mirrored networking is on' } else { Warn 'WSL mirrored networking is off' }
if ($ollama.installed) { Ok ('Ollama' + $(if ($ollama.running) { ' (running)' } else { ' (not running)' })) } else { Bad 'Ollama is not installed' }
if ($ollama.models -contains $DefaultModel) { Ok "director model $DefaultModel" } elseif ($ollama.running) { Warn "director model $DefaultModel isn't pulled yet" }
if ($wans) { foreach ($w in $wans) { Ok "WanGP found: $w" } } else { Warn 'WanGP not found in the usual places' }
if ($mcp) { Ok "WanGP's MCP server answers on 127.0.0.1:$wanPort" } else { Warn "WanGP's MCP server doesn't answer on 127.0.0.1:$wanPort" }
$distro = if ($cfg.distro) { $cfg.distro } else { 'FilmLab' }
$distroExists = $wsl.distros -contains $distro
if ($distroExists) { Ok "FilmLab distro '$distro' exists" } else { Info "FilmLab distro '$distro' not created yet" }
if ($hasGit) { Ok 'Git' } else { Warn 'Git is not installed (needed to install WanGP and for FilmLab updates)' }
if (-not $isCheckout) { Warn 'This FilmLab folder is a download, not a Git checkout: updates need one (setup can connect it later)' }
# FilmLab's own ports: busy ones are fine when it's this FilmLab running already.
foreach ($p in @(19300, 19189)) {
    $busy = Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue
    if ($busy -and -not $distroExists) {
        $who = (Get-Process -Id ($busy | Select-Object -First 1).OwningProcess -ErrorAction SilentlyContinue).ProcessName
        Warn "Port $p is already in use ($who). FilmLab needs it: close that program, or FilmLab won't start."
    }
}

if ($CheckOnly) { Say ''; Say 'Check only: nothing was changed.' 'White'; exit 0 }
if ($blocking) {
    Bad 'This PC is missing something FilmLab needs (above).'
    if (-not (Ask-YesNo 'Continue anyway?' $false)) { exit 1 }
}
# A ZIP download carries Windows' "from the internet" mark on every file: clear it on FilmLab's own scripts.
Get-ChildItem -Path $Repo -Recurse -File -Include *.ps1, *.cmd -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue

# 2. WSL
Head '2. WSL (the Linux side FilmLab runs in)'
function Test-WslVersion([string]$v, [string]$min) {
    $p = @($v -split '\.' | Where-Object { $_ -match '^\d+$' }) + @('0', '0', '0')
    return ([version]('{0}.{1}.{2}' -f $p[0], $p[1], $p[2])) -ge [version]$min
}
$wslReady = { $w = Get-Wsl; $w.installed -and (Test-WslVersion $w.version '2.4.4') }
Wait-Until 'WSL 2.4.4 or newer' $wslReady @(
    'Open PowerShell as Administrator and run:   wsl --install --no-distribution',
    '(if WSL is already there but older:          wsl --update)',
    'Restart Windows if it asks you to, then come back here.')

$wsl = Get-Wsl
if (-not $wsl.mirrored) {
    Warn 'FilmLab needs WSL mirrored networking, so it can reach WanGP and Ollama on this PC (127.0.0.1).'
    Info "It is one line in $env:USERPROFILE\.wslconfig. It applies to every WSL distro, after WSL restarts."
    $c = Ask-Choice 'Turn it on?' @('A=Add it for me (a backup of .wslconfig is kept)', 'M=I will do it myself', 'Q=Stop setup')
    if ($c -eq 'q') { exit 1 }
    if ($c -eq 'a') {
        $f = Join-Path $env:USERPROFILE '.wslconfig'
        if (Test-Path $f) {
            Copy-Item $f "$f.filmlab-backup-$(Get-Date -Format yyyyMMddHHmmss)"
            $text = Get-Content $f -Raw
            if ($text -match '(?m)^\s*networkingMode\s*=') { $text = $text -replace '(?m)^\s*networkingMode\s*=.*$', 'networkingMode=mirrored' }
            elseif ($text -match '(?m)^\s*\[wsl2\]') { $text = $text -replace '(?m)^\s*\[wsl2\]\s*$', "[wsl2]`r`nnetworkingMode=mirrored" }
            else { $text = $text.TrimEnd() + "`r`n`r`n[wsl2]`r`nnetworkingMode=mirrored`r`n" }
            Set-Content -Path $f -Value $text -Encoding ASCII
        } else {
            Set-Content -Path $f -Value "[wsl2]`r`nnetworkingMode=mirrored`r`n" -Encoding ASCII
        }
        Ok "mirrored networking added to $f"
        $running = @(Clean (& wsl.exe -l -q --running 2>$null) | Where-Object { $_.Trim() })
        if ($running) {
            Warn ("WSL must restart for it to apply. Running now: " + ($running -join ', '))
            if (Ask-YesNo 'Restart WSL now? (wsl --shutdown stops every running Linux distro; save your work there first)' $false) {
                & wsl.exe --shutdown; Ok 'WSL restarted'
            } else { Info 'Restart WSL yourself later (wsl --shutdown, or restart Windows) before FilmLab can reach WanGP.' }
        }
    } else {
        Wait-Until 'Mirrored networking' { (Get-Wsl).mirrored } @(
            "Add these two lines to $env:USERPROFILE\.wslconfig:", '    [wsl2]', '    networkingMode=mirrored',
            'Then run:  wsl --shutdown   (stops every running Linux distro)')
    }
}

# 3. Ollama
Head '3. Ollama (runs the director model)'
# Installed but not running: start its tray app (the same as starting it from the Start menu).
$ollamaApp = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama app.exe'
$script:ollamaStarted = $false
$ollamaUp = {
    $o = Get-Ollama
    if ($o.installed -and -not $o.running -and -not $script:ollamaStarted -and (Test-Path $ollamaApp)) {
        Start-Process $ollamaApp; $script:ollamaStarted = $true; Start-Sleep 5; $o = Get-Ollama
    }
    $o.installed -and $o.running
}
Wait-Until 'Ollama' $ollamaUp @(
    'Install Ollama for Windows from https://ollama.com/download/windows',
    '(or in a terminal:  winget install --id Ollama.Ollama )',
    'Setup starts it once it is installed (it then runs in the tray).')
$model = if ($cfg.director_model) { $cfg.director_model } else { $DefaultModel }
$ollama = Get-Ollama
if ($ollama.models -notcontains $model) {
    Info "The director uses $model, an Ollama cloud model: it runs on Ollama's servers (no GPU here), so it needs a free"
    Info 'Ollama account. Sign in first:  ollama signin   (opens your browser).'
    $c = Ask-Choice "Set up $model?" @('S=Sign in now, then pull it', 'P=Already signed in: just pull it', 'O=Use another Ollama model', 'L=Later')
    if ($c -eq 'o') { $model = Ask-Text 'Model name (as in ollama list)' $model }
    if ($c -eq 's') { & $ollama.exe signin }
    if ($c -in @('s', 'p', 'o')) {
        & $ollama.exe pull $model
        if ($LASTEXITCODE -ne 0) { Warn "Pulling $model failed; sign in (ollama signin) and run setup again." }
    }
}
if ((Get-Ollama).models -contains $model) { Ok "director model $model" }
$cfg.director_model = $model; Save-Config $cfg

# 4. WanGP
Head '4. WanGP (makes the videos, images, voices and music)'
$wan = $cfg.wangp_path
if (-not $wan -or -not (Test-Path (Join-Path $wan 'wgp.py'))) {
    $wan = $null
    $wans = Find-WanGP $cfg
    if ($wans.Count -ge 1) {
        $opts = @()
        for ($i = 0; $i -lt $wans.Count; $i++) { $opts += "$($i + 1)=Use $($wans[$i])" }
        $opts += @('P=It is somewhere else (type the folder)', 'I=Install a new WanGP')
        $c = Ask-Choice 'Which WanGP should FilmLab use?' $opts
        if ($c -match '^\d+$') { $wan = $wans[[int]$c - 1] }
        elseif ($c -eq 'p') { $wan = Ask-Text 'WanGP folder (the one with wgp.py)' }
    } else {
        $c = Ask-Choice 'No WanGP found. What now?' @('I=Install WanGP for me', 'P=It is somewhere else (type the folder)', 'Q=Stop setup')
        if ($c -eq 'q') { exit 1 }
        if ($c -eq 'p') { $wan = Ask-Text 'WanGP folder (the one with wgp.py)' }
    }
    if (-not $wan) {
        Wait-Until 'Git' { [bool](Get-Command git -ErrorAction SilentlyContinue) } @(
            'Install Git for Windows from https://git-scm.com/download/win  (or:  winget install --id Git.Git )',
            'The default options are fine.')
        $drive = ($hw.disks | Sort-Object free_gb -Descending | Select-Object -First 1).drive
        $wan = Ask-Text 'Install WanGP into' "$($drive):\Wan2GP"
        if (-not (Test-Path (Join-Path $wan 'wgp.py'))) {
            Say "  cloning WanGP into $wan" 'Gray'
            & git clone --depth 1 $WanGPRepo $wan
            if ($LASTEXITCODE -ne 0) { Bad 'git clone failed (see above).'; exit 1 }
        }
        Say ''
        Say "  WanGP's own installer opens in a new window. Choose 1 (Automatic Install): it picks the right" 'White'
        Say '  Python, PyTorch and CUDA build for your GPU. It can take 10-30 minutes. Come back here when it is done.' 'White'
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', 'scripts\install.bat' -WorkingDirectory $wan -Wait
        if (-not (Get-ChildItem $wan -Directory | Where-Object { $_.Name -match '^(venv|\.venv|env|env_uv|env_conda)' })) {
            Warn "WanGP's installer didn't leave an environment in $wan. Run $wan\scripts\install.bat again, then setup."
        }
    }
    if (-not (Test-Path (Join-Path $wan 'wgp.py'))) { Bad "$wan has no wgp.py: that isn't a WanGP folder."; exit 1 }
    $cfg.wangp_path = $wan; Save-Config $cfg
}
Ok "WanGP: $wan"

# MCP: on with the web UI, loopback only, no file-system reads (FilmLab uploads what WanGP needs).
$wcfgFile = Join-Path $wan 'wgp_config.json'
$argFiles = @((Join-Path $wan 'args.txt'), (Join-Path $wan 'scripts\args.txt')) | Where-Object { Test-Path $_ }
foreach ($af in $argFiles) {
    $t = Get-Content $af -Raw
    if ($t -match '--mcp-allow-read-file-system') { Warn "$af lets MCP clients read any file on this PC (--mcp-allow-read-file-system). FilmLab doesn't need it; consider removing it." }
    if ($t -match '--mcp-host\s+(\S+)' -and $Matches[1] -notin @('127.0.0.1', 'localhost')) { Warn "$af serves MCP on $($Matches[1]), not only this PC. FilmLab expects 127.0.0.1." }
}
if (Test-Path $wcfgFile) {
    $w = Get-Content $wcfgFile -Raw | ConvertFrom-Json
    if ($w.mcp_port) { $wanPort = [int]$w.mcp_port }
    if (-not $w.mcp_webui) {
        if (Ask-YesNo "Turn on WanGP's MCP server (Configuration > Agents > Enable MCP with Web UI)? A backup of wgp_config.json is kept." $true) {
            Copy-Item $wcfgFile "$wcfgFile.filmlab-backup-$(Get-Date -Format yyyyMMddHHmmss)"
            $w | Add-Member -NotePropertyName mcp_webui -NotePropertyValue $true -Force
            if (-not $w.mcp_port) { $w | Add-Member -NotePropertyName mcp_port -NotePropertyValue 8000 -Force }
            $w | ConvertTo-Json -Depth 32 | Set-Content -Path $wcfgFile -Encoding UTF8
            Ok 'MCP turned on (takes effect when WanGP starts or restarts)'
        }
    }
} else {
    # Never started yet: no config file. Add the launch flag instead.
    $af = Join-Path $wan 'scripts\args.txt'
    $t = if (Test-Path $af) { Get-Content $af -Raw } else { '' }
    if ($t -notmatch '--mcp-webui') {
        Add-Content -Path $af -Value '--mcp-webui'
        Ok "MCP turned on with --mcp-webui in $af"
    }
}
$cfg.wangp_port = $wanPort; Save-Config $cfg
if (-not (Test-Mcp $wanPort)) {
    $launcher = Get-Launcher $wan
    if ($launcher -and (Ask-YesNo "Start WanGP now ($launcher)?" $true)) {
        Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', "`"$launcher`"" -WorkingDirectory (Split-Path $launcher -Parent)
        Info 'WanGP is starting in its own window (the first start can take several minutes).'
    }
    Wait-Until "WanGP's MCP server on 127.0.0.1:$wanPort" { Test-Mcp $wanPort } @(
        'Start WanGP (its run script, Pinokio or WanGP Manager) and wait until its window says it is running.',
        "If it is running already, restart it so the MCP setting applies (Configuration > Agents > Enable MCP with Web UI).")
}

# 5. The FilmLab distro
Head '5. FilmLab (Linux distro, director, backend, cockpit)'
$wsl = Get-Wsl
$repoWsl = ConvertTo-WslPath $Repo
if ($wsl.distros -notcontains $distro) {
    $distro = Ask-Text 'Name for the FilmLab Linux distro' $distro
    $drive = ($hw.disks | Sort-Object free_gb -Descending | Select-Object -First 1).drive
    $loc = Ask-Text 'Where to keep it (its disk grows with your films and backups)' "$($drive):\FilmLab\wsl"
    if ($wsl.distros -contains $distro) { Bad "A distro named $distro already exists; pick another name."; exit 1 }
    Say "  creating the distro $distro in $loc (Ubuntu 24.04; downloads about 500 MB)" 'Gray'
    & wsl.exe --install -d Ubuntu-24.04 --name $distro --location $loc --no-launch
    if ($LASTEXITCODE -ne 0) { Bad 'Creating the distro failed (see above).'; exit 1 }
    $cfg.distro = $distro; $cfg.distro_location = $loc; Save-Config $cfg
}
$cfg.distro = $distro; Save-Config $cfg
$marker = Invoke-Distro $distro 'test -x /usr/local/bin/uv && command -v node >/dev/null && id -u filmlab >/dev/null' -Root
if ($marker -ne 0) {
    Say '  system setup inside the distro (as root, once)' 'Gray'
    if ((Invoke-Distro $distro "bash '$repoWsl/scripts/install/distro-root.sh'" -Root) -ne 0) { Bad 'System setup failed (see above and the log).'; exit 1 }
    & wsl.exe --terminate $distro | Out-Null   # restart only this distro, so systemd and the default user apply
    if ((Invoke-Distro $distro "bash '$repoWsl/scripts/install/distro-root.sh' --linger" -Root) -ne 0) { Bad 'Enabling user services failed.'; exit 1 }
}
Say '  FilmLab setup inside the distro (as filmlab)' 'Gray'
if ((Invoke-Distro $distro "bash '$repoWsl/scripts/install/distro-user.sh' '$model'") -ne 0) { Bad 'FilmLab setup failed (see above and the log).'; exit 1 }
if ($wanPort -ne 8000) {
    $null = Invoke-Distro $distro "mkdir -p ~/.config/filmlab && f=~/.config/filmlab/filmlab.env && touch `$f && sed -i '/^FILMLAB_WANGP_URL=/d' `$f && echo FILMLAB_WANGP_URL=http://127.0.0.1:$wanPort/mcp >> `$f"
}
Ok 'FilmLab is installed'

# 6. Owner login
Head '6. Your FilmLab login'
if ((Invoke-Distro $distro "cd '$repoWsl/backend' && ~/filmlab/venv/bin/python -m app.cli has-owner") -eq 0) {
    Ok 'an owner login exists already'
} else {
    $user = Ask-Text 'Username for the cockpit' $env:USERNAME.ToLower()
    while ($true) {
        Info 'Choose a password of 12 or more characters (typed twice; nothing is shown).'
        & wsl.exe -d $distro -- bash -lc "cd '$repoWsl/backend' && ~/filmlab/venv/bin/python -m app.cli set-owner --username '$user'"
        if ($LASTEXITCODE -eq 0) { break }
    }
}

# 7. Start
Head '7. Starting FilmLab'
& (Join-Path $Repo 'scripts\filmlabctl.ps1') start
$url = "http://127.0.0.1:$CockpitPort"
$up = $false
for ($i = 0; $i -lt 30 -and -not $up; $i++) { Start-Sleep 2; $up = (Test-Http "$url/") -or (Test-Http "https://127.0.0.1:$CockpitPort/") }
if ($up) { Ok "FilmLab is running: $url" } else { Warn 'FilmLab did not answer yet; see: scripts\filmlabctl.ps1 logs' }

# 8. Models
Head '8. Models'
Info 'WanGP downloads each model the first time it is used. Setup can do that now, so your first shot is fast:'
$null = Invoke-Distro $distro "cd '$repoWsl/backend' && ~/filmlab/venv/bin/python -m app.cli warmup"
if (Ask-YesNo 'Download the missing models now? (tens of GB; can take an hour or more; WanGP must stay running)' $true) {
    $null = Invoke-Distro $distro "cd '$repoWsl/backend' && ~/filmlab/venv/bin/python -m app.cli warmup --yes"
} else { Info 'Later: they download on first use, or run setup again.' }

# Take inspection: where the vision check's frames go is the owner's explicit choice (off until chosen).
if (-not $cfg.vision_asked) {
    Head 'Take inspection'
    Info 'After each render FilmLab checks the take. Technical checks (length, black or frozen picture, bad sound)'
    Info 'always run here. An optional vision check also looks at four frames of the take with an Ollama model:'
    $c = Ask-Choice 'Vision check?' @(
        'O=Off: no frames are sent anywhere (technical checks only; you can turn it on later in Settings)',
        "C=Cloud: four frames per take go to $DefaultModel on Ollama's servers (no GPU used here)",
        'L=Local: an Ollama vision model on this PC (frames stay here; uses GPU memory while rendering)')
    $vcmd = $null
    if ($c -eq 'c') { $vcmd = "vision cloud --model '$DefaultModel'" }
    elseif ($c -eq 'l') {
        $vm = Ask-Text 'Local vision model (it is pulled with Ollama if needed)' 'qwen2.5vl:7b'
        if ((Get-Ollama).models -notcontains $vm) { & $ollama.exe pull $vm }
        $vcmd = "vision local --model '$vm'"
    } else { $vcmd = 'vision off' }
    if ((Invoke-Distro $distro "cd '$repoWsl/backend' && ~/filmlab/venv/bin/python -m app.cli $vcmd") -eq 0) {
        $cfg.vision_asked = $true; Save-Config $cfg
    } else { Warn 'Could not save that choice; set it in the cockpit under Settings > Take inspection.' }
}

# 9. Optional: phone/LAN access over HTTPS
Head '9. Optional: use FilmLab from your phone or another PC'
if (-not $cfg.lan -and (Ask-YesNo 'Set up access from other devices on your home network (HTTPS with a private certificate)?' $false)) {
    $lan = Get-LanAddress
    if (-not $lan) { Warn 'No home network address found; skipping.' }
    else {
        Info "This PC: $($lan.ip) on $($lan.cidr) ($($lan.alias))"
        $host_ = $env:COMPUTERNAME.ToLower()
        if ((Invoke-Distro $distro "FILMLAB_LAN_IP=$($lan.ip) FILMLAB_LAN_CIDR=$($lan.cidr) FILMLAB_HOSTNAME=$host_ bash '$repoWsl/scripts/install/f1c-tls.sh'") -eq 0) {
            $origins = "https://$($lan.ip):$CockpitPort,https://127.0.0.1:$CockpitPort,https://localhost:$CockpitPort,https://$($host_):$CockpitPort,https://$host_.local:$CockpitPort"
            $envLines = "FILMLAB_HOST=0.0.0.0`nFILMLAB_SSL_CERTFILE=/home/filmlab/.config/filmlab/tls/server.crt`nFILMLAB_SSL_KEYFILE=/home/filmlab/.config/filmlab/tls/server.key`nFILMLAB_SECURE_COOKIES=1`nFILMLAB_ALLOWED_ORIGINS=$origins`nFILMLAB_ALLOWED_NETS=127.0.0.1/32,::1/128,$($lan.cidr)"
            $null = Invoke-Distro $distro "f=~/.config/filmlab/filmlab.env; touch `$f; sed -i -E '/^FILMLAB_(HOST|SSL_CERTFILE|SSL_KEYFILE|SECURE_COOKIES|ALLOWED_ORIGINS|ALLOWED_NETS)=/d' `$f; printf '%s\n' '$($envLines -replace "`n", "' '")' >> `$f"
            Info 'Next: a firewall rule for port 19300 from your home network only (Windows asks for administrator rights).'
            if (Ask-YesNo 'Add the firewall rule?' $true) {
                Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                    "`"$(Join-Path $Repo 'scripts\install\f1c-firewall.ps1')`"", '-Lan', $lan.cidr, '-Yes'
            }
            $ca = Join-Path $Repo 'local\filmlab-ca.crt'
            if ((Test-Path $ca) -and (Ask-YesNo 'Trust the FilmLab certificate on this PC (Windows asks to confirm)?' $true)) {
                Import-Certificate -FilePath $ca -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
            }
            & (Join-Path $Repo 'scripts\filmlabctl.ps1') restart
            $cfg.lan = $lan.cidr; Save-Config $cfg
            $url = "https://$($lan.ip):$CockpitPort"
            Ok "From your phone: $url"
            Info "Install $ca on each phone first (Android: Settings > Security > Install a certificate > CA certificate)."
        }
    }
}

# 10. Optional: start at logon
if (-not $cfg.autostart_asked) {
    if (Ask-YesNo 'Start FilmLab automatically when you log on to Windows?' $false) {
        & (Join-Path $Repo 'scripts\install\f7-startup-task.ps1') -Install
    }
    $cfg.autostart_asked = $true; Save-Config $cfg
}

# 11. Updates: a ZIP download can't update; connect it to the GitHub repository (the files stay as they are).
Update-Path
if (-not (Test-Path (Join-Path $Repo '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    Head '11. Updates'
    Info 'This FilmLab folder was downloaded, not cloned, so it cannot be updated with scripts\update.sh yet.'
    if (Ask-YesNo "Connect it to $FilmLabRepo for updates? (your files stay as they are)" $true) {
        Push-Location $Repo
        try {
            & git init -q -b main
            & git remote add origin $FilmLabRepo
            & git fetch -q origin main
            if ($LASTEXITCODE -eq 0) {
                & git reset -q origin/main   # history from GitHub; the folder's files are left untouched
                & git branch -q -u origin/main
                $changed = @(& git status --porcelain).Count
                if ($changed) { Warn "$changed file(s) differ from GitHub's latest version: run the update to bring them in line." }
                else { Ok 'connected: this folder matches the latest version on GitHub' }
            } else { Warn 'Could not reach GitHub; try again later by running setup.' }
        } finally { Pop-Location }
    }
}

Head 'Done'
Ok "FilmLab Studio: $url"
Info 'Start / stop later with:  scripts\filmlabctl.ps1 start | stop | status'
Info 'WanGP must be running (with MCP on) whenever you render.'
if (Ask-YesNo 'Open FilmLab in your browser now?' $true) { Start-Process $url }
