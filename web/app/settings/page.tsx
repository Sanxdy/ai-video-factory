"use client";

/** Settings page: grouped, single source of truth for save + toast. */
import { useCallback, useEffect, useState } from "react";
import { api, post , Title} from "../lib";
import { useToast } from "../toast";
import { SectionCard, Field, Icon, ICONS, PreviewButton, groupVoices } from "./ui";
import DailySchedule from "./schedule";
import WatermarkSettings from "./watermark";
import SubtitlePicker from "./subtitle-picker";
import { ClientSecretHowTo, YouTubeProdWarning } from "./youtube-howto";

let _audio: HTMLAudioElement | null = null;
let _audioTrack = "";
const _setBtns = new Set<(playing: boolean) => void>();

function MusicRow({ track }: { track: string }) {
  const [playing, setPlaying] = useState(false);
  const { notify } = useToast();

  useEffect(() => {
    const fn = (p: boolean) => setPlaying(p);
    _setBtns.add(fn);
    return () => { _setBtns.delete(fn); };
  }, []);

  const toggle = () => {
    if (_audio && _audioTrack !== track) {
      _audio.pause();
      _audio = null;
    }
    if (!_audio || _audioTrack !== track) {
      _audio = new Audio(`/api/music/file?name=${encodeURIComponent(track)}`);
      _audio.volume = 0.7;
      _audioTrack = track;
      _audio.addEventListener("ended", () => _setBtns.forEach((f) => f(false)));
    }
    if (_audio.paused) {
      _audio.play().then(() => {
        _setBtns.forEach((f) => f(false));
        setPlaying(true);
      }).catch((e) => {
        _setBtns.forEach((f) => f(false));
        notify(`Cannot play ${track}: ${String(e?.message || e)}`, "err");
      });
    } else {
      _audio.pause();
      setPlaying(false);
    }
  };

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      border: "1px solid var(--color-border)", borderRadius: 8,
      padding: "6px 10px",
    }}>
      <button className="btn" type="button" title={`${playing ? "Pause" : "Play"} ${track}`}
              onClick={toggle} style={{ width: 40, minWidth: 40, padding: 0 }}>
        <Icon d={playing ? ICONS.pause : ICONS.play} size={14} />
      </button>
      <span style={{ fontSize: "var(--text-sm)" }}>{track.replace(".mp3", "")}</span>
    </div>
  );
}

type LLMConfig = {
  api_base_url: string;
  api_model: string;
  api_fallback_models: string;
  has_api_key: boolean;
  api_key_hint: string | null;
};

type ImageConfig = {
  provider: string;
  providers: string[];
  asset_mode: string;
  stock_active: boolean;
  stock_sources: string;
  has_pexels_key: boolean;
  pexels_key_hint: string | null;
  has_pixabay_key: boolean;
  pixabay_key_hint: string | null;
};

type SubtitleConfig = { preset: string; presets: { id: string; label: string }[] };
// `group` is the language heading — 41 voices read as seven short lists under
// <optgroup> instead of one long scroll.
type VoiceConfig = { voice: string; voices: { id: string; group: string; label: string }[] };
type DaemonConfig = { enabled: boolean; running: boolean; videos_per_day: number; start_hour: number; end_hour: number };

type YouTubeStatus = {
  connected: boolean;
  has_secret: boolean;
  alive?: boolean;
  channel_title?: string;
  channel_id?: string;
  error?: string;
};

const IMG_LABELS: Record<string, string> = {
  auto: "Auto: best available",
  pexels: "Stock photos: Pexels (API key)",
};

const SECTIONS = [
  { id: "llm", label: "AI model" },
  { id: "media", label: "Media" },
  { id: "audio", label: "Audio" },
  { id: "production", label: "Production" },
  { id: "watermark", label: "Watermark" },
  { id: "youtube", label: "YouTube" },
];

