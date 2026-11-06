<#
  Optional: start FilmLab when you log on to Windows. NOT installed by default; FilmLab starts only when you
  run filmlabctl. This needs your explicit decision (roadmap: "optional Windows boot startup with owner opt-in").

  -Install  creates a per-user Scheduled Task "FilmLab Studio" that runs at your logon, 60 s after it
            (so WSL and WanGP come up first), and runs: filmlabctl.ps1 -Command start
            It only starts FilmLab's own units in the FilmLab distro; it doesn't touch other distros,
            WSL settings, the firewall or WanGP.
  -Remove   deletes that task again.
  -Status   shows whether it exists.

  Run from a normal (not elevated) PowerShell:  .\f7-startup-task.ps1 -Status
#>
param([switch]$Install, [switch]$Remove, [switch]$Status)
$ErrorActionPreference = "Stop"
$name = "FilmLab Studio"
$ctl = Join-Path (Split-Path -Parent (Split-Path -Parent $PSCommandPath)) "filmlabctl.ps1"

if ($Install) {
  if (-not (Test-Path $ctl)) { throw "filmlabctl.ps1 not found at $ctl" }
  $action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ctl`" -Command start"
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
  $trigger.Delay = "PT60S"
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Starts FilmLab Studio's own services in the FilmLab WSL distro at logon (opt-in)." | Out-Null
  "Installed: '$name' runs filmlabctl start 60 s after you log on. Remove with -Remove."
} elseif ($Remove) {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    "Removed '$name'. FilmLab starts only when you run filmlabctl again."
  } else { "No '$name' task; nothing to remove." }
} else {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if ($t) { "'$name' is installed ($($t.State)): FilmLab starts at logon." }
  else { "'$name' is not installed: FilmLab starts only when you run filmlabctl (default)." }
}
