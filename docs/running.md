# ▶️ Running FilmLab

Day-to-day operation: starting and stopping, using FilmLab from your phone, sharing the GPU, backups, updates and health checks.

Commands below assume FilmLab lives in `C:\FilmLab` and its distro is called `FilmLab`. Use your own folder and name if you chose different ones.

- [Start and stop](#start-and-stop)
- [Order of things](#order-of-things)
- [From your phone](#from-your-phone)
- [Sharing the GPU](#sharing-the-gpu)
- [Backups and restore](#backups-and-restore)
- [Updates and rollback](#updates-and-rollback)
- [Health and cleanup](#health-and-cleanup)
- [Changing your password](#changing-your-password)
- [Command reference](#command-reference)

---

## Start and stop

Everything is controlled with **`scripts\filmlabctl.ps1`** in PowerShell:

```powershell
C:\FilmLab\scripts\filmlabctl.ps1 start     # start FilmLab (director gateway + backend)
C:\FilmLab\scripts\filmlabctl.ps1 status    # what's running and listening
C:\FilmLab\scripts\filmlabctl.ps1 logs      # backend log (add -Service gateway for the director)
C:\FilmLab\scripts\filmlabctl.ps1 stop      # stop FilmLab and its distro
```

Then open **http://127.0.0.1:19300** (or your HTTPS address if you set up phone access).

> [!NOTE]
> `start` keeps a small hidden WSL session open, because WSL otherwise stops an idle distro after a few seconds. `stop` ends it and shuts down **only** the FilmLab distro. Your other WSL distros keep running. FilmLab never runs `wsl --shutdown`.

Nothing starts with Windows unless you chose **Start at logon** during setup (`scripts\install\f7-startup-task.ps1 -Install` / `-Remove`).

## Order of things

1. **WanGP**, with its MCP server on (*Configuration → Agents → Enable MCP with Web UI*). Needed for rendering.
2. **Ollama**, running in the tray. Needed for the director and for take inspection.
3. **FilmLab**: `filmlabctl.ps1 start`.

The order isn't strict: FilmLab reconnects on its own when WanGP or Ollama start or restart. The status lights in the cockpit's top bar show **Events**, **Director** and **WanGP** (with its queue), and the **GPU** and **VRAM** meters show live load.

## From your phone

If you set up phone access, open `https://<your PC's address>:19300` on any device that trusts FilmLab's certificate. The cockpit adapts to small screens: the project view switches between **Storyboard**, **Director** and **Renders** with tabs.

- Turn on **🔔 notifications** in the top bar on each device to get a notification when a render finishes while FilmLab isn't on screen.
- **Ctrl + K** (or the 🔍 button) jumps to any project, shot, page or Library item.
- After adding a device, run **Health & backups → LAN self-test** on it.

If your PC's address changes (e.g. a new router), run `Setup-FilmLab.cmd` again and redo the phone access step. Devices keep trusting the same certificate authority.

## Sharing the GPU

FilmLab renders one thing at a time and waits behind any WanGP jobs of your own.

- **Hold GPU work** (in the cockpit) pauses FilmLab: batches pause, director runs stop and new renders are refused until you release it. Optionally it also cancels FilmLab's running renders. Your own WanGP jobs are never touched.
- **Settings → GPU sharing → wait** makes FilmLab wait while a local Ollama model is holding VRAM.

## Backups and restore

- **Health & backups → Back up now**, plus one automatic backup a day while FilmLab runs (the last 10 are kept). A backup holds the database, all media and library files (unchanged files are hard-linked, so backups are cheap) and a checksum manifest. Passwords, certificates and secrets are **not** included.
- **Verify** checks files and sizes; **Deep verify** re-hashes everything; **Restore drill** rebuilds your newest export from the backup alone and compares it frame by frame. None of these change anything.
- Backups live inside the distro (`~/filmlab/backups`). To keep a copy outside it, set **Backups → copy folder**, e.g. `/mnt/d/FilmLabBackups`.

**Restore** from a backup (it checks the backup, asks, keeps your current data aside and restarts):

```powershell
wsl -d FilmLab -- bash /mnt/c/FilmLab/scripts/restore.sh <backup name>
```

Sign in again afterwards.

## Updates and rollback

FilmLab never fetches or updates by itself.

```powershell
git -C C:\FilmLab fetch
wsl -d FilmLab -- bash /mnt/c/FilmLab/scripts/update.sh origin/main
```

`update.sh` refuses while renders or exports run (or if you changed files in the FilmLab folder), copies the database, records the current version, switches to the new one, rebuilds the cockpit, reinstalls the director's instructions, restarts and checks health. New database migrations run on start, with their own safety copy.

To go back to the version before the last update (and its database, if a migration ran):

```powershell
wsl -d FilmLab -- bash /mnt/c/FilmLab/scripts/rollback.sh
```

## Health and cleanup

**Health & backups** shows:

- database integrity and missing or unused files;
- storage by kind (takes, exports, library, caches, backups) and free space; new GPU work is refused below 5 GB free;
- FilmLab's jobs compared with WanGP's queue;
- VRAM use and any local Ollama models that are loaded;
- the self-tests.

**Cleanup** removes only caches and unused files, and only the ones it lists first.

## Changing your password

```powershell
wsl -d FilmLab -- bash -lc "cd /mnt/c/FilmLab/backend && ~/filmlab/venv/bin/python -m app.cli set-owner --username you"
```

This signs out every device.

## Command reference

| What | Command |
|---|---|
| Start / stop / status | `scripts\filmlabctl.ps1 start` · `stop` · `status` |
| Restart only the backend | `scripts\filmlabctl.ps1 restart-backend` (check no render is running first) |
| Logs | `scripts\filmlabctl.ps1 logs [-Service backend\|gateway] [-Lines 200]` |
| Re-run / repair setup | `Setup-FilmLab.cmd` |
| What's installed | `scripts\install\Setup-FilmLab.ps1 -CheckOnly` |
| Models: list / download missing | in the distro: `~/filmlab/venv/bin/python -m app.cli warmup [--yes]` (from `backend/`) |
| Update / rollback | `scripts/update.sh <ref>` · `scripts/rollback.sh` (in the distro) |
| Restore a backup | `scripts/restore.sh <backup name>` (in the distro) |
| Open a shell in the distro | `wsl -d FilmLab` |
