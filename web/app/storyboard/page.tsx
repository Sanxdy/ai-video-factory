"use client";

/** Storyboard page: create a storyboard-only project with scene count, duration, and ratio. */
import { useState } from "react";
import { useRouter } from "next/navigation";
import { post, Icon, ICONS, Title } from "../lib";

const RATIOS = [
  { value: "9:16", label: "9:16 (YouTube Shorts)" },
  { value: "16:9", label: "16:9 (Regular Video)" },
] as const;

export default function StoryboardPage() {
  const router = useRouter();
  const [topic, setTopic] = useState("");
  const [sceneCount, setSceneCount] = useState(5);
  const [maxDuration, setMaxDuration] = useState(15);
  const [ratio, setRatio] = useState<string>("9:16");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  /* Scenes × duration is the number people actually care about, and it is the
     only "how long will this be" answer the form can give before generating. */
  const totalSecs = sceneCount * maxDuration;
  const totalLen = totalSecs >= 60
    ? `${Math.floor(totalSecs / 60)}m ${totalSecs % 60}s`
    : `${totalSecs}s`;

  async function create() {
    if (!topic.trim()) {
      setErr("Topic is required");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const r = await post<{ project: number }>("/storyboard", {
        topic: topic.trim(),
        scene_count: sceneCount,
        max_scene_duration: maxDuration,
        aspect_ratio: ratio,
      });
      router.push(`/project/${r.project}`);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Title>{"Storyboard | AVF Console"}</Title>
      <div className="pagehead">
        <div>
          <h1>New Storyboard</h1>
          <p>Generate a script and storyboard, then upload your own video per scene.</p>
        </div>
      </div>

      {err && (
        <p className="msg msg-err" role="alert" style={{ marginBottom: "var(--space-4)" }}>
          {err}
          <button className="banner-x" onClick={() => setErr("")} aria-label="Dismiss">×</button>
        </p>
      )}

      <div className="card sb-card">
        <div className="field">
          <span className="field-label">What should it be about?</span>
          <textarea
            className="input"
            autoFocus
            rows={3}
            aria-label="Storyboard topic"
            placeholder="e.g. Why the sky is blue"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <p className="hint">The topic for your storyboard. Each scene will get narration + visual prompt.</p>
        </div>

        <div className="formrow" style={{ gap: "var(--space-4)", marginTop: "var(--space-3)", alignItems: "flex-start" }}>
          <div className="field" style={{ flex: 1 }}>
            <span className="field-label">Number of scenes</span>
            <input
              type="range"
              min={1}
              max={10}
              value={sceneCount}
              onChange={(e) => setSceneCount(+e.target.value)}
              style={{ width: "100%" }}
            />
            <p className="hint" style={{ textAlign: "center", margin: 0 }}>
              <strong>{sceneCount}</strong> scene{sceneCount > 1 ? "s" : ""}
            </p>
          </div>

          <div className="field" style={{ flex: 1 }}>
            <span className="field-label">Fixed duration per scene</span>
            <input
              type="range"
              min={1}
              max={60}
              value={maxDuration}
              aria-label="Fixed duration per scene in seconds"
              onChange={(e) => setMaxDuration(+e.target.value)}
              style={{ width: "100%" }}
            />
            <p className="hint" style={{ textAlign: "center", margin: 0 }}>
              <strong>{maxDuration}s</strong> per scene
            </p>
          </div>
        </div>

        {/* Ratio and the resulting output sit side by side: at full card width a
            lone two-button picker leaves the row half empty. */}
        <div className="sb-split">
          <div className="field">
            <span className="field-label" id="sb-ratio-label">Output ratio</span>
            {/* Same control as the dashboard's New-video modal. The two used to be
                different markups (buttons with a <br/> here, role=radio there), and
                selection was signalled only by which button class was applied, so
                nothing announced it to a screen reader. */}
            <div className="fmt-pick" role="radiogroup" aria-labelledby="sb-ratio-label">
              {RATIOS.map((r) => (
                <button
                  key={r.value}
                  type="button"
                  role="radio"
                  aria-checked={ratio === r.value}
                  data-on={ratio === r.value ? "1" : "0"}
                  onClick={() => setRatio(r.value)}
                >
                  <span className="t">{r.value}</span>
                  <span className="d">
                    {r.value === "9:16" ? "YouTube Shorts" : "Regular video"}
                  </span>
                </button>
              ))}
            </div>
            <p className="hint" style={{ marginTop: "var(--space-2)" }}>
              {ratio === "9:16"
                ? "Vertical video (1080×1920): uploaded as YouTube Shorts with #Shorts tag."
                : "Landscape video (1920×1080): uploaded as a regular YouTube video."}
            </p>
          </div>

          <aside className="sb-summary" aria-label="What this storyboard will produce">
            <span className="field-label">This will produce</span>
            <dl className="sb-facts">
              <div><dt>Scenes</dt><dd>{sceneCount}</dd></div>
              <div><dt>Per scene</dt><dd>{maxDuration}s</dd></div>
              <div><dt>Total length</dt><dd>{totalLen}</dd></div>
              <div><dt>Frame</dt><dd>{ratio === "9:16" ? "1080×1920" : "1920×1080"}</dd></div>
            </dl>
            <p className="hint" style={{ margin: 0 }}>
              Every scene gets its own narration, visual prompt and subtitle pass. You
              review and upload each one — nothing publishes by itself.
            </p>
          </aside>
        </div>

        <div className="formrow" style={{ justifyContent: "flex-end", marginTop: "var(--space-5)" }}>
          <button className="btn btn-primary" onClick={create} disabled={busy || !topic.trim()}>
            <Icon d={ICONS.play} size={15} /> {busy ? "Generating…" : "Generate Storyboard"}
          </button>
        </div>
      </div>
    </>
  );
}