export default function SettingsPage() {
  const { notify } = useToast();
  const [cfg, setCfg] = useState<LLMConfig | null>(null);
  const [img, setImg] = useState<ImageConfig | null>(null);
  const [sub, setSub] = useState<SubtitleConfig | null>(null);
  const [vo, setVo] = useState<VoiceConfig | null>(null);
  const [dm, setDm] = useState<DaemonConfig | null>(null);
  const [yt, setYt] = useState<YouTubeStatus | null>(null);
  const [ytUrl, setYtUrl] = useState("");
  const [ytCode, setYtCode] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [pexelsKey, setPexelsKey] = useState("");
  const [pixabayKey, setPixabayKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [musicDb, setMusicDb] = useState(-18);
  const [voiceVolume, setVoiceVolume] = useState(80);
  const [musicVolume, setMusicVolume] = useState(40);
  const [tracks, setTracks] = useState<string[]>([]);
  /* Which bed the mix preview auditions. Was hardcoded to tracks[0] (whichever
     filename sorted first), so you could not hear the bed you were choosing. */
  const [mixTrack, setMixTrack] = useState("");
  const [dirty, setDirty] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState("llm");

  useEffect(() => {
    api<{ voice_volume: number; music_volume: number }>("/settings/audio")
      .then((r) => { setVoiceVolume(r.voice_volume); setMusicVolume(r.music_volume); })
      .catch(() => {});
    api<string[]>("/music").then(setTracks).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    const [s, im, sc, vc, dm0, y] = await Promise.all([
      api<LLMConfig>("/settings/llm"),
      api<ImageConfig>("/settings/image"),
      api<SubtitleConfig>("/settings/subtitle"),
      api<VoiceConfig>("/settings/voice"),
      api<DaemonConfig>("/settings/daemon"),
      api<YouTubeStatus>("/settings/youtube"),
    ]);
    setCfg(s);
    setImg(im);
    setSub(sc);
    setVo(vc);
    setDm(dm0);
    setYt(y);
  }, []);

  useEffect(() => { load().catch((e) => notify(String(e.message || e), "err")); }, [load]);

  const markDirty = (key: string) => setDirty((prev) => new Set(prev).add(key));
  const clearDirty = (key: string) => setDirty((prev) => { const n = new Set(prev); n.delete(key); return n; });

  const run = async (fn: () => Promise<void>, key: string) => {
    setBusy(true);
    try { await fn(); } catch (e: any) {
      notify(String(e.message || e), "err");
      setBusy(false);
      throw e;
    } finally { setBusy(false); }
    clearDirty(key);
  };

  const saveLLM = () => run(async () => {
    const payload = {
      api_base_url: cfg!.api_base_url || null,
      api_model: cfg!.api_model || null,
      api_fallback_models: cfg!.api_fallback_models || null,
      api_key: apiKey || null,
    };
    await post("/settings/llm", payload);
    setApiKey("");
    await load();
    notify("AI model settings saved.", "ok");
  }, "llm");

  const testLLM = () => run(async () => {
    const r = await post<{ ok: boolean; model?: string; reply?: string; error?: string }>("/llm/test", {
      api_base_url: cfg!.api_base_url || null,
      api_model: cfg!.api_model || null,
      api_fallback_models: cfg!.api_fallback_models || null,
      api_key: apiKey || null,
    });
    if (r.ok) notify(`Works: ${r.model} replied "${r.reply}"`, "ok");
    else notify(r.error || "Test failed", "err");
  }, "llm-test");

  const saveImage = () => run(async () => {
    await post("/settings/image", {
      provider: img!.provider, pexels_key: pexelsKey || null,
      pixabay_key: pixabayKey || null,
      stock_sources: img!.stock_sources,
      asset_mode: img!.asset_mode,
    });
    setPexelsKey("");
    setPixabayKey("");
    await load();
    notify("Image settings saved.", "ok");
  }, "media");

  /* Stock sources: both get searched, best match wins. The backend rejects an
     empty list, so refuse to deselect the last remaining source. */
  const stockSources = new Set(
    (img?.stock_sources || "pexels").split(",").map((s) => s.trim()).filter(Boolean));
  const toggleSource = (name: string) => {
    if (!img) return;
    const next = new Set(stockSources);
    if (next.has(name)) {
      if (next.size === 1) return;   // keep at least one
      next.delete(name);
    } else {
      next.add(name);
    }
    setImg({ ...img, stock_sources: ["pexels", "pixabay"].filter((s) => next.has(s)).join(",") });
    markDirty("media");
  };

  const saveSubtitle = () => run(async () => {
    await post("/settings/subtitle", { preset: sub!.preset });
    await load();
    notify("Subtitle preset saved.", "ok");
  }, "media");

  const saveAudio = () => run(async () => {
    await post("/settings/audio", { voice_volume: Number(voiceVolume), music_volume: Number(musicVolume) });
    await post("/settings/voice", { voice: vo!.voice });
    await load();
    notify("Audio settings saved.", "ok");
  }, "audio");

  const saveDaemon = () => run(async () => {
    await post("/settings/daemon", {
      enabled: dm!.enabled, videos_per_day: dm!.videos_per_day,
      start_hour: dm!.start_hour, end_hour: dm!.end_hour,
    });
    await load();
    notify(dm!.enabled ? "Production saved and running." : "Production saved: stopped.", "ok");
  }, "production");

  if (!cfg || !img || !sub || !vo || !dm || !yt) {
    return <div className="skeleton" style={{ height: 320 }} />;
  }

  const llmAction = (
    <div className="formrow">
      <button className="btn btn-secondary" onClick={testLLM} disabled={busy}>Test connection</button>
      <button className="btn btn-primary" onClick={saveLLM} disabled={busy}>Save AI model</button>
    </div>
  );

  return (
    <>
      <Title>{"Settings | AVF Console"}</Title>
      <div className="pagehead">
        <div>
          <h1>Settings</h1>
          <p>Configure the factory: AI model, media, audio, production, and publishing.</p>
        </div>
      </div>

      {/* Tabs, not a scroll-spy rail: each section used to live on one long page
          and the nav merely scrolled to it, so reaching Watermark meant scrolling
          past everything else. One section is mounted at a time. */}
      <nav aria-label="Settings sections" style={{ marginBottom: "var(--space-4)" }}>
        <div className="segmented-control" role="tablist">
          {SECTIONS.map((s) => (
            <button
              key={s.id}
              type="button"
              role="tab"
              id={`tab-${s.id}`}
              aria-selected={activeTab === s.id}
              aria-controls={`panel-${s.id}`}
              onClick={() => setActiveTab(s.id)}
              className="segmented-btn"
              data-active={activeTab === s.id}
            >
              {s.label}
            </button>
          ))}
        </div>
      </nav>

      <div role="tabpanel" id={`panel-${activeTab}`} aria-labelledby={`tab-${activeTab}`}>

      {activeTab === "llm" && (
      <SectionCard id="llm" title="AI model" description="LLM brain for the whole pipeline: your own API key (BYOK)." action={llmAction} dirty={dirty.has("llm")}>
        <p className="hint" style={{ marginBottom: 16 }}>
          Active now: <strong>api / {cfg.api_model || "not configured"}</strong>
        </p>
        <Field label="Base URL" hint="Any OpenAI-compatible endpoint: OpenAI, Groq, OpenRouter, DeepSeek, LM Studio…">
          <input className="input" type="text" placeholder="https://api.openai.com/v1"
                 value={cfg.api_base_url}
                 onChange={(e) => { setCfg({ ...cfg, api_base_url: e.target.value }); markDirty("llm"); }} />
        </Field>
        <Field label="Model name">
          <input className="input" type="text" placeholder="gpt-4o-mini"
                 value={cfg.api_model}
                 onChange={(e) => { setCfg({ ...cfg, api_model: e.target.value }); markDirty("llm"); }} />
        </Field>
        <Field label="Fallback models (comma-separated)" hint="Tried in order when the main model errors/timeouts (e.g. cbai/glm-5.2, cbai/kimi-k2.5)">
          <input className="input" type="text"
                 placeholder="model-a, model-b"
                 value={cfg.api_fallback_models}
                 onChange={(e) => { setCfg({ ...cfg, api_fallback_models: e.target.value }); markDirty("llm"); }} />
        </Field>
        <Field label="API key">
          <input className="input" type="password" placeholder={
            cfg.has_api_key ? `Stored (${cfg.api_key_hint}): leave empty to keep` : "sk-…"
          }
                 value={apiKey}
                 onChange={(e) => { setApiKey(e.target.value); markDirty("llm"); }} />
        </Field>
      </SectionCard>
      )}

      {activeTab === "media" && (
      <SectionCard id="media" title="Media" description="Scene images, motion mode, and subtitle look." dirty={dirty.has("media")}>
        <Field label="Image provider" hint="What fills the video frame. Auto picks the best installed source and falls back on failure.">
          <select className="select" value={img.provider} aria-label="Image provider"
                  onChange={(e) => { setImg({ ...img, provider: e.target.value }); markDirty("media"); }}>
            {img.providers.map((p) => (
              <option key={p} value={p}>{IMG_LABELS[p] ?? p}</option>
            ))}
          </select>
        </Field>
        <Field label="Motion mode" hint={img.stock_active ? "Stock footage active: scenes use real video clips from Pexels + Pixabay." : "No stock key: scenes fall back to still images. Free keys: pexels.com/api and pixabay.com/api/docs"}>
          <select className="select" value={img.asset_mode} aria-label="Motion mode"
                  onChange={(e) => { setImg({ ...img, asset_mode: e.target.value }); markDirty("media"); }}>
            <option value="auto">Auto: stock video when key present, else images</option>
            <option value="stock">Stock footage: real moving video (Pexels / Pixabay)</option>
            <option value="image">Still images only</option>
          </select>
        </Field>
        <Field label="Stock sources" group
               hint="Both get searched and the best match wins — ties are broken at random, so consecutive videos stop landing on the same clip.">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {([
              ["pexels", "Pexels", img.has_pexels_key,
               "Searched with the full fallback chain. No caching requirement."],
              ["pixabay", "Pixabay", img.has_pixabay_key,
               "Searched on the primary query only. Results cached 24h. Throttled to 30/min."],
            ] as const).map(([id, name, hasKey, meta]) => {
              const on = stockSources.has(id);
              return (
                <label key={id} style={{
                  display: "flex", alignItems: "flex-start", gap: 10,
                  background: "var(--color-surface-2)",
                  border: `1px solid ${on ? "rgba(var(--color-accent-rgb),.45)" : "var(--color-border)"}`,
                  borderRadius: "var(--radius-sm)", padding: "10px 12px", cursor: "pointer",
                }}>
                  <input type="checkbox" checked={on} onChange={() => toggleSource(id)}
                         aria-label={`Use ${name} as a stock source`}
                         style={{ marginTop: 3, width: 15, height: 15, flex: "0 0 auto",
                                  accentColor: "var(--color-accent)" }} />
                  <span>
                    <span style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>{name}</span>
                    <span className={`badge ${hasKey ? "badge-complete" : "badge-approval"}`}
                          style={{ marginLeft: 6 }}>
                      {hasKey ? "key stored" : "no key"}
                    </span>
                    <span style={{ display: "block", color: "var(--color-muted)",
                                   fontSize: "var(--text-xs)", marginTop: 2 }}>{meta}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </Field>
        {(img.provider === "pexels" || img.provider === "auto") && (
          <Field label="Pexels API key">
            <input className="input" type="password"
                   placeholder={img.has_pexels_key ? `Stored (${img.pexels_key_hint}): leave empty to keep` : "free key at pexels.com/api"}
                   value={pexelsKey}
                   onChange={(e) => { setPexelsKey(e.target.value); markDirty("media"); }} />
          </Field>
        )}
        {stockSources.has("pixabay") && (
          <Field label="Pixabay API key"
                 hint="Free key at pixabay.com/api/docs — required for the Pixabay source.">
            <input className="input" type="password"
                   placeholder={img.has_pixabay_key ? `Stored (${img.pixabay_key_hint}): leave empty to keep` : "free key at pixabay.com/api/docs"}
                   value={pixabayKey}
                   onChange={(e) => { setPixabayKey(e.target.value); markDirty("media"); }} />
          </Field>
        )}
        <div className="formrow" style={{ marginTop: 12 }}>
          <button className="btn btn-primary" onClick={saveImage} disabled={busy}>Save media</button>
        </div>

        <hr className="rule" />

        <Field label="Subtitle preset" group hint="Caption look burned into the final video. Applies on the next render.">
          <SubtitlePicker presets={sub.presets} value={sub.preset}
                          onChange={(id) => { setSub({ ...sub, preset: id }); markDirty("media"); }} />
        </Field>
        <div className="formrow">
          <button className="btn btn-primary" onClick={saveSubtitle} disabled={busy}>Save subtitle</button>
        </div>
      </SectionCard>
      )}

      {activeTab === "audio" && (
      <SectionCard id="audio" title="Audio" description="Narration voice, music beds, and mix level." dirty={dirty.has("audio")}>
        <Field label="Voice" group hint="Kokoro TTS (local, free). Pick by language — the voice's language is what the script gets read in. Applies on the next render.">
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <select className="select" value={vo.voice} aria-label="Narration voice"
                    onChange={(e) => { setVo({ ...vo, voice: e.target.value }); markDirty("audio"); }}
                    style={{ flex: 1, minWidth: 220 }}>
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
        <Field label="Music beds (auto per theme, or override per video)" group>
          {tracks.length ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))", gap: 10 }}>
              {tracks.map((t) => <MusicRow key={t} track={t} />)}
            </div>
          ) : (
            <p className="msg msg-warn" style={{ display: "block", padding: "10px 12px", margin: 0 }}>
              No music beds installed. Videos render with narration only until at
              least one <code>.mp3</code> is in the app&apos;s music folder.
            </p>
          )}
          <p className="hint">Real CC0 music (OpenGameArt). These loop under the narration and duck while the voice speaks.</p>
        </Field>
        <Field label={`Voice volume: ${voiceVolume}`} hint="How loud the narration/VO is. 0 = silent, 100 = maximum.">
          <input type="range" min={0} max={100} step={1}
                 aria-label="Voice volume level"
                 value={voiceVolume}
                 onChange={(e) => { setVoiceVolume(Number(e.target.value)); markDirty("audio"); }}
                 style={{ width: "100%", maxWidth: 420 }} />
        </Field>
        <Field label={`Music volume: ${musicVolume}`} group hint="How loud the background music is. 0 = silent, 100 = maximum. Lower numbers make the bed quieter under the voice.">
          <input type="range" min={0} max={100} step={1}
                 aria-label="Music bed volume level"
                 value={musicVolume}
                 onChange={(e) => { setMusicVolume(Number(e.target.value)); markDirty("audio"); }}
                 style={{ width: "100%", maxWidth: 420 }} />
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 8 }}>
            <PreviewButton
              className="btn"
              title="Hear VO + the selected bed at these levels"
              url={`/api/mix/preview?track=${encodeURIComponent(mixTrack || tracks[0] || "")}` +
                   `&voice_volume=${voiceVolume}&music_volume=${musicVolume}`}>
              <Icon d={ICONS.play} size={14} /> Preview mix (VO + BGM)
            </PreviewButton>
            {tracks.length > 0 && (
              <select className="select" aria-label="Music bed for the preview"
                      value={mixTrack || tracks[0]} style={{ maxWidth: 220 }}
                      onChange={(e) => setMixTrack(e.target.value)}>
                {tracks.map((t) => <option key={t} value={t}>{t.replace(/\.(mp3|wav)$/, "")}</option>)}
              </select>
            )}
          </div>
          {!tracks.length && (
            <p className="hint" style={{ marginTop: 6 }}>
              The mix preview needs a music bed, so it will fail until one is in{" "}
              <code>content/music/</code>.
            </p>
          )}
        </Field>
        <div className="formrow">
        <button className="btn btn-primary" onClick={saveAudio} disabled={busy}>Save audio</button>
        </div>
      </SectionCard>
      )}

      {/* Production owns two cards: the daemon itself and its schedule table. */}
      {activeTab === "production" && (
      <>
      <SectionCard id="production" title="Automatic production" description="Keep the factory running: resumes stuck projects and produces new videos inside the daily window. Videos wait for your approval (nothing uploads by itself)." dirty={dirty.has("production")}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <input type="checkbox" checked={dm.enabled}
                 aria-label="Enable automatic production"
                 onChange={(e) => { setDm({ ...dm, enabled: e.target.checked }); markDirty("production"); }} />
          Enabled {dm.running
            ? <strong style={{ color: "var(--color-success-text)" }}>: running</strong>
            : <span style={{ color: "var(--color-muted)" }}>: stopped</span>}
        </label>
        <div className="formrow">
          <div className="field" style={{ flex: 1, minWidth: 120 }}>
            <span>Videos per day (1-20)</span>
            <input className="input" type="number" min={1} max={20}
                   aria-label="Videos per day" value={dm.videos_per_day}
                   onChange={(e) => { setDm({ ...dm, videos_per_day: Number(e.target.value) || 1 }); markDirty("production"); }} />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 120 }}>
            <span>Window start hour (0-23)</span>
            <input className="input" type="number" min={0} max={23}
                   aria-label="Production window start hour" value={dm.start_hour}
                   onChange={(e) => { setDm({ ...dm, start_hour: Number(e.target.value) }); markDirty("production"); }} />
          </div>
          <div className="field" style={{ flex: 1, minWidth: 120 }}>
            <span>Window end hour (0-23)</span>
            <input className="input" type="number" min={0} max={23}
                   aria-label="Production window end hour" value={dm.end_hour}
                   onChange={(e) => { setDm({ ...dm, end_hour: Number(e.target.value) }); markDirty("production"); }} />
          </div>
        </div>
        <div className="formrow">
          <button className="btn btn-primary" onClick={saveDaemon} disabled={busy}>Save production</button>
        </div>
      </SectionCard>
      <DailySchedule />
      </>
      )}

      {activeTab === "watermark" && <WatermarkSettings />}

      {activeTab === "youtube" && (
      <SectionCard id="youtube" title="YouTube" description="Connect once so 'Approve & upload' can publish (unlisted). Needs a Desktop-app OAuth client from Google Cloud Console with the YouTube Data API v3 enabled." dirty={dirty.has("youtube")}>
        <p className="hint" style={{ marginBottom: 12 }}>
          Status:{" "}
          {yt.connected ? (
            yt.alive === false ? (
              <strong style={{ color: "var(--color-danger-text)" }}><Icon d={ICONS.warning} size={14} /> Broken: {yt.error}</strong>
            ) : yt.alive ? (
              <strong style={{ color: "var(--color-success-text)" }}>Connected: {yt.channel_title} ({yt.channel_id})</strong>
            ) : (
              <strong>Connected: checking…</strong>
            )
          ) : yt.has_secret ? (
            "Secret stored: not connected yet"
          ) : (
            "Not connected"
          )}
        </p>

        {yt.connected && (
          <p className="hint" style={{ marginBottom: 16 }}>
            <a className="btn btn-secondary" style={{ display: "inline-block" }} href={`https://www.youtube.com/channel/${yt.channel_id || ""}`} target="_blank" rel="noreferrer">Open channel ↗</a>{" "}
            <button className="btn btn-secondary" disabled={busy}
                    onClick={() => run(async () => {
                      await fetch("/api/settings/youtube", { method: "DELETE" });
                      await load();
                      notify("Disconnected. Use Connect to login with another account.", "ok");
                    }, "youtube")}>Disconnect / change account</button>
            {" "}<span>Status is verified live on every page load.</span>
          </p>
        )}

        {!yt.has_secret && (
          <>
            <YouTubeProdWarning style={{ marginBottom: 16 }} />
            <ClientSecretHowTo />
            <Field label="Client secret (client_secret.json)">
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
                     }, "youtube");
                   }} />
            </Field>
          </>
        )}

        {yt.has_secret && (
          <>
            {/* The 7-day token expiry is the usual reason someone is looking at
                this panel with a secret already stored, so the fix belongs here
                too — not only in the wizard they ran a week ago. */}
            {!yt.connected && <YouTubeProdWarning style={{ marginBottom: 16 }} />}
            <div className="formrow" style={{ marginTop: 8 }}>
              <button className="btn btn-primary" disabled={busy}
                      onClick={() => run(async () => {
                        const r = await post<{ url: string }>("/settings/youtube/start", {});
                        setYtUrl(r.url);
                        window.open(r.url, "_blank");
                      }, "youtube")}>{yt.connected
                        ? "Re-login / grant new permissions"
                        : "1. Connect with Google"}</button>
              {yt.connected && (
                <span className="hint">Use this to refresh an expired token or add permissions (e.g. playlists).</span>
              )}
            </div>
            {ytUrl && (
              <p className="hint" style={{ marginTop: 8 }}>
                Consent opened in a new tab (<a href={ytUrl} target="_blank" rel="noreferrer">open again</a>). After approving, Google lands you on a <code>localhost</code> page that won&apos;t load (that&apos;s fine).
              </p>
            )}
            <Field label="2. Paste the redirected URL or code" group>
              <input className="input" type="text"
                     placeholder="http://localhost/?code=4/0Axx…  or  4/0Axx…"
                     value={ytCode}
                     onChange={(e) => setYtCode(e.target.value)} />
              <div className="formrow" style={{ marginTop: 8 }}>
                <button className="btn btn-primary" disabled={busy || !ytCode.trim()}
                        onClick={() => run(async () => {
                          await post("/settings/youtube/finish", { code: ytCode });
                          setYtUrl(""); setYtCode("");
                          await load();
                          notify("YouTube connected: uploads are now enabled.", "ok");
                        }, "youtube")}>3. Finish connection</button>
              </div>
            </Field>
          </>
        )}
      </SectionCard>
      )}
      </div>{/* /tabpanel */}
    </>
  );
}
