"use client";

/** First-run wizard. Three steps: the LLM key (required), a stock footage key
 *  (required — see below), and YouTube (optional). layout.tsx redirects here
 *  while the LLM key is missing; POST /api/produce enforces both keys again
 *  server-side, because a client gate is not a guard.
 *
 *  Stock is required because AVF has no AI video generator: providers/video/ is
 *  empty and the only keyless path is a generated still with a slow zoom over
 *  it. Scenes are meant to be real moving footage from Pexels or Pixabay. */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, post, Title } from "../lib";
import { useToast } from "../toast";
import { Field, Icon, ICONS, PreviewButton, groupVoices } from "../settings/ui";
import { ClientSecretHowTo, YouTubeProdWarning } from "../settings/youtube-howto";
import SubtitlePicker from "../settings/subtitle-picker";

type LLMConfig = {
  api_base_url: string;
  api_model: string;
  api_fallback_models: string;
  has_api_key: boolean;
  api_key_hint: string | null;
};

type ImageConfig = {
  provider: string;
  stock_sources: string;
  has_pexels_key: boolean;
  pexels_key_hint: string | null;
  has_pixabay_key: boolean;
  pixabay_key_hint: string | null;
};

type YouTubeStatus = {
  connected: boolean;
  has_secret: boolean;
  alive?: boolean;
  channel_title?: string;
  error?: string;
};

type VoiceConfig = {
  voice: string;
  voices: { id: string; group: string; label: string }[];
};

type AudioConfig = { voice_volume: number; music_volume: number };

type SubtitleConfig = { preset: string; presets: { id: string; label: string }[] };

type WmRatio = {
  enabled: boolean;
  path: string | null;
  x: number; y: number; scale: number; opacity: number;
  exists: boolean;
};
type WmConfig = { settings: Record<string, WmRatio> };

type ScheduleRow = {
  id: string; time: string; videos_per_run: number;
  format: string; enabled: boolean;
};

/* Every settings group, in the order they are asked. Steps 0-2 are the ones that
   block a render; 3-6 are the ones with working defaults that a first-time user
   should still see, because a silent default is a choice made for them. */
const STEP_LABELS = [
  "AI model", "Stock footage", "YouTube",
  "Voice & music", "Subtitles", "Watermark", "Schedule",
];
const DONE_STEP = STEP_LABELS.length;

function Steps({ current }: { current: number }) {
  return (
    <div className="steps">
      {STEP_LABELS.map((label, i) => (
        <div key={label}
             className={`step${i < current ? " step-done" : i === current ? " step-active" : ""}`}>
          <span className="step-dot" />
          <span className="step-label">{label}</span>
        </div>
      ))}
    </div>
  );
}

