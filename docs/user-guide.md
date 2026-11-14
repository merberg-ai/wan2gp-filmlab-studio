# 🎬 User guide

How to make a film with FilmLab Studio, screen by screen.

- [The cockpit](#the-cockpit)
- [Console](#console)
- [Projects](#projects)
- [The Library](#the-library)
- [Create](#create)
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
| **Top bar** | Pages (Projects, Library, Settings, Health & backups, Recently deleted: restore an item, delete it forever, or **Empty Recently deleted…** for everything at once), the FilmLab version, a **WanGP** status light with its queue (and **Events** when live updates are down), live **GPU** and **VRAM** meters, 🔍 **Go to** (Ctrl + K), **>_ Console** (the ` key), 🔔 notifications, sign out |
| **Project view** | Three panels: **Storyboard**, **Director** chat, **Renders**. On a phone they become tabs |

Everything updates live: progress, new takes and the director's replies appear without reloading. A toast tells you when a render finishes; with 🔔 on, you also get a system notification when FilmLab isn't on screen.

On a wide screen the project's header is one compact row, the **timeline** (length meter and film strip) sits in its own card above the panels so it's always in view, and the three panels (Storyboard, Director, Renders) fill the rest of the window and scroll inside. They can be **resized** by dragging the bars between them (double-click one to reset). **Panels:** in the project header hides or shows each one (a hidden panel is a thin bar you click to bring back), **Focus…** shows one panel only, and **Reset** brings back the default. Your layout is remembered in this browser. Phones show one panel at a time, on tabs.

## Console

**>_** in the top bar (or the ` key) opens the **Console**: what FilmLab is doing in the background, live, one line each. **Full page ↗**, or **Go to → Console**, opens it as a page.

- **What appears:** renders and generations (sent, WanGP's status line, done, failed, stalled), the director's steps and tool calls, plan parts saved or refused (and what FilmLab repaired), WanGP stopping and coming back, take inspections, and FilmLab's own warnings and errors.
- **Each line:** a time, a level (info, warn, error) and a source (Director, Plan, Render, Create, WanGP, System). Lines with a ▸ open their details, such as the exact plan the director sent and why it was refused, with **Copy**.
- **Filters:** **All · Warnings · Errors**, a source, **This project** (inside a project) and search. **Pause** stops the scrolling; **Load older lines** goes back in time.
- **Errors while it's closed:** a red count appears on **>_**.

## Projects

Each project's card on the Projects page shows a picture: the take you chose with **☆ Set as cover**, else the take the edit uses for the opening shot, else the first shot's reference image, else a placeholder. Hover over it to play the cover take. The card also shows the project's template, a **Rendering** badge while something renders, and how many shots there are, how many are rendered and how long the film is.

Above the cards, search by title or logline, filter by type (template) and stage (not planned, planned, rendering, has renders), and sort (updated, newest, name, most shots, longest). Your choices are remembered in this browser.

**+ New project** opens a form: a title, an optional logline, and a starting point, which is a blank project or a template. **Create and open** takes you straight to it.

| Template | Good for |
|---|---|
| Horror teaser | Short, tense scenes with a scare |
| Music video | Shots cut to a song from the Library |
| Illustrated story | A story told in a consistent illustrated style |
| Character dialogue | People talking, one line per shot |

A template pre-fills the brief's hints, the style and sensible defaults. Open **⚙ Settings** in the project header (or click any of the chips next to it) to change:

- the title and logline;
- **Generation (advanced)**: this project's own values for what WanGP's Advanced mode offers, for each of its video models: inference steps, guidance, sampler and shift, negative prompt and NAG, step-skipping caches, quality options, upscaling, frame interpolation and film grain, the sliding window for long shots, and LoRAs. Open a model to see its settings: FilmLab asks WanGP what that model has, so each model lists only its own, with WanGP's ranges and the template's value as the default (a distilled 8-step model shows its steps as fixed). An empty field follows the template; **↺** puts one back, **Reset all** all of them. **Save generation settings** has WanGP check the values first, and they apply to renders started from then on (takes already made keep theirs; a take's details show the steps it used). FilmLab keeps setting the prompt, size, length, seed, references and sound itself.
- the default **size and length** of new shots. Size is picked like in WanGP: a category (1080p, 720p, 540p, 480p, …) and then one of its sizes, with its aspect ratio and whether it's landscape, portrait or square. A shot's editor and **Select → Change…** use the same picker;
- the **autonomy level** (see [below](#autonomy-and-redo-runs));
- the **video models** this project uses for shots with and without references.

## The Library

The Library holds everything your films reuse: **characters, locations, props, styles and sounds**.

- **Looks:** an asset can have several looks (e.g. *default*, *night*, *wounded*), each with **versions**. Lock a look to freeze it: shots that use it keep using exactly those images.
- **Images:** upload PNG, JPEG or WebP references, or generate them with WanGP from a look's **Generate** panel (from text, or from your existing images to keep identity). Generated **candidates** wait for you to **Keep** or **Discard** them.
- **Voices:** a character can have a **Voice** sample: 5–15 seconds of one person speaking clearly (WAV of any bit depth, MP3 or FLAC). Uploads show their progress in the bottom-left corner. Whenever that character has a line, FilmLab uses it as their voice.
- **Director requests:** when the director needs an image the Library lacks, its request appears on the asset for you to **Generate** or **Reject**.
- **📸 Extract** (Library, or **📸 Extract** under any take): make a character, location, prop or style from a frame. Pick a take and step to the frame (◀ ▶ one frame at a time), or upload a picture, or pick a Library image; **Grab this frame**, then drag on it to select just the part you want. **Add it as it is** makes the selection the asset's look. **Rework it with a model first** sends it to a WanGP model that takes reference pictures, with a ready-made instruction for the kind (full body on a plain background, the place without people, the object alone…) that you can edit; its candidates appear on the new asset's page to **Keep**, and the original stays as the asset's *source frame* look.

Use the names you give assets in your briefs (e.g. `Anthony_science`, `Jim_lab`). The director matches them.

- An asset's page lists every shot that **uses it** (by project and look); click one to open it. With more than one look, **Use one look everywhere** puts the chosen look in all those shots, in the same place (locked and rendering shots are skipped, and each change shows in the shot's History).

## Create

**Create** in the top menu makes a whole new asset from a description. It has a tab for each kind: **Character · Location · Prop · Style · Sound**. The Library's **✨ Create with the director…** opens it too.

1. **Describe it** in the form on the left, then pick:
   - **Pictures** (not for sounds): their size, which starts at the open project's size and uses the category/size picker, and the image models. The first picture is made from text; the other views are edits of it, so it stays the same subject.
   - **Voice** (characters): **Design a voice** from a description, **Clone a sample** you upload, or **No voice**.
   - **Kind of sound:** **Music or song**, or **Ambience** (a background soundscape: rain, crowd, engine hum).
2. **The director drafts it:** a name, a description of what must stay consistent, two to four picture views (the first is the anchor), the voice's description and a sample line, or the sound's style. Edit any of it, or press **Redraft**. Drafts in progress are listed under the form.
3. **Make each part** when you're ready; every step asks before using the GPU.
   1. **Pictures:** generate each view on its own. **Make more** makes new candidates, and clicking a candidate enlarges it. **Use my picture…** uses your own image as the first view. Change the size between generations.
   2. **Voice:** designed from the description, or cloned from your uploaded recording (2-30 s, one person speaking clearly). Keep the take you like. **Hear it** says any line you type in that voice.
   3. **Music** or **Ambience** for sounds.
   4. **Test clip** (optional): a 3-second clip from the kept pictures with your video model, to see whether the look holds up in motion. Clips stay with the draft.
4. **Approve into the Library.** Optionally also add it to a project's cast and to one of that project's shots as its next reference. Until then the draft is hidden from the Library, the director and every shot. **Discard** sends it to Recently deleted.

You can also just ask the director in a project chat (*"we need a lab cat"*). Its draft appears on the Create page for you to finish.

## Planning with the director

Open a project's **Brief** (top of the storyboard), write what the film is about, and press **Plan with director**.

- Mention your Library assets **by name**, the setting, the style, and anything that must happen. Give the **target length**.
- **🎭 Cast** (a chip in the project header) opens a panel from the right. It sets which Library assets the film uses: characters, locations, props, styles and sounds, with search, a kind filter and **Add all shown**. It also sets what the director may use:
  - **Only this cast:** the director sees and uses only those assets. When it leaves a scene without a place, FilmLab sets the cast location the scene's description clearly names.
  - **Whole library, where the brief calls for it.**
  - **Fresh start:** nothing from the Library.

  Assets you attach to shots join the cast automatically.
- The director plans **scene by scene**: each scene's goal, time and place and mood, and its shots, each with a prompt, references, length, transition, **sound** and a reason. You can watch every step in the **Director** panel.
- While it plans, the storyboard shows **how far it got**: the parts saved and the seconds planned against the target, with new scenes appearing as each part is saved. **Stop** ends it there (also in the Director panel, for any reply): the saved scenes stay, and **Plan more** carries on from them.
- **Brief check** (a director model on Ollama): when the director says the plan is done, FilmLab lists what your brief says must happen (never more parts than the plan has shots) and checks that a shot shows each part: a shot that says it in your brief's own words counts at once, and only for the rest is the model asked. A part the plan skipped (a battle between an entrance and an escape, say) is asked for on its own, and its new shots are put where they belong in the film, not at the end. The Director panel says what was found and added. It asks for at most three parts at a time, and for none when the storyboard is already well over the target: then it names what's missing, and **Plan more** adds it once there's room. Music videos aren't checked.
- **Re-plan drafts** replaces draft shots that have no takes. Locked, approved and rendered shots always stay. **Plan more** adds after the current shots.
- **Before you plan**, the Brief checks your brief against the project's settings and offers a one-click fix:
  - the brief asks for a different length than the **target** (the director plans to the target);
  - the brief has action, but the project's **style** asks for calm pacing and gentle camera moves (*Use the illustrated action style*);
  - the brief says nobody speaks, but the style gives every shot a line (or the other way round);
  - a name the brief uses isn't in the **Library**, or has no reference images yet.

  **Keep as is** hides an item for that project. Planning with an item still open asks first.
- **Chat** with the director for anything else: *"make shot 3 a close-up"*, *"add a scene where…"*. It edits drafts and explains each change in the shot's **History**.

> [!NOTE]
> The director can only use FilmLab's own tools. It can draft and edit shots and ask for images, but it can't render, spend GPU time, or change your settings: those are always your clicks.

## The storyboard

- The **film strip** at the top shows every shot as a frame sized by its length, grouped by scene, with the take the edit uses. Click a frame to open the shot; drag to reorder. A coloured bar shows each shot's state, and the running total is compared with your target length.
- Under it, the **length meter** shows the film scene by scene against the target length (the line), and says how far over or short it is. Each scene's header gives its share of the target.
- Below it are the **scenes** (edit their goal, time and place and mood; reorder or delete them) and their **shot cards**.
- A shot with a problem a render would report (a picture the prompt names but the shot doesn't have, a setting the prompt doesn't state, speech that isn't set, …) shows **⚠** with the count. Open the shot to read them and press **✨ Fix with director** (it changes only what's listed and keeps the rest of the prompt) or **Edit**. **✨ Fix N with director** in the storyboard header does every flagged shot at once.
- The director can't leave a prompt without its scene's **place and time of day**: a plan or prompt change that does is refused, and it rewrites it. (Light, weather and mood are up to the prompt.) Your own edits are never refused; they get the ⚠ instead.
- **Select** lets you tick shots, or a whole scene, and then **Render…**, **Lock**, **Unlock**, **Change…** (model, size or length) or **Delete** them together. *Unlocked drafts* selects what a re-plan would replace. Locked, approved and rendering shots are skipped, with the reason.
- Deleting a shot or a scene shows **Undo** for a few seconds; the shots are also in **Recently deleted** afterwards. A deleted shot's takes, renders and pending redo go with it.
- **Clear…** starts the storyboard over: every shot and scene goes to Recently deleted (locked shots and their scenes stay), and the director starts a fresh conversation so the next plan doesn't build on the old one. **Undo** brings it all back.
- **Render storyboard…** reviews every shot with WanGP first (what will render, what can't and why, how long it should take) and then renders your selection as a **batch**, one shot at a time, in order. Pause, resume or cancel it from the batch bar, and retry failed shots. Tick **Preview first** for a preview batch (see [Rendering](#rendering)).
  - The review says roughly **when it will be done** ("Done around 9:10 PM"), from this PC's past renders.
  - **Start at a set time** schedules it, for example overnight: it waits, paused, and starts by itself (FilmLab must be running then). **Start now** starts it early.
  - In the batch bar, **↑ ↓** move a waiting shot sooner or later.
- **▶ Animatic** plays the storyboard as stills for each shot's length: the take the edit uses, else the shot's first reference, else its prompt on a card. It shows the transitions, the shot's idea as a caption, and the edit's first music track. Space plays and pauses; Esc closes.

## Shots

A shot card shows its prompt, its **sound** summary, its references, and the director's latest reason. Its buttons:

| Button | What it does |
|---|---|
| **Edit** | Title, your idea, the picture prompt, **Sound**, length, size, transition into the next shot, model, preset |
| **✨ Enhance** | The director picks references (if there are none) and rewrites the prompt from your idea, naming each reference as `<Picture N>` |
| **Validate** | Asks WanGP whether it can render the shot, and at what size and length |
| **History** | Every change to the shot: who made it, when and why, the **takes rendered from each version** (click one to open it) and **Restore this version**. *Only prompt changes…* hides the rest |
| **Lock** | The director can't change it and batches don't re-render it |
| **Preview** | A quick take at half the width and height (see [Rendering](#rendering)) |
| **Approve & render** / **Render new take** | Queues it in WanGP at full quality |

**References:** each shot has up to eight reference images, in order. In the prompt they're `<Picture 1>`, `<Picture 2>`, … FilmLab warns before a render if the prompt names a picture the shot doesn't have, or doesn't state the scene's setting. Every shot is rendered on its own, so its prompt must say where and when it is.

**Transitions:** cut, dissolve, fade through black or white, wipes, slides, circle open/close, clock wipe, zoom, blur, pixelate and squeeze. They render in exports; the in-browser preview shows them as a fade.

## Music videos

Start a project from the **Music video** preset and its setup opens by itself; the **🎵** chip in the project header opens it again. One song plays from start to end under the whole film, so a cut never restarts the music.

1. **The song.** Generate it from the exact lyrics (with `[Verse]` / `[Chorus]` markers) and a style (genre, mood, instruments, voice), then **Use this** on the candidate you like; or upload a finished track; or pick a Sound from the Library. The film's target length becomes the song's length, and the song becomes the edit's soundtrack.
2. **How the shots follow it:**
   - **Lip-sync to the song.** Each shot is made by a song-driven model from its own slice of the song (where the shot starts in the film, for as long as it lasts), and the performer sings exactly that part. The suggested model keeps the performers' look from their pictures; models marked **Not downloaded** are downloaded by WanGP the first time they render in the project (often several GB). Mouths may not match every syllable.
   - **Picture with one soundtrack.** Shots are made with your usual models and the song plays over them. Faster and works with any model, but mouths only roughly match.

**Plan with director** then plans the shots back to back to cover the song, each quoting the lyric lines sung during it. In the edit, the preview, the animatic and the export, the shots' own sound is muted under the song.

While WanGP downloads a model for a render, the render says so with how much has arrived and how fast, and **Watch in WanGP ↗** opens WanGP's own page with the exact progress. FilmLab finds that page by itself; set `FILMLAB_WANGP_WEBUI` (its address) or `FILMLAB_WANGP_CKPTS` (WanGP's model folder) if it can't.

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

**Previews.** A preview renders the shot at half its width and height (about a quarter of the pixels, so several times faster), with the same model, prompt, references, sound and steps. Use it to judge timing, framing and continuity across the film before spending full-quality time:

1. **Preview** a shot, or tick **Preview first** in *Render storyboard…* for the whole film.
2. Preview takes are marked **P** and *Preview*. If one looks right, press **Render full quality** on it: the full render reuses the preview's seed, so it stays as close as the model allows (a different size is never pixel-identical).
3. Until a shot has a full-quality take, the edit uses its preview (a rough cut) and says so; the final export scales it up.

## Reviewing takes

Every new take is **inspected** automatically:

- **Technical checks**, always, on your PC: length, black or frozen picture, silent or clipping sound.
- An optional **vision check** of four frames against the prompt and the scene, by an Ollama vision model. Choose it under **Settings → Take inspection**:
  - **Off** (the default on a new install): no frames are sent anywhere.
  - **Local model**: frames stay on your PC; it uses GPU memory.
  - **Cloud model**: four frames per take go to Ollama's servers.

  Settings shows whether the model is Local or Cloud, and asks before you turn on the cloud option.

A take that looks wrong is **flagged ⚠**. Inspection is advice; it never deletes anything.

- Mark each take **👍 Good**, **🤔 Maybe** or **👎 Reject**, and add notes on what works and what doesn't.
- **Use in edit** picks the take the edit uses (by default the newest good one).
- **Compare A/B…** plays two takes side by side, in sync, and **Keep this one** picks it for the edit.
- **☆ Set as cover** makes the take the project's picture on the Projects page; **★ Cover** puts it back to FilmLab's choice.
- Move the pointer over a take's thumbnail to **scrub** through it.
- **Keys** while a take shows: <kbd>J</kbd>/<kbd>K</kbd> previous and next take, <kbd>Space</kbd> play, <kbd>A</kbd> good, <kbd>M</kbd> maybe, <kbd>R</kbd> reject, <kbd>C</kbd> compare.
- **Review mode** (in the Renders panel, made for phones) shows one take at a time, full screen and looping: swipe **right** for 👍 good, **left** for 👎 reject, **up** for 🤔 maybe, and it moves to the next. It starts at the first take you haven't reviewed.
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
- **✨ Plan transitions:** the director chooses a transition and its length for each scene change, or for every cut, based on the mood of the shots on either side. For example, a calm moment might fade out, while action might get a hard cut or a quick wipe. You can watch its reasoning as it works, then adjust the list. **Accept** applies the transitions, and **Cancel** discards them. You can close the window while it works; the button then shows **Review planned transitions**.
- **Music:** up to two tracks from the Library's sounds, with start, offset, gain, fades and automatic **ducking** under dialogue.
- **Titles and captions:** title cards (start, end or between shots) and timed captions.
- **Preview** in the browser, then **export**:
  - a **Draft** for a quick look;
  - a **Final**, with loudness normalised to −14 LUFS and optional burned-in captions.

  Exports are MP4 (H.264/AAC) with an SRT caption file and a manifest of every source used.

**Music** comes from the Library (kind *Sound*), up to two tracks placed on the film's timeline (start, where in the file, level, fades, ducking under dialogue). **Whole film** puts a track under the entire film.

## Storybook

The **Storybook** tab turns the film into an illustrated book, using a frame from each shot's take.

- **📖 Picture book** puts one picture on each page, with narration and the characters' lines below it. **💬 Comic** puts 2–6 panels on each page, with caption boxes and speech bubbles for the dialogue. With **Chapters per scene** on, each scene starts a new page under its title.
- **Click a page or panel** to edit it:
  - choose a different moment of the take, or crop the picture;
  - write the narration or caption;
  - show or hide the dialogue;
  - start a new comic page there, or leave the shot out.
- **Click the cover** to set the title, subtitle, author and cover picture.
- A page keeps the frame you chose. When you render a newer take of its shot, the page shows **new take**, and **Use the new take** switches to it.
- **✨ Write the story:** the director writes the narration for every page (or a caption for every comic panel), plus a title and subtitle. You can watch it work, then edit the text. **Accept** saves it, and **Cancel** discards it. You can add a note such as "for children, bedtime-story voice".
- **📕 Export book:** the server draws every page (A4, 150 dpi) and produces a **PDF**, each page as a **PNG** (zipped) and a **CBZ** for comic-reader apps.
- **History** restores the book as it was after any earlier change.

The old contact sheet is still at `#/p/<project>/sheet`.

## Settings

| Section | What you set |
|---|---|
| **Startup & WanGP** | Whether WanGP is connected, with **Start WanGP**, and where WanGP is (found by itself, or the folder you choose; **Detect again**). Also: start WanGP when FilmLab starts, start it again if it stops, and **Start FilmLab when I sign in to Windows** so FilmLab comes back after a restart. This section shows even while WanGP is down |
| **Models** | The default WanGP templates for video (with and without references), reference images, voice design and music; and every installed template with whether it's usable (**Check again** after downloading models in WanGP) |
| **Presets** | Ready-made size, length and model combinations, plus your own |
| **Director** | The director's model, your standing notes for it, how it replies, how many steps it may take, and a connection test |
| **Local models** | On a 16 GB GPU, **qwen3.5:9b** works best as both the director and the take-inspection vision model. It plans correctly with FilmLab's tools, uses about 6.7 GB at its 32k context and also reads images, so only one model ever loads. Models over about 14 GB (e.g. 27B) don't fit in 16 GB and run far too slowly; llama3.1 and mistral write tool calls as text and can't direct |
| **Take inspection** | Inspect new takes automatically, and the vision check: Off, a Local model, or a Cloud model (with a Local/Cloud label on the model) |
| **GPU sharing** | How FilmLab shares the GPU with local Ollama models. **Take turns** (the default): FilmLab unloads local models before any WanGP job, a local director or vision model waits while WanGP is busy, and a render asked for during a local director turn starts when the turn ends. Also: wait, warn, or don't check |

## Tips for better results

- **Name things consistently.** Use Library names in the brief, and give characters clear, distinct looks.
- **State the setting in every shot.** Each shot is rendered alone; the model doesn't know the scene unless the prompt says so. Enhance does this for you.
- **One action per shot, one line per shot.** Short shots (3–6 s) with a clear beginning and end come out best.
- **Lock what you love.** Locked shots and looks survive re-plans and batches.
- **Review every take.** Faces, hands, text and fast motion can drift. Reshoot rather than fight a bad take.
