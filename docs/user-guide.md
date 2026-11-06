# 🎬 User guide

How to make a film with FilmLab Studio, screen by screen.

- [The cockpit](#the-cockpit)
- [Projects](#projects)
- [The Library](#the-library)
- [The Creator](#the-creator)
- [Planning with the director](#planning-with-the-director)
- [The storyboard](#the-storyboard)
- [Shots](#shots)
- [Sound, dialogue and voices](#sound-dialogue-and-voices)
- [Rendering](#rendering)
- [Reviewing takes](#reviewing-takes)
- [Autonomy and redo runs](#autonomy-and-redo-runs)
- [Edit and export](#edit-and-export)
- [Settings](#settings)
- [Tips for better results](#tips-for-better-results)

---

## The cockpit

| Area | What it's for |
|---|---|
| **Top bar** | Pages (Projects, Library, Settings, Health & backups, Recently deleted), status lights for **Events**, **Director** and **WanGP** (with its queue), live **GPU** and **VRAM** meters, 🔍 **Go to** (Ctrl + K), 🔔 notifications, sign out |
| **Project view** | Three panels: **Storyboard**, **Director** chat, **Renders**. On a phone they become tabs |

Everything updates live: progress, new takes and the director's replies appear without reloading. A toast tells you when a render finishes; with 🔔 on, you also get a system notification when FilmLab isn't on screen.

## Projects

**Projects → New project.** Give it a title and, optionally, a template:

| Template | Good for |
|---|---|
| Horror teaser | Short, tense scenes with a scare |
| Music video | Shots cut to a song from the Library |
| Illustrated story | A story told in a consistent illustrated style |
| Character dialogue | People talking, one line per shot |

A template pre-fills the brief's hints, the style and sensible defaults. Open **⚙ Settings** in the project header (or click any of the chips next to it) to change:

- the title and logline;
- the default **size and length** of new shots;
- the **autonomy level** (see [below](#autonomy-and-redo-runs));
- the **video models** this project uses for shots with and without references.

## The Library

The Library holds everything your films reuse: **characters, locations, props, styles and sounds**.

- **Looks:** an asset can have several looks (e.g. *default*, *night*, *wounded*), each with **versions**. Lock a look to freeze it: shots that use it keep using exactly those images.
- **Images:** upload PNG, JPEG or WebP references, or generate them with WanGP from a look's **Generate** panel (from text, or from your existing images to keep identity). Generated **candidates** wait for you to **Keep** or **Discard** them.
- **Voices:** a character can have a **Voice** sample: 5–15 seconds of one person speaking clearly (WAV, MP3 or FLAC). Whenever that character has a line, FilmLab uses it as their voice.
- **Director requests:** when the director needs an image the Library lacks, its request appears on the asset for you to **Generate** or **Reject**.

Use the names you give assets in your briefs (e.g. `Anthony_science`, `Jim_lab`). The director matches them.

## The Creator

**Library → ✨ Create with the director…** makes a whole new asset from a description.

1. Pick the kind: character, location, prop, style, or **sound** (music). Describe it in your own words, and pick the image models (the first image is made from text; the other views are edits of it, so it stays the same subject). For characters, tick **Design a voice too**.
2. The **director drafts it**: a name, a description of what must stay consistent, two to four image views (the first is the *anchor*), and, for characters, a voice description and a sample line. Edit anything, or press **Redraft**.
3. **Generate** each part when you're ready (every step asks before using the GPU):
   1. **Anchor**: keep the best one;
   2. **Other views**, made from the kept anchor;
   3. **Voice**: play the takes and keep one;
   4. **Music**, for sounds.
4. **Approve into the library.** Until then the draft is hidden from the Library, the director and every shot. **Discard** sends it to Recently deleted.

You can also just ask the director in a project chat (*"we need a lab cat"*). Its draft waits under the Create button for you.

## Planning with the director

Open a project's **Brief** (top of the storyboard), write what the film is about, and press **Plan with director**.

- Mention your Library assets **by name**, the setting, the style, and anything that must happen. Give the **target length**.
- The director plans **scene by scene**: each scene's goal, time and place and mood, and its shots, each with a prompt, references, length, transition, **sound** and a reason. You can watch every step in the **Director** panel.
- **Re-plan drafts** replaces draft shots that have no takes. Locked, approved and rendered shots always stay. **Plan more** adds after the current shots.
- **Chat** with the director for anything else: *"make shot 3 a close-up"*, *"add a scene where…"*. It edits drafts and explains each change in the shot's **History**.

> [!NOTE]
> The director can only use FilmLab's own tools. It can draft and edit shots and ask for images, but it can't render, spend GPU time, or change your settings: those are always your clicks.

## The storyboard

- The **film strip** at the top shows every shot as a frame sized by its length, grouped by scene, with the take the edit uses. Click a frame to open the shot; drag to reorder. A coloured bar shows each shot's state, and the running total is compared with your target length.
- Below it are the **scenes** (edit their goal, time and place and mood; reorder or delete them) and their **shot cards**.
- **Render storyboard…** reviews every shot with WanGP first (what will render, what can't and why, how long it should take) and then renders your selection as a **batch**, one shot at a time, in order. Pause, resume or cancel it from the batch bar, and retry failed shots.

## Shots

A shot card shows its prompt, its **sound** summary, its references, and the director's latest reason. Its buttons:

| Button | What it does |
|---|---|
| **Edit** | Title, your idea, the picture prompt, **Sound**, length, size, transition into the next shot, model, preset |
| **✨ Enhance** | The director picks references (if there are none) and rewrites the prompt from your idea, naming each reference as `<Picture N>` |
| **Validate** | Asks WanGP whether it can render the shot, and at what size and length |
| **History** | Every change to the shot: who made it, when and why |
| **Lock** | The director can't change it and batches don't re-render it |
| **Approve & render** / **Render new take** | Queues it in WanGP |

**References:** each shot has up to eight reference images, in order. In the prompt they're `<Picture 1>`, `<Picture 2>`, … FilmLab warns before a render if the prompt names a picture the shot doesn't have, or doesn't state the scene's setting. Every shot is rendered on its own, so its prompt must say where and when it is.

**Transitions:** cut, dissolve, fade through black or white, wipes, slides, circle open/close, clock wipe, zoom, blur, pixelate and squeeze. They render in exports; the in-browser preview shows them as a fade.

## Sound, dialogue and voices

FilmLab's video models make the picture **and** the sound together. Every shot has a **Sound** section:

- **Nobody speaks**, or **Someone speaks**, with one or more lines: **who** (a character from the shot's references, or e.g. *a radio announcer* off screen), the **exact words**, and **how** they're said (*whispering, amazed*). FilmLab warns if the lines are too long for the shot.
- **Sounds of the place and the action**, e.g. *servo whirr, crackling relays, low hum*.
- **Music**, the score the audience hears (empty for none).
- **Refine audio**: an extra audio pass, on models that support it.

FilmLab writes this into the MiniMax H3 model's own script format: each line becomes an exact, tagged line of dialogue, and "nobody speaks" is stated explicitly. So words come out as written, and silent shots stay silent. A speaking character with a **voice sample** in the Library speaks in that voice (up to three voices per shot).

> [!TIP]
> Keep each line short: about 2–3 words per second of shot. One line per shot works best.

## Rendering

The **Renders** panel shows what's rendering, WanGP's queue, progress and an **estimate of the time left** (from this PC's past renders of the same model), then the shot's takes.

- With a shot selected, the panel shows only its takes; **Show all** goes back to every shot. A note tells you when another shot gets a new take.
- Renders queue in WanGP behind anything else it's doing. FilmLab never cancels or reorders other WanGP jobs.

## Reviewing takes

Every new take is **inspected** automatically: technical checks (length, black or frozen picture, silent or clipping sound) and a vision check of four frames against the prompt. A take that looks wrong is **flagged ⚠**. Inspection is advice; it never deletes anything.

- Mark each take **👍 Good**, **🤔 Maybe** or **👎 Reject**, and add notes on what works and what doesn't.
- **Use in edit** picks the take the edit uses (by default the newest good one).
- **Compare A/B…** plays two takes side by side, in sync.
- After **👎 Reject**, a card offers to **Render again** (new random seed; *Add to queue* if something's rendering), **Edit the shot first**, or **Ask the director to fix it**: it looks at the take and your notes and proposes a revised prompt for you to accept.

## Autonomy and redo runs

Each project has an autonomy level (Project settings):

| Level | The director can… |
|---|---|
| **A0 · Draft** | plan and propose only |
| **A1 · Approved shots** | ask you to approve each render |
| **A2 · Approved batches** | ask for batches you start; propose redos you accept |
| **A3 · Bounded redo** | additionally, you can start **redo runs**: FilmLab renders the director's fixes for flagged shots by itself, within limits you set (redos per shot, total renders, GPU minutes, time) |

Nothing in chat can raise these limits, and **Hold GPU work** stops everything at once.

## Edit and export

The **Edit & export** tab assembles the film from each shot's chosen take, in storyboard order:

- **Trim** each clip's in and out points, skip clips, set its audio gain or mute it, and set transition lengths.
- **Music:** up to two tracks from the Library's sounds, with start, offset, gain, fades and automatic **ducking** under dialogue.
- **Titles and captions:** title cards (start, end or between shots) and timed captions.
- **Preview** in the browser, then **export**:
  - a **Draft** for a quick look;
  - a **Final**, with loudness normalised to −14 LUFS and optional burned-in captions.

  Exports are MP4 (H.264/AAC) with an SRT caption file and a manifest of every source used.
- The **Contact sheet** shows every shot's frames and inspection at a glance.

## Settings

| Section | What you set |
|---|---|
| **Models** | The default WanGP templates for video (with and without references), reference images, voice design and music; and every installed template with whether it's usable (**Check again** after downloading models in WanGP) |
| **Presets** | Ready-made size, length and model combinations, plus your own |
| **Director** | The director's model, your standing notes for it, how it replies, how many steps it may take, and a connection test |
| **GPU sharing** | Whether to wait while a local Ollama model holds VRAM |

## Tips for better results

- **Name things consistently.** Use Library names in the brief, and give characters clear, distinct looks.
- **State the setting in every shot.** Each shot is rendered alone; the model doesn't know the scene unless the prompt says so. Enhance does this for you.
- **One action per shot, one line per shot.** Short shots (3–6 s) with a clear beginning and end come out best.
- **Lock what you love.** Locked shots and looks survive re-plans and batches.
- **Review every take.** Faces, hands, text and fast motion can drift. Reshoot rather than fight a bad take.
