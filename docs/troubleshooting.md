# 🩺 Troubleshooting

Start with **`Setup-FilmLab.cmd`**: it re-checks everything and repairs what's missing. To only look, run `scripts\install\Setup-FilmLab.ps1 -CheckOnly`. The cockpit's **Health & backups** page and `scripts\filmlabctl.ps1 status` / `logs` cover the rest.

- [Setup](#setup)
- [Starting FilmLab](#starting-filmlab)
- [WanGP and rendering](#wangp-and-rendering)
- [The director](#the-director)
- [Sound and dialogue](#sound-and-dialogue)
- [Phone and network access](#phone-and-network-access)
- [Getting help](#getting-help)

---

## Setup

<details>
<summary><b>"Windows protected your PC" when starting Setup-FilmLab.cmd</b></summary>

Windows SmartScreen warns about any unsigned script downloaded from the internet. Click **More info → Run anyway**. Setup then clears that mark from FilmLab's other scripts, so you'll only see it once.
</details>

<details>
<summary><b>"CPU virtualization looks off"</b></summary>

WSL needs your CPU's virtualization feature. Restart into your PC's BIOS/UEFI settings (usually **Del**, **F2** or **F10** at power-on) and enable **Intel Virtualization Technology (VT-x)** or **AMD SVM Mode**, save, and run setup again.
</details>

<details>
<summary><b>"NVIDIA driver … needs 580 or newer"</b></summary>

WanGP's recommended build uses CUDA 13. Update the driver from [nvidia.com/drivers](https://www.nvidia.com/drivers) or the NVIDIA app, restart, and run setup again.
</details>

<details>
<summary><b>"Port 19300 (or 19189) is already in use"</b></summary>

Another program is using a port FilmLab needs; setup names it. Close it (or stop an older FilmLab with `scripts\filmlabctl.ps1 stop`) and run setup again.
</details>

<details>
<summary><b>"WSL 2.4.4 or newer isn't ready yet"</b></summary>

Open **PowerShell as Administrator** and run `wsl --install --no-distribution` (no WSL yet) or `wsl --update` (older WSL). Setup notices by itself when it's done; if Windows asks you to restart, restart and run `Setup-FilmLab.cmd` again. Virtualization must be enabled in your BIOS/UEFI. Setup's report tells you if WSL itself fails to start.
</details>

<details>
<summary><b>Setup can't find WanGP</b></summary>

Choose **It is somewhere else** and type the folder that contains `wgp.py` (for Pinokio installs this is usually `%USERPROFILE%\pinokio\api\<wan…>\app`). Or choose **Install WanGP for me**.
</details>

<details>
<summary><b>WanGP's installer fails</b></summary>

That's WanGP's own installer; its window shows why. Common causes are an old NVIDIA driver (update it), a missing Git or Python (its installer offers to install them), or antivirus blocking downloads. Run `scripts\install.bat` in the WanGP folder again, then re-run FilmLab setup. See WanGP's [installation guide](https://github.com/deepbeepmeep/Wan2GP/blob/main/docs/INSTALLATION.md).
</details>

<details>
<summary><b>"Pulling gemma4:31b-cloud failed"</b></summary>

Cloud models need you to be signed in to Ollama: run `ollama signin`, finish in the browser, then run setup again. Or name a model you already have when setup asks.
</details>

<details>
<summary><b>"A distro named FilmLab already exists"</b></summary>

Setup reuses a FilmLab distro it created before. If a distro with that name belongs to something else, pick another name when setup asks.
</details>

## Starting FilmLab

<details>
<summary><b>The cockpit doesn't load</b></summary>

1. `scripts\filmlabctl.ps1 status`: both units should be *active* and port 19300 listening.
2. `scripts\filmlabctl.ps1 logs`: the backend log shows why it stopped.
3. With phone access set up, the address is **https**://…:19300; without it, **http**://127.0.0.1:19300.
</details>

<details>
<summary><b>FilmLab stops by itself a few seconds after a terminal closes</b></summary>

Start it with `filmlabctl.ps1 start` (not by opening a WSL shell): `start` keeps the distro alive until `stop` or sign-out.
</details>

<details>
<summary><b>"WanGP" or "Director" light is red</b></summary>

See [WanGP and rendering](#wangp-and-rendering) or [The director](#the-director). Both reconnect on their own once fixed.
</details>

## WanGP and rendering

<details>
<summary><b>"WanGP is not connected"</b></summary>

- WanGP must be running with **Configuration → Agents → Enable MCP with Web UI** on, then restarted.
- Its MCP server must be on port 8000 (or the port setup recorded) on `127.0.0.1`.
- WSL **mirrored networking** must be on, and WSL restarted since it was turned on: FilmLab reaches WanGP through `127.0.0.1`.
</details>

<details>
<summary><b>A template shows "not usable" or "model not downloaded"</b></summary>

FilmLab never downloads models on its own. Run the warm-up (`app.cli warmup --yes`, see [Running FilmLab](running.md#command-reference)), or use the model once in WanGP, then press **Check again** in **Settings**. "A LoRA this template needs isn't installed" means WanGP is missing a LoRA for that template; pick another template or install the LoRA in WanGP.
</details>

<details>
<summary><b>Renders fail with "CUDA out of memory"</b></summary>

Something else is using the GPU: a game, another WanGP job, or a local Ollama model. Close it, or use a smaller size or shorter shot. **Settings → GPU sharing → wait** makes FilmLab wait for a local Ollama model to unload.
</details>

<details>
<summary><b>A render shows no progress for a long time</b></summary>

FilmLab flags a render after 10 minutes without progress but never cancels it: WanGP may still be downloading a model on its first use. Check WanGP's window. Cancel from the Renders panel if it's really stuck.
</details>

<details>
<summary><b>A render says "Downloading…" for a long time</b></summary>

WanGP downloads a model's files the first time something renders with it, which can take many minutes for a large model. The render shows how much has arrived and how fast, and isn't marked as stalled meanwhile. **Watch in WanGP ↗** opens WanGP's own page with the exact progress. If no link appears, FilmLab didn't find WanGP's page: set `FILMLAB_WANGP_WEBUI` to its address (for example `http://127.0.0.1:8888/`) in FilmLab's environment and restart it.
</details>

## The director

<details>
<summary><b>"The director's reply came back empty"</b></summary>

The model's answer was lost: some models through Ollama write a tool call that never arrives. gemma4:31b-cloud does this with most plan_storyboard calls on some briefs, so the model writes the plan but Ollama drops it. It isn't your usage running out (that's a "Director unavailable" error) and Start fresh doesn't help.

- **When planning:** FilmLab asks the same model for the plan directly as JSON instead, part by part, and saves each part through the same checks. You'll see the note "FilmLab is asking the model for the plan directly instead".
- **In other turns:** it recovers once in a fresh conversation, by resending your message.
- **If it keeps happening:** press **Plan more** (your saved scenes stay) or **Start fresh** in the Director panel and try again.
</details>

<details>
<summary><b>"Director unavailable"</b></summary>

Ollama must be running, and for cloud models you must be signed in (`ollama signin`) with internet access. **Settings → Director → Test** checks the connection. While the director is down, everything else (rendering approved shots, review, edit and export) still works.
</details>

<details>
<summary><b>The director planned something odd</b></summary>

Edit or lock the shots you want to keep and re-plan, or ask in chat (*"keep shots 1–3, redo scene 2 as a single wide shot"*). Add standing instructions under **Settings → Director → notes**.
</details>

## Sound and dialogue

<details>
<summary><b>Wrong words, gibberish, or someone talks who shouldn't</b></summary>

- Open the shot's **Edit → Sound**. If it says *Sound not set*, choose **Nobody speaks** or add the exact lines (or press **✨ Enhance**).
- Keep lines short for the shot's length: about 2–3 words per second.
- Exact dialogue is fully supported on MiniMax H3 models; other audio models get the sound as a plain description.
</details>

<details>
<summary><b>A character's voice changes between shots</b></summary>

Give the character a **Voice** sample in the Library (or design one with the Creator). Characters without one get a new voice each time.
</details>

## Phone and network access

<details>
<summary><b>The phone says the connection isn't private</b></summary>

Install `local\filmlab-ca.crt` (from your FilmLab folder) on the phone as a **CA certificate**, not as a VPN/app or Wi-Fi certificate. On iPhone, also enable it under *Certificate Trust Settings*.
</details>

<details>
<summary><b>The phone can't reach FilmLab at all</b></summary>

- The phone must be on the same home network as the PC.
- The firewall rule must exist: re-run the phone access step in setup.
- If your PC's address changed, re-run setup's phone access step to reissue the certificate.
</details>

## Getting help

When you open an issue, include:

- the output of `scripts\install\Setup-FilmLab.ps1 -CheckOnly`;
- the end of `%LOCALAPPDATA%\FilmLab\setup.log` (for setup problems) or of `scripts\filmlabctl.ps1 logs` (for everything else);
- what you did and what you expected.

> [!CAUTION]
> Logs can contain your project names and prompts. Check them before posting. FilmLab never writes passwords or tokens to its logs.
