"use client";

/** Project detail: stage progress, video player, script/scenes, actions. */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, post, upload, del, statusBadge, Icon, ICONS, useSSE } from "../../lib";
import { useToast } from "../../toast";

type Progress = { stage: string; pct: number; detail: string; started: number };
type Detail = {
  id: number; status: string; topic: string | null;
  youtube_id: string | null; has_video: boolean;
  progress?: Progress | null; video_v?: number | null; music?: string;
  aspect_ratio?: string | null; storyboard_only?: number | null;
  script: { hook?: string; body?: string; cta?: string } | null;
  scenes: { scene_number: number; narration: string; visual_prompt: string;
            duration: number; status: string; has_video?: boolean;
            asset_name?: string | null }[];
};

/** Pipeline steps shown in the UI; lists map DB statuses → one step. */
const STEPS: [string, string[]][] = [
  ["Idea", ["IDEA"]],
  ["Research", ["RESEARCHING", "RESEARCHED"]],
  ["Script", ["SCRIPTING", "SCRIPTED"]],
  ["Storyboard", ["STORYBOARDING", "STORYBOARDED"]],
  ["Assets", ["GENERATING_ASSETS", "ASSETS_READY"]],
  ["Audio", ["GENERATING_AUDIO", "AUDIO_READY"]],
  ["Subtitles", ["GENERATING_SUBTITLES", "SUBTITLES_READY"]],
  ["Edit", ["EDITING", "RENDERED"]],
  ["Quality", ["QUALITY_CHECK"]],
  ["Approval", ["APPROVAL"]],
  ["Upload", ["UPLOADING", "UPLOADED", "ANALYTICS"]],
  ["Done", ["COMPLETE"]],
];
const ACTIVE = new Set(["COMPLETE", "FAILED", "CANCELLED"]);

function PlaylistField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [pls, setPls] = useState<{ id: string; title: string }[]>([]);
  const [mode, setMode] = useState<"pick" | "new">("pick");
  useEffect(() => {
    api<{ playlists: { id: string; title: string }[] }>("/youtube/playlists")
      .then((d) => setPls(d.playlists || [])).catch(() => {});
  }, []);
  // keep select in sync when a playlist is pre-set (e.g. daily schedule)
  useEffect(() => {
    if (value && pls.some((p) => p.title === value)) setMode("pick");
    else if (value) setMode("new");
  }, [value, pls]);
  return (
    <div className="field" style={{ maxWidth: 340 }}>
      <span>Playlist</span>
      {mode === "pick" ? (
        <>
          <select className="input" value={pls.some((p) => p.title === value) ? value : ""}
                  onChange={(e) => {
                    if (e.target.value === "__new__") setMode("new");
                    else onChange(e.target.value);
                  }}>
            <option value="">None</option>
            {pls.map((p) => <option key={p.id} value={p.title}>{p.title}</option>)}
            <option value="__new__">➕ Create new…</option>
          </select>
          <p className="hint">Pick an existing playlist, or create a new one.</p>
        </>
      ) : (
        <>
          <input className="input" placeholder="New playlist name"
                 value={value} onChange={(e) => onChange(e.target.value)} autoFocus />
          <button type="button" className="btn btn-secondary btn-sm"
                  onClick={() => { setMode("pick"); onChange(""); }}>
            ← Back to existing
          </button>
          <p className="hint">Will be created on the channel at upload.</p>
        </>
      )}
    </div>
  );
}

