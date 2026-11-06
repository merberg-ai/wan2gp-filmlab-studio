# 📦 Installation

FilmLab Studio installs with a guided **setup assistant** that checks your PC first, explains anything that's missing, and asks before every change. This page walks through it step by step, then covers manual installation, updating and uninstalling.

- [Before you start](#before-you-start)
- [Run the setup assistant](#run-the-setup-assistant)
- [Step by step](#step-by-step)
- [After setup](#after-setup)
- [Manual installation](#manual-installation)
- [Updating](#updating)
- [Uninstalling](#uninstalling)

---

## Before you start

| You need | Notes |
|---|---|
| **Windows 11** 22H2 or newer | Needed for WSL mirrored networking |
| **NVIDIA RTX GPU**, 16 GB+ VRAM recommended | 12 GB works for small shots |
| **32 GB RAM** (64 GB recommended) | |
| **~150 GB free disk** | Models take 50–100 GB; your films and backups grow over time |
| **An internet connection** | For setup, models and the director |
| **A free Ollama account** | For the default cloud director model |
| **CPU virtualization on** | Usually on already; setup tells you if it's off (it's a BIOS/UEFI setting) |
| **NVIDIA driver 580 or newer** | For WanGP's recommended build; setup checks it |

> [!NOTE]
> **Starting from a brand-new PC is fine.** You don't need Git, WSL, Ollama or WanGP first: setup detects each one, tells you exactly what to install and where to get it, waits while you do (it notices by itself when it's done), and installs everything else.

## Run the setup assistant

1. **Get FilmLab** into the folder where it should live, on any drive. This folder stays in use, so don't put it in Downloads, a temp folder or OneDrive (setup warns if you do).
   - **No Git yet (a fresh PC):** on GitHub click **Code → Download ZIP** and extract it to e.g. `C:\FilmLab`. At the end, setup offers to connect the folder to GitHub so you can update later.
   - **With Git:**
     ```powershell
     git clone https://github.com/merberg-ai/wan2gp-filmlab-studio.git C:\FilmLab
     ```

2. **Double-click `Setup-FilmLab.cmd`** in that folder. If Windows shows *"Windows protected your PC"*, click **More info → Run anyway**. The script isn't code-signed; it's this repository's own, and you can read it in `scripts\install\Setup-FilmLab.ps1`.

   It opens a console window and starts with a report of your PC and what's installed. Answer each question with **Enter** for the suggested answer, or type your choice. When it's waiting for something you're installing, it checks again every few seconds by itself; press **Q** to stop and run it again later.

> [!TIP]
> Want to see the report without changing anything? In PowerShell, from the FilmLab folder:
> ```powershell
> .\scripts\install\Setup-FilmLab.ps1 -CheckOnly
> ```

## Step by step

### 1. This PC

Setup reports Windows, your CPU, memory, every GPU with its VRAM and driver, and free space on each drive. It warns if something is below the recommended level and stops if something essential (Windows 11, an NVIDIA GPU) is missing. You can continue anyway. It also checks that:

- **CPU virtualization is on** (WSL needs it). If it's off, turn on *Intel VT-x* / *AMD SVM* in your PC's BIOS/UEFI.
- **The NVIDIA driver is 580 or newer.** If not, update it from [nvidia.com/drivers](https://www.nvidia.com/drivers) or the NVIDIA app before WanGP is installed.
- **FilmLab's ports (19300, 19189) are free**, and the FilmLab folder isn't in OneDrive, Downloads or a temp folder.

### 2. WSL

FilmLab's backend runs in its own small Linux distro under **WSL 2** (version 2.4.4 or newer).

- **WSL missing or too old?** Setup shows you the command and waits. Open **PowerShell as Administrator** and run:
  ```powershell
  wsl --install --no-distribution
  ```
  (or `wsl --update` if WSL is just old). Restart Windows if asked, then press **Enter** in setup to check again.
- **Mirrored networking.** FilmLab reaches WanGP and Ollama on your PC through WSL's *mirrored networking*. If it's off, setup offers to add it to `%USERPROFILE%\.wslconfig` (it keeps a backup). WSL must restart for it to apply; setup asks before doing that, because restarting WSL stops every running Linux distro.

### 3. Ollama

The director's model runs through [Ollama](https://ollama.com).

- **Ollama missing?** Install it from **https://ollama.com/download/windows** (or run `winget install --id Ollama.Ollama`), start it once, then press **Enter** in setup.
- **The director model.** The default, `gemma4:31b-cloud`, is an Ollama **cloud** model: it runs on Ollama's servers, not your GPU, and needs a free Ollama account. Setup offers to run `ollama signin` (opens your browser) and then pulls the model. You can name a different model instead, including a local one.

### 4. WanGP

[WanGP](https://github.com/deepbeepmeep/Wan2GP) renders everything FilmLab makes.

- **Already installed?** Setup looks in the usual places (including Pinokio's `api` folder) and lists what it finds. Pick one, or type the path of the folder that contains `wgp.py`.
- **Not installed?** Choose **Install WanGP for me**. Setup clones WanGP into a folder you choose, then opens **WanGP's own installer** in a new window: choose **1. Automatic Install**. It picks the right Python, PyTorch and CUDA build for your GPU. This takes 10–30 minutes.
- **MCP server.** FilmLab talks to WanGP through WanGP's MCP server on `127.0.0.1:8000`. Setup turns on **Configuration → Agents → Enable MCP with Web UI** for you (keeping a backup of `wgp_config.json`). It also warns if your WanGP launch options let MCP clients read files on your PC or serve MCP beyond this PC; FilmLab needs neither.
- **Start WanGP.** Setup offers to start it and waits until its MCP server answers. The first start can take several minutes.

### 5. FilmLab

Setup creates a Linux distro (default name **FilmLab**, stored on your emptiest drive; you can change both) and installs inside it:

- Ubuntu 24.04 with Node.js 24, Python 3.12, uv and ffmpeg;
- a user `filmlab` with no administrator rights;
- the **director**: OpenClaw with a locked-down tool policy and its gateway on loopback only;
- the **backend** (a Python virtual environment) and the **web cockpit**.

This takes a few minutes. Your FilmLab folder stays on Windows; the distro uses it in place.

### 6. Your login

Choose a username and a password of **12 or more characters**. This is the only login; anyone with it can use everything, so keep it to yourself.

### 7. Start

Setup starts FilmLab and checks that the cockpit answers on **http://127.0.0.1:19300**.

### 8. Models

WanGP downloads each model the first time it's used. Setup lists the six models FilmLab uses by default and which are missing:

| Used for | Default model |
|---|---|
| Video shots with references | MiniMax H3 Ref2VA Pruned PDD 8 Steps |
| Video shots without references | MiniMax H3 FL2VA Pruned PDD 8 Steps |
| Reference images from text | Z Image Turbo |
| Reference images from other images | Flux Klein 9B |
| Character voices (Creator) | Qwen3 1.7B (voice design) |
| Music (Creator) | ACE-Step 1.5 Turbo LM 1.7B |

Say **yes** and setup runs one tiny generation per missing model, which makes WanGP download it. Together this can be tens of GB and take an hour or more. Keep WanGP running. Say **no** and they download the first time you use them instead.

### 9. Phone access (optional)

Use FilmLab from your phone, tablet or another PC on your home network:

1. Setup detects your PC's address and home network, and creates a **private certificate authority** limited to that network and this PC's name.
2. It adds a **firewall rule** for port 19300 from your home network only (Windows asks for administrator rights).
3. It offers to trust the certificate on this PC, then restarts FilmLab on HTTPS.
4. **On each phone**, install `local\filmlab-ca.crt` from your FilmLab folder:
   - **Android:** *Settings → Security → Encryption & credentials → Install a certificate → CA certificate*
   - **iPhone/iPad:** open the file, install the profile, then enable it in *Settings → General → About → Certificate Trust Settings*
5. Open the address setup shows you (e.g. `https://192.168.x.y:19300`) and run **Health & backups → LAN self-test**.

FilmLab is never reachable from the internet. Don't forward port 19300 on your router.

### 10. Start at logon (optional)

Adds a per-user Windows task that starts FilmLab a minute after you log on. Without it, start FilmLab yourself (see [Running FilmLab](running.md)).

### 11. Updates

If you downloaded FilmLab as a ZIP, setup offers to connect the folder to this GitHub repository (it needs Git, which setup will have asked you to install for WanGP). Your files stay exactly as they are; it only adds the version history, so `scripts/update.sh` can update FilmLab later. A cloned folder skips this step.

## After setup

- Open the cockpit, sign in, and follow [Make your first film](../README.md#-make-your-first-film).
- Bookmark **http://127.0.0.1:19300** (or your HTTPS address).
- Before rendering, **start WanGP** with MCP on. FilmLab reconnects on its own when WanGP starts.

Your choices are saved in `%LOCALAPPDATA%\FilmLab\config.json` and every step is logged to `%LOCALAPPDATA%\FilmLab\setup.log`.

## Manual installation

<details>
<summary><b>Install everything by hand</b></summary>

All commands assume FilmLab is in `C:\FilmLab` (`/mnt/c/FilmLab` inside WSL). The scripts find their own folder, so any location works.

1. **Prerequisites:** WSL 2.4.4+, mirrored networking in `%USERPROFILE%\.wslconfig`:
   ```ini
   [wsl2]
   networkingMode=mirrored
   ```
   Ollama running with the director model (`ollama signin`, `ollama pull gemma4:31b-cloud`), and WanGP running with **Enable MCP with Web UI** on port 8000.

2. **Create the distro:**
   ```powershell
   wsl --install -d Ubuntu-24.04 --name FilmLab --location D:\FilmLab\wsl --no-launch
   wsl -d FilmLab -u root -- bash /mnt/c/FilmLab/scripts/install/distro-root.sh
   wsl --terminate FilmLab
   wsl -d FilmLab -u root -- bash /mnt/c/FilmLab/scripts/install/distro-root.sh --linger
   ```

3. **Install FilmLab inside it:**
   ```powershell
   wsl -d FilmLab -- bash /mnt/c/FilmLab/scripts/install/distro-user.sh gemma4:31b-cloud
   ```

4. **Create your login:**
   ```powershell
   wsl -d FilmLab -- bash -lc "cd /mnt/c/FilmLab/backend && ~/filmlab/venv/bin/python -m app.cli set-owner --username you"
   ```

5. **Start:** `C:\FilmLab\scripts\filmlabctl.ps1 start`, then open http://127.0.0.1:19300.

6. **Optional models:** `wsl -d FilmLab -- bash -lc "cd /mnt/c/FilmLab/backend && ~/filmlab/venv/bin/python -m app.cli warmup --yes"`

</details>

## Updating

FilmLab never updates itself. To update, **fetch** the new version and let the update script move to it. It refuses while renders run, copies the database, records where you were (for rollback), rebuilds the cockpit, updates the director's instructions and restarts:

```powershell
git -C C:\FilmLab fetch
wsl -d FilmLab -- bash /mnt/c/FilmLab/scripts/update.sh origin/main
```

Use a release tag instead of `origin/main` (e.g. `v1.1.0`) to update to a specific release.

If something goes wrong, `scripts/rollback.sh` returns to the previous version (and database). See [Running FilmLab → Updates](running.md#updates-and-rollback).

## Uninstalling

> [!WARNING]
> Unregistering the distro **permanently deletes** everything in it: your projects, takes, library, exports and backups. Copy out what you want to keep first.

1. Copy any backups or exports you want out of the distro (`\\wsl$\FilmLab\home\filmlab\filmlab\backups` and `...\media`).
2. Stop FilmLab: `scripts\filmlabctl.ps1 stop`.
3. If you set up phone access, remove the firewall rule from an **elevated** PowerShell: `scripts\install\f1c-firewall.ps1 -Remove`, and remove the *FilmLab Studio Local CA* certificate from each device.
4. If you enabled autostart: `scripts\install\f7-startup-task.ps1 -Remove`.
5. Delete the distro: `wsl --unregister FilmLab`.
6. Delete `%LOCALAPPDATA%\FilmLab` and the FilmLab folder.

WanGP, Ollama and their models are left untouched. Remove them separately if you no longer need them.