export default function SetupPage() {
  const { notify } = useToast();
  const [step, setStep] = useState<number | null>(null); // null while loading
  const [cfg, setCfg] = useState<LLMConfig | null>(null);
  const [img, setImg] = useState<ImageConfig | null>(null);
  const [yt, setYt] = useState<YouTubeStatus | null>(null);
  const [busy, setBusy] = useState(false);

  const [apiKey, setApiKey] = useState("");
  const [test, setTest] = useState<{ ok: boolean; text: string } | null>(null);

  const [pexelsOn, setPexelsOn] = useState(true);
  const [pixabayOn, setPixabayOn] = useState(false);
  const [pexelsKey, setPexelsKey] = useState("");
  const [pixabayKey, setPixabayKey] = useState("");

  const [ytUrl, setYtUrl] = useState("");
  const [ytCode, setYtCode] = useState("");

  const [vo, setVo] = useState<VoiceConfig | null>(null);
  const [au, setAu] = useState<AudioConfig | null>(null);
  const [sub, setSub] = useState<SubtitleConfig | null>(null);
  const [wm, setWm] = useState<WmConfig | null>(null);
  const [scheds, setScheds] = useState<ScheduleRow[]>([]);
  const [tracks, setTracks] = useState<string[]>([]);

  const load = useCallback(async () => {
    const [l, im, y, v, a, s, w, sc] = await Promise.all([
      api<LLMConfig>("/settings/llm"),
      api<ImageConfig>("/settings/image"),
      api<YouTubeStatus>("/settings/youtube"),
      api<VoiceConfig>("/settings/voice"),
      api<AudioConfig>("/settings/audio"),
      api<SubtitleConfig>("/settings/subtitle"),
      api<WmConfig>("/settings/watermark"),
      api<ScheduleRow[]>("/settings/schedules"),
    ]);
    setCfg(l); setImg(im); setYt(y);
    setVo(v); setAu(a); setSub(s); setWm(w); setScheds(sc);
    return { l, im, y };
  }, []);

  /* The bed list is decorative here — the step works without it — so a failure
     to load it must not take the wizard down with it. */
  useEffect(() => {
    api<string[]>("/music").then(setTracks).catch(() => {});
  }, []);

  /* Land on the first unfinished step, so returning here after a partial setup
     resumes rather than restarting. */
  useEffect(() => {
    load()
      .then(({ l, im }) => {
        setStep(!l.has_api_key ? 0 : !(im.has_pexels_key || im.has_pixabay_key) ? 1 : 2);
      })
      .catch((e) => notify(String(e.message || e), "err"));
  }, [load, notify]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e: any) {
      notify(String(e.message || e), "err");
    } finally {
      setBusy(false);
    }
  };

  const saveLLM = () => run(async () => {
    await post("/settings/llm", {
      api_base_url: cfg!.api_base_url || null,
      api_model: cfg!.api_model || null,
      api_fallback_models: cfg!.api_fallback_models || null,
      api_key: apiKey || null,
    });
    setApiKey("");
    await load();
    setStep(1);
  });

  const testLLM = () => run(async () => {
    setTest(null);
    const r = await post<{ ok: boolean; model?: string; reply?: string; error?: string }>(
      "/llm/test",
      {
        api_base_url: cfg!.api_base_url || null,
        api_model: cfg!.api_model || null,
        api_fallback_models: cfg!.api_fallback_models || null,
        api_key: apiKey || null,
      });
    setTest(r.ok
      ? { ok: true, text: `Connected — ${r.model} replied “${r.reply}”.` }
      : { ok: false, text: r.error || "Test failed." });
  });

  /* A ticked box with no key is dropped by stock_sources(), so the forward
     action stays disabled until one ticked source actually has a key. */
  const pexelsOk = pexelsOn && Boolean(pexelsKey.trim() || img?.has_pexels_key);
  const pixabayOk = pixabayOn && Boolean(pixabayKey.trim() || img?.has_pixabay_key);

  const saveStock = () => run(async () => {
    const sources = [pexelsOn && "pexels", pixabayOn && "pixabay"].filter(Boolean).join(",");
    await post("/settings/image", {
      provider: img!.provider,
      pexels_key: pexelsKey || null,
      pixabay_key: pixabayKey || null,
      stock_sources: sources,
      // the wizard's promise is real footage, so say so rather than leaving a
      // stills-only asset_mode from an earlier run in place
      asset_mode: "stock",
    });
    setPexelsKey(""); setPixabayKey("");
    await load();
    setStep(2);
  });

  /* Steps 3-6 save on Continue rather than on every keystroke: a slider drag
     would otherwise be one POST per pixel, and a half-finished choice would be
     live in the next render. */
  const saveVoiceAudio = () => run(async () => {
    await post("/settings/voice", { voice: vo!.voice });
    await post("/settings/audio", {
      voice_volume: au!.voice_volume, music_volume: au!.music_volume,
    });
    await load();
    setStep(4);
  });

  const saveSubtitle = () => run(async () => {
    await post("/settings/subtitle", { preset: sub!.preset });
    await load();
    setStep(5);
  });

  /* Both ratios take the same on/off: the wizard does not know which aspect the
     first video will be, and a watermark that only appears on Shorts would look
     like a bug. Position stays in Settings, where the drag box lives. */
  const setWatermark = (enabled: boolean) => run(async () => {
    for (const [ratio, s] of Object.entries(wm!.settings)) {
      await post("/settings/watermark", {
        ratio, enabled,
        x: s.x, y: s.y, scale: s.scale, opacity: s.opacity,
      });
    }
    await load();
  });

  const saveSchedule = () => run(async () => {
    await post("/settings/schedules", scheds);
    await load();
  });

  if (step === null || !cfg || !img || !yt || !vo || !au || !sub || !wm) {
    return (
      <>
        <Title>{"Setup | AVF Console"}</Title>
        <div className="card wiz"><div className="skeleton" style={{ height: 320 }} /></div>
      </>
    );
  }

  return (
    <>
      <Title>{"Setup | AVF Console"}</Title>
      <div className="card wiz">
        <Steps current={step} />

        {/* ── step 1: AI model ─────────────────────────────────────── */}
        {step === 0 && (
          <>
            <h1>Welcome to AVF</h1>
            <p className="hint" style={{ marginBottom: "var(--space-5)" }}>
              One thing is required before AVF can produce anything: an LLM to write the script,
              plan the scenes and time the subtitles. The key is stored on this machine and sent
              only to the endpoint you name below.
            </p>

            <Field label="Base URL"
                   hint="Any OpenAI-compatible endpoint: OpenAI, Groq, OpenRouter, DeepSeek, LM Studio…">
              <input className="input" type="text" placeholder="https://api.openai.com/v1"
                     value={cfg.api_base_url}
                     onChange={(e) => setCfg({ ...cfg, api_base_url: e.target.value })} />
            </Field>
            <Field label="Model name">
              <input className="input" type="text" placeholder="gpt-4o-mini"
                     value={cfg.api_model}
                     onChange={(e) => setCfg({ ...cfg, api_model: e.target.value })} />
            </Field>
            <Field label="API key">
              <input className="input" type="password"
                     placeholder={cfg.has_api_key
                       ? `Stored (${cfg.api_key_hint}): leave empty to keep`
                       : "sk-…"}
                     value={apiKey}
                     onChange={(e) => setApiKey(e.target.value)} />
            </Field>

            {test && (
              <div className={`msg ${test.ok ? "msg-ok" : "msg-err"}`}
                   style={{ marginBottom: "var(--space-4)", padding: "10px 12px",
                            borderRadius: "var(--radius-sm)" }}>
                <span>{test.text}</span>
              </div>
            )}

            <div className="formrow">
              <button className="btn btn-primary" disabled={busy} onClick={saveLLM}>
                Save and continue
              </button>
              <button className="btn btn-secondary" disabled={busy} onClick={testLLM}>
                Test connection
              </button>
              <span className="hint" style={{ margin: 0 }}>
                Test sends one prompt and stores nothing.
              </span>
            </div>
          </>
        )}

        {/* ── step 2: stock footage ────────────────────────────────── */}
        {step === 1 && (
          <>
            <h1>Stock footage</h1>
            <p className="hint" style={{ marginBottom: "var(--space-4)" }}>
              <strong>One of these two keys is required.</strong> Moving footage can only come from
              Pexels or Pixabay — those are the only two sources AVF has, and each needs its own
              free API key. Paste at least one to continue.
            </p>
            <p className="hint" style={{ marginBottom: "var(--space-5)" }}>
              There is no keyless substitute, so this step cannot be skipped. AVF has no AI video
              generator; without a stock key the scenes become still pictures with a slow zoom across
              them, which is not a video worth watching. The free still service that would supply those
              pictures is also unreliable at video resolution — in testing it failed 2 of 5 requests,
              quietly returned 576×1024 when asked for 1080×1920, took 22–47 seconds each time, and
              stamped its own watermark in the corner, which the final crop does not remove.
            </p>

            <Field label="Pexels">
              <label style={{
                display: "flex", alignItems: "flex-start", gap: 10,
                background: "var(--color-surface-2)",
                border: `1px solid ${pexelsOn ? "rgba(var(--color-accent-rgb),.45)" : "var(--color-border)"}`,
                borderRadius: "var(--radius-sm)", padding: "10px 12px", cursor: "pointer",
              }}>
                <input type="checkbox" checked={pexelsOn}
                       onChange={(e) => setPexelsOn(e.target.checked)}
                       style={{ marginTop: 3, width: 15, height: 15, flex: "0 0 auto",
                                accentColor: "var(--color-accent)" }} />
                <span>
                  <span style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>Use Pexels footage</span>
                  <span className={`badge ${img.has_pexels_key ? "badge-complete" : "badge-approval"}`}
                        style={{ marginLeft: 6 }}>
                    {img.has_pexels_key ? "key stored" : "no key yet"}
                  </span>
                  <span style={{ display: "block", color: "var(--color-muted)",
                                 fontSize: "var(--text-xs)", marginTop: 2 }}>
                    Searched with the full fallback chain, so more queries find something.
                    No caching requirement — results are always fresh.
                  </span>
                </span>
              </label>

              <details className="howto" style={{ marginTop: 8 }}>
                <summary>Where do I get a Pexels key? — free, about a minute</summary>
                <ol>
                  <li>Open <a href="https://www.pexels.com/api/" target="_blank" rel="noreferrer">pexels.com/api</a> and
                    click <strong>Get Started</strong>.</li>
                  <li>Sign up or log in. A free Pexels account is enough — no credit card, no company
                    details, and this is a normal Pexels account, not a separate developer signup.</li>
                  <li>You land on a form asking for an application name and description. It is only
                    ever shown to you — put anything, e.g. name <code>AVF</code>, description
                    <code>local video generator</code>.</li>
                  <li>Submit. Your key is displayed immediately on that page: a long string of letters
                    and numbers. Copy it and paste it in the box below.</li>
                </ol>
                <p className="hint" style={{ padding: "0 16px 12px 34px", margin: 0 }}>
                  The free tier is meant for exactly this kind of use. AVF caches every search on
                  disk for 24 hours, so a video costs a handful of requests, not one per scene.
                </p>
              </details>

              <input className="input" type="password" style={{ marginTop: 8 }}
                     placeholder={img.has_pexels_key
                       ? `Stored (${img.pexels_key_hint}): leave empty to keep`
                       : "Paste your Pexels API key here"}
                     value={pexelsKey}
                     onChange={(e) => setPexelsKey(e.target.value)} />
            </Field>

            <Field label="Pixabay">
              <label style={{
                display: "flex", alignItems: "flex-start", gap: 10,
                background: "var(--color-surface-2)",
                border: `1px solid ${pixabayOn ? "rgba(var(--color-accent-rgb),.45)" : "var(--color-border)"}`,
                borderRadius: "var(--radius-sm)", padding: "10px 12px", cursor: "pointer",
              }}>
                <input type="checkbox" checked={pixabayOn}
                       onChange={(e) => setPixabayOn(e.target.checked)}
                       style={{ marginTop: 3, width: 15, height: 15, flex: "0 0 auto",
                                accentColor: "var(--color-accent)" }} />
                <span>
                  <span style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>Use Pixabay footage</span>
                  <span className={`badge ${img.has_pixabay_key ? "badge-complete" : "badge-approval"}`}
                        style={{ marginLeft: 6 }}>
                    {img.has_pixabay_key ? "key stored" : "no key yet"}
                  </span>
                  <span style={{ display: "block", color: "var(--color-muted)",
                                 fontSize: "var(--text-xs)", marginTop: 2 }}>
                    A second library, so a scene Pexels cannot match may still find one here.
                    Searched on the primary query only, results cached 24h, throttled to 30/min.
                  </span>
                </span>
              </label>

              <details className="howto" style={{ marginTop: 8 }}>
                <summary>Where do I get a Pixabay key? — free, about a minute</summary>
                <ol>
                  <li>Create a free account at
                    <a href="https://pixabay.com/accounts/register/" target="_blank" rel="noreferrer">pixabay.com</a>
                    {" "}if you do not have one, then log in.</li>
                  <li>Open <a href="https://pixabay.com/api/docs/" target="_blank" rel="noreferrer">pixabay.com/api/docs</a>
                    {" "}<strong>while logged in</strong>. This page <em>is</em> the key page — there is no
                    separate application form to fill in.</li>
                  <li>Near the top of that page, in the parameter table, find the row for
                    <code>key</code>. Your personal API key is shown right there, in place of the
                    placeholder. Copy it and paste it in the box below.</li>
                </ol>
                <p className="hint" style={{ padding: "0 16px 12px 34px", margin: 0 }}>
                  If you see a placeholder instead of a real key, you are not logged in — log in and
                  reload the page. Pixabay allows 100 requests per minute; AVF deliberately stays at
                  30 and caches every search for 24 hours, which is what their API terms require.
                </p>
              </details>

              <input className="input" type="password" style={{ marginTop: 8 }}
                     placeholder={img.has_pixabay_key
                       ? `Stored (${img.pixabay_key_hint}): leave empty to keep`
                       : "Paste your Pixabay API key here"}
                     value={pixabayKey}
                     onChange={(e) => setPixabayKey(e.target.value)} />
            </Field>

            <p className="hint" style={{ marginBottom: "var(--space-4)" }}>
              Turn both on and AVF searches both and takes the better match. A box that is ticked
              with no key pasted is ignored — that is why each box sits next to its own key field.
            </p>

            <div className="formrow">
              <button className="btn btn-primary" disabled={busy || !(pexelsOk || pixabayOk)}
                      onClick={saveStock}>
                Save and continue
              </button>
              {!(pexelsOk || pixabayOk) && (
                <span className="hint" style={{ margin: 0 }}>
                  Enabled once one of the ticked sources has a key.
                </span>
              )}
            </div>
          </>
        )}

        {/* ── step 3: YouTube ──────────────────────────────────────── */}
        {step === 2 && (
          <>
            <h1>YouTube uploads</h1>
            <p className="hint" style={{ marginBottom: "var(--space-4)" }}>
              Optional. Skip it and AVF still renders videos — you just upload them yourself.
              This connects <em>your</em> channel with <em>your</em> OAuth client; nothing is shared
              with anyone else.
            </p>

            {yt.connected && (
              <div className="msg msg-ok" style={{ marginBottom: "var(--space-5)",
                                                   padding: "10px 12px",
                                                   borderRadius: "var(--radius-sm)" }}>
                <span>Connected: <strong>{yt.channel_title}</strong></span>
              </div>
            )}

            <YouTubeProdWarning style={{ marginBottom: "var(--space-5)" }} />

            {!yt.has_secret && (
              <>
                <ClientSecretHowTo />

                <Field label="Client secret (the JSON you just downloaded)"
                       hint="Read once to start the sign-in, then stored locally alongside the token. Nothing is uploaded anywhere.">
                  <input type="file" accept=".json,application/json" className="input input-file"
                          disabled={busy}
                         onChange={(e) => {
                           const f = e.target.files?.[0];
                           if (!f) return;
                           e.target.value = "";
                           void run(async () => {
                             await post("/settings/youtube/secret", { content: await f.text() });
                             await load();
                             notify(`Client secret saved from ${f.name}.`, "ok");
                           });
                         }} />
                </Field>
              </>
            )}

            {yt.has_secret && !yt.connected && (
              <>
                <div className="formrow" style={{ marginBottom: "var(--space-4)" }}>
                  <button className="btn btn-secondary" disabled={busy}
                          onClick={() => run(async () => {
                            const r = await post<{ url: string }>("/settings/youtube/start", {});
                            setYtUrl(r.url);
                            window.open(r.url, "_blank");
                          })}>1. Connect with Google</button>
                </div>

                <Field label="2. Paste the redirected URL or code" group
                       hint="Approving lands you on a localhost page that won’t load — that’s expected. Copy the address bar.">
                  <input className="input" type="text"
                         placeholder="http://localhost/?code=4/0Axx…  or  4/0Axx…"
                         value={ytCode}
                         onChange={(e) => setYtCode(e.target.value)} />
                  <div className="formrow" style={{ marginTop: 8 }}>
                    <button className="btn btn-secondary" disabled={busy || !ytCode.trim()}
                            onClick={() => run(async () => {
                              await post("/settings/youtube/finish", { code: ytCode });
                              setYtUrl(""); setYtCode("");
                              await load();
                              notify("YouTube connected: uploads are now enabled.", "ok");
                            })}>3. Finish connection</button>
                  </div>
                </Field>
                {ytUrl && (
                  <p className="hint" style={{ marginTop: 8 }}>
                    Consent opened in a new tab (<a href={ytUrl} target="_blank" rel="noreferrer">open
                    again</a>).
                  </p>
                )}
              </>
            )}

            <div className="formrow" style={{ marginTop: "var(--space-4)" }}>
              <button className="btn btn-primary" disabled={busy} onClick={() => setStep(3)}>
                {yt.connected ? "Continue" : "Skip — I’ll upload manually"}
              </button>
            </div>
          </>
        )}

        {/* ── step 4: voice & music ────────────────────────────────── */}
        {step === 3 && (
          <>
            <h1>Narration and music</h1>
            <p className="hint" style={{ marginBottom: "var(--space-5)" }}>
              Kokoro runs on this machine, so narration is free and offline. The voice&apos;s
              language is what decides the language the script is written in — pick the one you
              want the video in, not the one you read.
            </p>

            <Field label="Voice" group hint="Grouped by language. Preview sends a short sample sentence.">
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                <select className="select" value={vo.voice} aria-label="Narration voice"
                        style={{ flex: 1, minWidth: 220 }}
                        onChange={(e) => setVo({ ...vo, voice: e.target.value })}>
                  {groupVoices(vo.voices).map(([group, list]) => (
                    <optgroup key={group} label={group}>
                      {list.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
                    </optgroup>
                  ))}
                </select>
                <PreviewButton url={`/api/tts/preview?voice=${vo.voice}`}
                               title={`Preview ${vo.voice}`}>
                  <Icon d={ICONS.play} size={14} /> Preview
                </PreviewButton>
              </div>
            </Field>

            <Field label={`Voice volume: ${au.voice_volume}`}
                   hint="How loud the narration is, 0–100.">
              <input type="range" min={0} max={100} step={1} aria-label="Voice volume level"
                     value={au.voice_volume}
                     onChange={(e) => setAu({ ...au, voice_volume: Number(e.target.value) })}
                     style={{ width: "100%", maxWidth: 420 }} />
            </Field>
            <Field label={`Music volume: ${au.music_volume}`} group
                   hint="How loud the background bed is. Lower numbers keep the music under the voice.">
              <input type="range" min={0} max={100} step={1} aria-label="Music bed volume level"
                     value={au.music_volume}
                     onChange={(e) => setAu({ ...au, music_volume: Number(e.target.value) })}
                     style={{ width: "100%", maxWidth: 420 }} />
              <div className="formrow" style={{ marginTop: 8 }}>
                <PreviewButton
                  title="Hear the voice over the bed at these levels"
                  url={`/api/mix/preview?track=${encodeURIComponent(tracks[0] || "")}` +
                       `&voice_volume=${au.voice_volume}&music_volume=${au.music_volume}`}>
                  <Icon d={ICONS.play} size={14} /> Preview mix
                </PreviewButton>
                <span className="hint" style={{ margin: 0 }}>
                  {tracks.length
                    ? `${tracks.length} CC0 beds installed; AVF picks one per video by theme.`
                    : "No music beds installed — videos render with narration only."}
                </span>
              </div>
            </Field>

            <div className="formrow">
              <button className="btn btn-primary" disabled={busy} onClick={saveVoiceAudio}>
                Save and continue
              </button>
              <button className="btn btn-secondary" disabled={busy} onClick={() => setStep(4)}>
                Skip
              </button>
              <span className="hint" style={{ margin: 0 }}>
                Fine-tune per bed in Settings → Audio.
              </span>
            </div>
          </>
        )}

        {/* ── step 5: subtitles ────────────────────────────────────── */}
        {step === 4 && (
          <>
            <h1>Subtitles</h1>
            <p className="hint" style={{ marginBottom: "var(--space-5)" }}>
              Captions are burned into the video, word-timed to the narration. Each preset is a
              different look — the preview shows the real renderer&apos;s output, not a mock-up.
            </p>

            <Field label="Caption preset" group>
              <SubtitlePicker presets={sub.presets} value={sub.preset}
                              onChange={(id) => setSub({ ...sub, preset: id })} />
            </Field>

            <div className="formrow">
              <button className="btn btn-primary" disabled={busy} onClick={saveSubtitle}>
                Save and continue
              </button>
              <button className="btn btn-secondary" disabled={busy} onClick={() => setStep(5)}>
                Skip
              </button>
            </div>
          </>
        )}

        {/* ── step 6: watermark ────────────────────────────────────── */}
        {step === 5 && (
          <>
            <h1>Watermark</h1>
            <p className="hint" style={{ marginBottom: "var(--space-4)" }}>
              Optional. A transparent PNG or WebP, drawn over every video. Off by default — leave
              it off and nothing is stamped on your videos.
            </p>

            <Field label="Watermark image"
                   hint="PNG or WebP with transparency, up to 5 MB. Resized to 2000px on the long edge.">
              <input type="file" accept=".png,.webp,image/png,image/webp" className="input input-file"
                      disabled={busy}
                     onChange={(e) => {
                       const f = e.target.files?.[0];
                       if (!f) return;
                       e.target.value = "";
                       void run(async () => {
                         const fd = new FormData();
                         fd.append("file", f);
                         const r = await fetch("/api/watermark/upload", { method: "POST", body: fd });
                         if (!r.ok) throw new Error((await r.text()).slice(0, 200) || `HTTP ${r.status}`);
                         await load();
                         notify(`Watermark saved from ${f.name}.`, "ok");
                       });
                     }} />
            </Field>

            <label style={{ display: "flex", alignItems: "center", gap: 8,
                            marginBottom: "var(--space-4)" }}>
              <input type="checkbox" disabled={busy || !wm.settings["9:16"]?.exists}
                     checked={Boolean(wm.settings["9:16"]?.enabled && wm.settings["9:16"]?.exists)}
                     style={{ width: 15, height: 15, accentColor: "var(--color-accent)" }}
                     onChange={(e) => void setWatermark(e.target.checked)} />
              Show the watermark on rendered videos
              {!wm.settings["9:16"]?.exists && (
                <span className="hint" style={{ margin: 0 }}>
                  Upload an image first.
                </span>
              )}
            </label>

            <div className="formrow">
              <button className="btn btn-primary" disabled={busy} onClick={() => setStep(6)}>
                Continue
              </button>
              <span className="hint" style={{ margin: 0 }}>
                Position, size and opacity live in Settings → Watermark.
              </span>
            </div>
          </>
        )}

        {/* ── step 7: schedule ─────────────────────────────────────── */}
        {step === 6 && (
          <>
            <h1>Automatic production</h1>
            <p className="hint" style={{ marginBottom: "var(--space-4)" }}>
              Optional. AVF can produce videos on a timetable while you are not looking. Nothing
              is ever uploaded without your approval — new videos wait on the dashboard.
            </p>

            {scheds.length ? (
              <>
                {scheds.map((s, i) => (
                  <label key={s.id} style={{
                    display: "flex", alignItems: "center", gap: 10,
                    background: "var(--color-surface-2)",
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-sm)", padding: "10px 12px",
                    marginBottom: 8, cursor: "pointer",
                  }}>
                    <input type="checkbox" checked={s.enabled}
                           style={{ width: 15, height: 15, accentColor: "var(--color-accent)" }}
                           onChange={(e) => setScheds(scheds.map((x, j) =>
                             j === i ? { ...x, enabled: e.target.checked } : x))} />
                    <span style={{ fontSize: "var(--text-sm)" }}>
                      <strong>{s.time}</strong> · {s.videos_per_run} video
                      {s.videos_per_run > 1 ? "s" : ""} · {s.format === "16:9" ? "landscape" : "portrait"}
                    </span>
                  </label>
                ))}
                <div className="formrow" style={{ marginBottom: "var(--space-4)" }}>
                  <button className="btn btn-secondary" disabled={busy} onClick={saveSchedule}>
                    Save schedule
                  </button>
                </div>
              </>
            ) : (
              <p className="msg msg-warn" style={{ display: "block", padding: "10px 12px",
                                                  marginBottom: "var(--space-4)" }}>
                No schedule yet. Add one in <strong>Settings → Automatic production</strong> — it
                needs a time, a video count and a format, which is more than a wizard step should
                ask for.
              </p>
            )}

            <div className="formrow">
              <button className="btn btn-primary" disabled={busy} onClick={() => setStep(DONE_STEP)}>
                Continue
              </button>
            </div>
          </>
        )}

        {/* ── done ─────────────────────────────────────────────────── */}
        {step === DONE_STEP && (
          <>
            <h1>You’re set up</h1>
            <p className="hint" style={{ marginBottom: "var(--space-4)" }}>
              Three things worth knowing before the first render.
            </p>
            <ul style={{ marginBottom: "var(--space-5)" }}>
              <li>
                Voice and subtitle models (~340 MB) download the first time you produce a video.
                That first run is slow; later ones aren’t.
              </li>
              <li>
                Scenes are filled from stock footage, so a scene is real moving video. If a search
                finds nothing for a scene, AVF falls back to a still image for that one scene only.
                Adding a second key — the other of Pexels or Pixabay — is the easiest way to reduce
                that. Keys live in <strong>Settings → Media</strong>.
              </li>
              <li>
                Everything AVF writes lives in one folder, shown on the System page. Rendered videos
                are under <code>content/rendered/</code>, logs under <code>runtime/logs/</code>.
                There is no output-folder setting to change.
              </li>
            </ul>
            <div className="formrow">
              <Link className="btn btn-primary" href="/">Produce your first video</Link>
              <Link className="btn btn-secondary" href="/settings">Go to Settings</Link>
            </div>
          </>
        )}
      </div>
    </>
  );
}