export default function ProjectShell() {
  // read id from the URL directly: static-export shells carry a baked RSC
  // param ("0"), so useParams/usePathname would return the wrong id
  const id = typeof window !== "undefined"
    ? window.location.pathname.split("/").pop() : undefined;
  const [p, setP] = useState<Detail | null>(null);
  const [msg, setMsg] = useState("");
  const [failed, setFailed] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [copied, setCopied] = useState<number | null>(null);
  const [uploadingScene, setUploadingScene] = useState<number | null>(null);
  const [pubAt, setPubAt] = useState("");
  const [privacy, setPrivacy] = useState("public");
  const [playlist, setPlaylist] = useState("");
  const [ytTitle, setYtTitle] = useState("");
  const [ytDesc, setYtDesc] = useState("");
  const [suggestBusy, setSuggestBusy] = useState(false);
  const [titleVariants, setTitleVariants] = useState<
    { title: string; score: number; why: string }[]>([]);
  const [musicTracks, setMusicTracks] = useState<string[]>([]);
  const [musicTrack, setMusicTrack] = useState("");
  const [thumbUrl, setThumbUrl] = useState("");
  const [thumbBusy, setThumbBusy] = useState(false);
  const fileInputs = useRef<Record<number, HTMLInputElement | null>>({});

  /* Was a second, private toast: same job as the app-wide one, but it carried no
     variant, so a failure looked exactly like a success. */
  const { notify } = useToast();

  const load = useCallback(() => {
    if (!id) return;
    setFailed(false);
    api<Detail>(`/projects/${id}`).then((d) => {
      setP(d);
      setMusicTrack(d.music || "");
    }).catch(() => setFailed(true));
  }, [id]);
  useEffect(load, [load]);
  // music list is static per server: fetch once
  useEffect(() => {
    api<string[]>("/music").then(setMusicTracks).catch(() => {});
  }, []);

  async function pickMusic(track: string) {
    setMusicTrack(track);
    try {
      await post(`/projects/${id}/music`, { track });
      notify(track ? `Music bed: ${track}: used on the next render`
                   : "Music bed reset to automatic");
    } catch (e) { notify(`Save failed: ${String(e).slice(0, 120)}`, "err"); }
  }

  const onEvent = useCallback((e: MessageEvent) => {
    const d = JSON.parse(e.data);
    if (String(d.project ?? d.project_id) === id) load();
  }, [id, load]);
  useSSE(onEvent);

  // while the pipeline runs: re-render each second (elapsed timer) and poll
  // every 3s as a safety net alongside SSE progress events
  const [, tick] = useState(0);
  const running = !!p && !ACTIVE.has(p.status) && p.status !== "APPROVAL";
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => tick((n) => n + 1), 1000);
    const poll = setInterval(load, 3000);
    return () => { clearInterval(t); clearInterval(poll); };
  }, [running, load]);

  async function act(path: string, body?: unknown) {
    try {
      await post(`/projects/${id}/${path}`, body);
      setMsg("");
      setTimeout(load, 800);
    } catch (e) { setMsg(String(e)); }
  }

  /** navigator.clipboard needs a secure context; execCommand covers file:// + http://127.0.0.1 */
  async function copyText(text: string): Promise<boolean> {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch { /* fall through */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch { return false; }
  }

  async function copyPrompt(sceneNum: number, text: string) {
    if (await copyText(text)) {
      setCopied(sceneNum);
      notify(`Prompt scene ${sceneNum} copied to clipboard`);
      setTimeout(() => setCopied(null), 1500);
    } else {
      notify("Copy failed: select the prompt text and copy manually", "err");
    }
  }

  async function onFilePicked(sceneNum: number, f: File | undefined) {
    const input = fileInputs.current[sceneNum];
    if (input) input.value = ""; // allow re-picking the same file
    if (!f) return;
    setUploadingScene(sceneNum);
    try {
      await upload(`/projects/${id}/scenes/${sceneNum}/asset`, f);
      load(); // static "Using …" line below the button shows the stored file
      load();
    } catch (e) { notify(`Upload failed: ${String(e).slice(0, 120)}`, "err"); }
    finally { setUploadingScene(null); }
  }

  async function removeAsset(sceneNum: number) {
    try {
      await del(`/projects/${id}/scenes/${sceneNum}/asset`);
      notify(`Video removed from scene ${sceneNum}`);
      load();
    } catch (e) { notify(`Remove failed: ${String(e).slice(0, 120)}`, "err"); }
  }

  async function generateThumb() {
    setThumbBusy(true);
    try {
      const r = await post<{ url: string }>(`/projects/${id}/thumbnail`, {});
      setThumbUrl(r.url);
      notify("Thumbnail generated: download it and set it on YouTube");
    } catch (e) { notify(`Thumbnail failed: ${String(e).slice(0, 120)}`, "err"); }
    finally { setThumbBusy(false); }
  }

  async function suggestMeta() {
    setSuggestBusy(true);
    try {
      const r = await post<{ title: string; description: string;
                             titles: { title: string; score: number; why: string }[] }>(
        `/projects/${id}/suggest-title`, {});
      setYtTitle(r.title);
      if (r.description) setYtDesc(r.description);
      setTitleVariants(r.titles || []);
      notify("Title & description suggested: edit freely before approving");
    } catch (e) { notify(`Suggest failed: ${String(e).slice(0, 120)}`, "err"); }
    finally { setSuggestBusy(false); }
  }

  if (failed) {
    return (
      <div className="card" style={{ marginTop: "var(--space-6)" }}>
        <p className="msg msg-err">Could not load project {id}.</p>
        <button className="btn btn-secondary" style={{ marginTop: "var(--space-3)" }}
                onClick={load}>Retry</button>
      </div>
    );
  }

  if (!p) {
    return (
      <>
        <div className="skeleton" style={{ height: 40, width: "60%", marginBottom: 16 }} />
        <div className="skeleton" style={{ height: 300 }} />
      </>
    );
  }

  const b = statusBadge(p.status);
  const stepIdx = STEPS.findIndex(([, states]) => states.includes(p.status));
  const cancellable = !ACTIVE.has(p.status);

  return (
    <>
      <p style={{ marginBottom: "var(--space-3)" }}>
        <Link href="/" style={{ color: "var(--color-muted)", display: "inline-flex",
             alignItems: "center", gap: 6 }}>
          <Icon d="M19 12H5m7-7l-7 7 7 7" size={15} /> Dashboard
        </Link>
      </p>

      <div className="pagehead">
        <div>
          <h1>{p.topic || `Project ${p.id}`}</h1>
          <p style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span className={`badge ${b.cls}`}>{b.label}</span>
            {p.storyboard_only === 1 && p.aspect_ratio && (
              <span className="badge badge-neutral">
                {p.aspect_ratio === "16:9" ? "16:9 Landscape" : "9:16 Shorts"}
              </span>
            )}
            {p.youtube_id && (
              <a href={`https://youtube.com/watch?v=${p.youtube_id}`}
                 target="_blank" rel="noreferrer"
                 style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                <Icon d={ICONS.yt} size={15} /> Watch on YouTube
              </a>
            )}
          </p>
        </div>
        {(cancellable || p.has_video) && (
          <div style={{ display: "flex", gap: "var(--space-3)" }}>
            <button className="btn btn-secondary" disabled={running}
                    onClick={async () => {
                      try {
                        const r = await post<{ project: number }>(`/projects/${id}/clone`, {});
                        notify(`Cloned as project ${r.project}: rendering now`);
                      } catch (e) { notify(`Clone failed: ${String(e).slice(0, 120)}`, "err"); }
                    }}>
              <Icon d={ICONS.copy} size={16} /> Clone
            </button>
            {p.has_video && (
              <button className="btn btn-secondary" onClick={() => { notify("Rebuild started: watch the status above"); act("retry"); }}>
                <Icon d={ICONS.refresh} size={16} /> Rebuild video
              </button>
            )}
            {cancellable && (
              <button className="btn btn-danger" onClick={() => act("cancel")}>
                <Icon d={ICONS.x} size={16} /> Cancel
              </button>
            )}
          </div>
        )}
      </div>

      {stepIdx >= 0 && (
        <div className="steps" aria-label="Pipeline progress">
          {STEPS.map(([label], i) => (
            <div key={label}
                 className={`step${i < stepIdx ? " step-done" : ""}${i === stepIdx ? " step-active" : ""}`}>
              <span className="step-dot" />
              <span className="step-label">{label}</span>
            </div>
          ))}
        </div>
      )}

      {running && (() => {
        const prog = p!.progress;
        const pct = prog?.pct ?? Math.round(((stepIdx + 1) / STEPS.length) * 100);
        const label = STEPS[stepIdx]?.[0] ?? p!.status;
        const elapsed = prog?.started ? Math.max(0, Math.floor(Date.now() / 1000 - prog.started)) : null;
        return (
          <div className="card" aria-live="polite" style={{ marginTop: "var(--space-4)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12,
                          fontSize: "var(--text-base)", marginBottom: 8 }}>
              <span style={{ fontWeight: 600 }}>
                {label}{prog?.detail ? ` : ${prog.detail}` : ""}…
              </span>
              <span style={{ color: "var(--color-muted)", fontVariantNumeric: "tabular-nums",
                             flexShrink: 0 }}>
                {pct}%{elapsed !== null ? ` · ${elapsed}s` : ""}
              </span>
            </div>
            <div style={{ height: 6, borderRadius: 3, overflow: "hidden",
                          background: "var(--color-surface-3)" }}>
              <div style={{ height: "100%", width: `${pct}%`,
                            background: "var(--color-accent)",
                            transition: "width .6s ease" }} />
            </div>
            <p className="hint" style={{ margin: "var(--space-2) 0 0" }}>
              Assembling clips + audio + subtitles is fast (~15–60s). Generating AI
              assets or a full run from scratch takes minutes to hours.
            </p>
          </div>
        );
      })()}

      {(p.status === "FAILED" || p.status === "CANCELLED") && (
        <div className={p.status === "FAILED" ? "msg msg-err" : "msg"}
             style={{ marginTop: "var(--space-4)" }}>
          <span>
            {p.status === "FAILED"
              ? <>Production failed. Check <code>avf doctor</code> and the server logs.</>
              : "Cancelled: nothing was uploaded."}
          </span>
          <button className="btn btn-secondary btn-sm"
                  onClick={() => act("retry")}>
            <Icon d={ICONS.refresh} size={15} /> Retry
          </button>
        </div>
      )}

      {p.has_video && (() => {
        const src = `/api/video/${p.id}${p.video_v ? `?v=${p.video_v}` : ""}`;
        // keep the visible project name; strip only filesystem-illegal chars
        const name = (p.topic || `project-${p.id}`)
          .replace(/[/\\:*?"<>|]+/g, "").trim().replace(/\.+$/, "") || `project-${p.id}`;
        return (
          <>
            <video className="player" controls preload="metadata" key={src}
                   style={{ marginTop: "var(--space-5)" }}>
              <source src={src} type="video/mp4" />
            </video>
            <p style={{ marginTop: "var(--space-2)", display: "flex", gap: 8, flexWrap: "wrap" }}>
              <a className="btn btn-secondary btn-sm" href={src} download={`${name}.mp4`}>
                <Icon d={ICONS.download} size={15} /> Download video
              </a>
              <a className="btn btn-secondary btn-sm" href={`/api/subtitles/${p.id}`}
                 style={{ marginLeft: 8 }}>
                <Icon d={ICONS.download} size={15} /> Download subtitles
              </a>
              <button className="btn btn-secondary btn-sm" disabled={thumbBusy}
                      onClick={generateThumb}>
                <Icon d={ICONS.film} size={15} />
                {thumbBusy ? "Rendering…" : "Generate YouTube thumbnail"}
              </button>
              {thumbUrl && (
                <a className="btn btn-secondary btn-sm" href={thumbUrl}
                   download={`${name}-thumbnail.jpg`}>
                  <Icon d={ICONS.download} size={15} /> Download thumbnail
                </a>
              )}
            </p>
            {thumbUrl && (
              <>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={thumbUrl} alt="YouTube thumbnail preview" width={320}
                     style={{ borderRadius: "var(--radius)", marginTop: 8, display: "block" }} />
                <p className="hint">Mid-frame with the title burned in: upload it as
                  the custom thumbnail on YouTube.</p>
              </>
            )}
          </>
        );
      })()}

      {p.status === "APPROVAL" && (
        <div className="card" style={{ marginTop: "var(--space-4)" }}>
          <div style={{ display: "flex", gap: "var(--space-3)", flexWrap: "wrap",
                        alignItems: "center" }}>
            <span style={{ color: "var(--color-muted)", fontSize: "var(--text-base)", marginRight: "auto" }}>
              This video passed quality control and needs your approval.
            </span>
            <button className="btn btn-success" onClick={() => {
              const meta: Record<string, string> = {};
              if (ytTitle.trim()) meta.title = ytTitle.trim();
              if (ytDesc.trim()) meta.description = ytDesc.trim();
              if (pubAt) meta.publish_at = new Date(pubAt).toISOString();
              if (privacy && privacy !== "public") meta.privacy = privacy;
              if (playlist.trim()) meta.playlist = playlist.trim();
              act("approve", Object.keys(meta).length ? meta : undefined);
              setPubAt("");
            }}>
              <Icon d={ICONS.check} size={16} /> Approve &amp; upload
            </button>
            <button className="btn btn-secondary" onClick={() => setRejecting(!rejecting)}>
              <Icon d={ICONS.refresh} size={16} /> Revise / reject
            </button>
          </div>
          <div style={{ display: "grid", gap: "var(--space-3)",
                        marginTop: "var(--space-4)" }}>
            <div className="field">
              <span>YouTube title</span>
              <input className="input" placeholder="Auto-generated if left empty"
                     maxLength={100} value={ytTitle}
                     onChange={(e) => setYtTitle(e.target.value)} />
              {!!titleVariants.length && (
                <div style={{ display: "grid", gap: 6, marginTop: 8 }}>
                  {titleVariants.map((v) => (
                    <button key={v.title} type="button" className="btn btn-secondary btn-sm"
                            style={{ justifyContent: "flex-start", textAlign: "left" }}
                            onClick={() => { setYtTitle(v.title); }}>
                      <strong style={{ fontVariantNumeric: "tabular-nums" }}>{v.score}</strong>
                      <span>{v.title}</span>
                      <span style={{ color: "var(--color-muted)", fontWeight: 400 }}>
                        {v.why}
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <div className="field">
              <span>Description</span>
              <textarea className="input" rows={3}
                        placeholder="Auto-generated if left empty"
                        value={ytDesc}
                        onChange={(e) => setYtDesc(e.target.value)} />
            </div>
            <div className="field" style={{ maxWidth: 340 }}>
              <span>Visibility</span>
              <div className="formrow">
                {["public", "unlisted", "private"].map((p) => (
                  <button key={p} type="button"
                          className={`btn btn-sm ${privacy === p ? "btn-primary" : "btn-secondary"}`}
                          onClick={() => setPrivacy(p)}>{p}</button>
                ))}
              </div>
              <p className="hint">Public: visible to everyone. Unlisted: anyone with the link.
                Private: only you.</p>
            </div>
            <PlaylistField value={playlist} onChange={setPlaylist} />
            <div className="field" style={{ maxWidth: 340 }}>
              <span>Schedule publish (optional)</span>
              <input className="input" type="datetime-local" aria-label="Schedule publish time"
                     value={pubAt} onChange={(e) => setPubAt(e.target.value)} />
              <p className="hint">Leave empty to upload at the chosen visibility now. With a time, the
                video uploads private and YouTube makes it public at that moment.</p>
            </div>
            <div className="formrow">
              <button className="btn btn-secondary btn-sm" disabled={suggestBusy}
                      onClick={suggestMeta}>
                <Icon d={ICONS.refresh} size={14} />
                {suggestBusy ? "Thinking…" : "Suggest with AI"}
              </button>
            </div>
          </div>
          {rejecting && (
            <div className="field" style={{ marginTop: "var(--space-4)" }}>
              <span>What should change?</span>
              <input className="input" autoFocus placeholder="e.g. shorter hook, different visuals"
                     value={reason} onChange={(e) => setReason(e.target.value)} />
              <p className="hint">Revise regenerates the script, storyboard and video with
                 your note. Reject discards the video entirely.</p>
              <div className="formrow" style={{ marginTop: "var(--space-2)" }}>
                <button className="btn btn-secondary"
                        onClick={() => { setRejecting(false); setReason(""); }}>Keep it</button>
                <button className="btn btn-primary" disabled={!reason.trim()} onClick={() => {
                  setRejecting(false);
                  act("revise", { reason });
                  setReason("");
                }}>Revise</button>
                <button className="btn btn-danger" onClick={() => {
                  setRejecting(false);
                  act("reject");
                  setReason("");
                }}>Reject</button>
              </div>
            </div>
          )}
        </div>
      )}
      {msg && <p className="msg msg-err" style={{ marginTop: "var(--space-3)" }}>{msg}</p>}

      {p.script && (
        <section className="card" style={{ marginTop: "var(--space-5)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <h2 style={{ marginRight: "auto" }}>Script</h2>
            <button className="btn btn-secondary btn-sm" onClick={async () => {
              const text = [p.script?.hook, p.script?.body, p.script?.cta]
                .filter(Boolean).join("\n\n");
              if (await copyText(text)) notify("Script copied to clipboard");
              else notify("Copy failed: select the text manually", "err");
            }}>
              <Icon d={ICONS.copy} size={14} /> Copy script
            </button>
          </div>
          <p style={{ fontWeight: 600 }}>{p.script.hook}</p>
          <p style={{ color: "var(--color-muted)", margin: "var(--space-2) 0" }}>{p.script.body}</p>
          <p style={{ fontStyle: "italic", fontSize: "var(--text-base)" }}>CTA: {p.script.cta}</p>
        </section>
      )}

      {!!p.scenes.length && (
        <section style={{ marginTop: "var(--space-5)" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <h2 style={{ marginBottom: 4 }}>Storyboard · {p.scenes.length} scenes</h2>
            <button className="btn btn-secondary btn-sm" onClick={async () => {
              const text = p.scenes.map((s) =>
                `Scene ${s.scene_number} (${s.duration}s):\n${s.visual_prompt || ""}`
              ).join("\n\n");
              if (await copyText(text)) notify(`All ${p.scenes.length} scene prompts copied`);
              else notify("Copy failed: select the prompts manually", "err");
            }}>
              <Icon d={ICONS.copy} size={14} /> Copy all prompts
            </button>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6,
                            fontSize: "var(--text-sm)", color: "var(--color-muted)" }}>
              Music bed
              <select className="input" style={{ width: "auto", padding: "4px 8px" }}
                      aria-label="Music bed track"
                      value={musicTrack} onChange={(e) => pickMusic(e.target.value)}>
                <option value="">Auto (default)</option>
                {musicTracks.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </label>
          </div>
          <p className="hint" style={{ marginBottom: "var(--space-3)" }}>
            Copy each scene prompt into Gemini (Veo), download the clip, then
            upload it here. Imported clips replace AI-generated ones.
          </p>
          <div style={{ display: "grid", gap: "var(--space-3)" }}>
            {p.scenes.map((s) => (
              <div key={s.scene_number} className="card" style={{
                padding: "var(--space-4)", display: "flex", gap: "var(--space-4)",
                alignItems: "flex-start",
              }}>
                <span style={{
                  background: "var(--color-surface-2)", borderRadius: "var(--radius-sm)",
                  padding: "6px 10px", fontSize: "var(--text-xs)", fontWeight: 600,
                  fontVariantNumeric: "tabular-nums", color: "var(--color-muted)",
                  flexShrink: 0,
                }}>{s.duration}s</span>
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: "var(--text-sm)" }}>
                      Scene {s.scene_number}
                    </span>
                    {s.has_video && (
                      <span style={{ color: "var(--color-success-text)",
                                     fontSize: "var(--text-xs)", fontWeight: 600,
                                     display: "inline-flex", alignItems: "center", gap: 4 }}>
                        <Icon d={ICONS.check} size={13} /> video ready
                      </span>
                    )}
                  </div>
                  <p style={{ fontSize: "var(--text-base)" }}>{s.narration}</p>
                  <div style={{ display: "flex", alignItems: "center", gap: 8,
                                marginTop: 4, flexWrap: "wrap" }}>
                    <p style={{ color: "var(--color-muted)", fontSize: "var(--text-xs)",
                                margin: 0, flex: 1, minWidth: 200 }}>
                      {s.visual_prompt}
                    </p>
                    <button className="btn btn-secondary btn-sm"
                            onClick={() => copyPrompt(s.scene_number, s.visual_prompt)}>
                      <Icon d={ICONS.copy} size={14} /> {copied === s.scene_number ? "Copied!" : "Copy prompt"}
                    </button>
                  </div>
                  <div style={{ display: "flex", gap: "var(--space-2)",
                                marginTop: "var(--space-3)", alignItems: "center" }}>
                    <input ref={(el) => { fileInputs.current[s.scene_number] = el; }}
                           type="file" accept="video/mp4,video/webm,video/quicktime"
                           style={{ display: "none" }}
                           onChange={(e) => onFilePicked(s.scene_number, e.target.files?.[0])} />
                    <button className="btn btn-primary btn-sm" disabled={uploadingScene === s.scene_number}
                            onClick={() => fileInputs.current[s.scene_number]?.click()}>
                      <Icon d={ICONS.plus} size={14} />
                      {uploadingScene === s.scene_number ? "Uploading…"
                        : s.has_video ? "Replace video" : "Upload video"}
                    </button>
                    {s.has_video && (
                      <button className="btn btn-secondary btn-sm"
                              onClick={() => removeAsset(s.scene_number)}>
                        <Icon d={ICONS.x} size={14} /> Remove
                      </button>
                    )}
                  </div>
                  {s.has_video && (
                    <p style={{ color: "var(--color-muted)", fontSize: "var(--text-xs)",
                                margin: "var(--space-2) 0 0" }}>
                      Using <strong>{s.asset_name || `scene-${String(s.scene_number).padStart(2, "0")}.mp4`}</strong>: included when you click Rebuild video.
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {p.storyboard_only === 1 && p.status === "STORYBOARDED" && (() => {
        const allUploaded = p.scenes.length > 0 && p.scenes.every((s) => s.has_video);
        return (
          <div className="card" style={{ marginTop: "var(--space-4)", padding: "var(--space-4)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span style={{ marginRight: "auto", fontSize: "var(--text-base)" }}>
                {allUploaded
                  ? "All scene videos uploaded: ready to render!"
                  : `${p.scenes.filter((s) => s.has_video).length}/${p.scenes.length} scene videos uploaded`}
              </span>
              <button className="btn btn-primary" disabled={!allUploaded || running}
                      onClick={async () => {
                        try {
                          await post(`/projects/${p.id}/render-storyboard`);
                          notify("Render started: watch the status above");
                        } catch (e) { notify(`Render failed: ${String(e).slice(0, 120)}`, "err"); }
                      }}>
                <Icon d={ICONS.play} size={15} /> Render video
              </button>
            </div>
            {!allUploaded && (
              <p className="hint" style={{ marginTop: "var(--space-2)" }}>
                Upload a video for each scene above. The render button enables when all scenes have a video.
              </p>
            )}
          </div>
        );
      })()}

    </>
  );
}
