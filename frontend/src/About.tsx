/** About FilmLab Studio: what it is, who made it, and the projects it stands on. */
import logo from "./brand/filmlab-logo.webp";

const REPO = "https://github.com/merberg-ai/wan2gp-filmlab-studio";

function Ext({ href, children }: { href: string; children: React.ReactNode }) {
  return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
}

export function AboutPage({ version }: { version?: string }) {
  return (
    <main className="page about">
      <section className="about-hero">
        <img className="about-logo" src={logo} alt="FilmLab Studio" width={720} height={103} />
        <p className="about-tagline">Describe a film. An AI director plans the scenes, shots, dialogue and sound; WanGP
          renders them on your own GPU.</p>
        <p className="muted small">{version ? `Version ${version} · ` : ""}Runs on your PC; nothing renders in the cloud.</p>
        <div className="about-links">
          <Ext href={REPO}>GitHub repository ↗</Ext>
          <Ext href={`${REPO}/issues`}>Report an issue ↗</Ext>
          <Ext href={`${REPO}#readme`}>Read me ↗</Ext>
        </div>
      </section>

      <section>
        <h2>What it does</h2>
        <ul className="about-list">
          <li><strong>Plan</strong>: write a brief; the director plans scenes and shots with references from your Library,
            sound and dialogue, and you edit or lock what you like.</li>
          <li><strong>Render</strong>: WanGP renders each shot locally, with previews, batches and redo runs, and
            inspects every take.</li>
          <li><strong>Review and cut</strong>: pick takes, compare them, and cut the film with transitions, music,
            titles and captions, then export.</li>
          <li><strong>Make</strong>: characters, places, voices and songs in the Library, and music videos lip-synced
            to a song.</li>
        </ul>
      </section>

      <section>
        <h2>Made by</h2>
        <div className="card about-author">
          <span className="about-avatar" aria-hidden>M</span>
          <span>
            <strong>merberg</strong>
            <span className="block small"><a href="mailto:j1mb3rg@gmail.com">j1mb3rg@gmail.com</a> ·{" "}
              <Ext href="https://github.com/merberg-ai">github.com/merberg-ai ↗</Ext></span>
          </span>
        </div>
      </section>

      <section>
        <h2>Built on</h2>
        <ul className="about-credits">
          <li className="card">
            <strong><Ext href="https://github.com/deepbeepmeep/Wan2GP">WanGP / Wan2GP ↗</Ext></strong>
            <span className="muted small">by DeepBeepMeep. The renderer behind every video, image, voice and song in
              FilmLab, through its MCP server. FilmLab wouldn't exist without it.</span>
          </li>
          <li className="card">
            <strong><Ext href="https://www.npmjs.com/package/openclaw">OpenClaw ↗</Ext></strong>
            <span className="muted small">The agent runtime behind the director.</span>
          </li>
          <li className="card">
            <strong><Ext href="https://ollama.com">Ollama ↗</Ext></strong>
            <span className="muted small">Runs the director's model, and the vision check of take inspection.</span>
          </li>
          <li className="card">
            <strong><Ext href="https://ffmpeg.org">FFmpeg ↗</Ext></strong>
            <span className="muted small">Thumbnails, song slices, the edit's preview and the export.</span>
          </li>
        </ul>
        <p className="muted small">And the model authors whose work WanGP runs: MiniMax, Lightricks, Black Forest Labs,
          Alibaba Qwen, ACE-Step and others. Each model keeps its own license.</p>
      </section>

      <section>
        <h2>License</h2>
        <p className="small">FilmLab Studio is released into the public domain under the{" "}
          <Ext href={`${REPO}/blob/main/LICENSE`}>Unlicense ↗</Ext>. WanGP, OpenClaw, Ollama, FFmpeg and the models are
          separate projects with their own licenses; FilmLab doesn't include them.</p>
      </section>
    </main>
  );
}
