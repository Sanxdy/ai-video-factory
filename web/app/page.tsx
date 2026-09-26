"use client";

/** Dashboard: project list, live SSE updates, produce modal with topic. */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, post, Project, statusBadge, Icon, ICONS, useSSE, Title } from "./lib";

const TERMINAL = new Set(["COMPLETE", "FAILED", "CANCELLED"]);

export default function Dashboard() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [topic, setTopic] = useState("");
  // one format for the whole batch, by design: a landscape render is ~6x
  // realtime, so mixing formats in one batch just delays the Shorts behind it
  const [ratio, setRatio] = useState("9:16");
  const [minutes, setMinutes] = useState(6);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [q, setQ] = useState("");
  const [fstatus, setFstatus] = useState("all");
  const [fkind, setFkind] = useState("all");
  const [sort, setSort] = useState("new");
  const [showArchived, setShowArchived] = useState(false);
  const [queue, setQueue] = useState<{ running: number[]; pending: number[] } | null>(null);
  const [page, setPage] = useState(1);

  const [analyticsRows, setAnalyticsRows] = useState<Project[] | null>(null);
  // "Finish setup" exists in the empty state because the layout gate catches a
  // missing AI-model key and nothing else — a missing stock key sails through
  // to here. So its visibility follows the same test the wizard uses to pick
  // its resume step and the produce guard enforces server-side: an AI-model key
  // AND a stock source with a key. It is not "no projects yet" — a fresh
  // install that has finished setup also has no projects, and the button then
  // sat there forever.
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);

  const load = useCallback(() => {
    api<Project[]>(`/projects${showArchived ? "?include_archived=1" : ""}`)
      .then(setProjects).catch(() => setProjects([]));
    api<{ running: number[]; pending: number[] }>("/queue")
      .then(setQueue).catch(() => {});
    api<Project[]>("/analytics")
      .then(setAnalyticsRows).catch(() => {});
  }, [showArchived]);
  useEffect(load, [load]);
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api<{ has_api_key: boolean }>("/settings/llm"),
      api<{ has_pexels_key: boolean; has_pixabay_key: boolean }>("/settings/image"),
    ]).then(([l, im]) => {
      if (!cancelled) setNeedsSetup(!l.has_api_key || !(im.has_pexels_key || im.has_pixabay_key));
    }).catch(() => { if (!cancelled) setNeedsSetup(false); });
    return () => { cancelled = true; };
  }, []);

  const onEvent = useCallback((e: MessageEvent) => {
    const d = JSON.parse(e.data);
    // every stage transition carries project_id → table stays live
    if (d.project != null || d.project_id != null || d.event === "analytics_synced") load();
    if (d.event === "pipeline_error") setErr(`Project ${d.project} failed: ${d.error}`);
  }, [load]);
  useSSE(onEvent);

  async function create() {
    setBusy(true); setErr("");
    const topics = topic.split("\n").map((t) => t.trim()).filter(Boolean);
    const fmt = { aspect_ratio: ratio, target_minutes: minutes };
    try {
      if (topics.length > 1) {
        await post("/produce", { topics: topics.slice(0, 20), ...fmt });
        setShowNew(false); setTopic("");
      } else {
        await post("/produce", topics.length ? { topic: topics[0], ...fmt } : fmt);
        setShowNew(false); setTopic("");
      }
      setTimeout(load, 800);
    } catch (e) { setErr(String(e)); }
    finally { setBusy(false); }
  }

  async function cancel(pid: number) {
    setErr("");
    try { await post(`/projects/${pid}/cancel`); load(); }
    catch (e) { setErr(String(e)); }
  }

  // rows whose pipeline can still be stopped (APPROVAL is a human gate, not running)
  const cancellable = projects?.filter(
    (p) => !TERMINAL.has(p.status) && p.status !== "APPROVAL") ?? [];

  async function cancelAll() {
    if (cancellable.length === 0 ||
        !confirm(`Cancel ${cancellable.length} running project${cancellable.length > 1 ? "s" : ""}?`))
      return;
    setErr("");
    for (const p of cancellable) {
      try { await post(`/projects/${p.id}/cancel`); } catch (e) { setErr(String(e)); }
    }
    load();
  }

  function toggle(pid: number, on: boolean) {
    setSel((s) => {
      const n = new Set(s);
      if (on) n.add(pid); else n.delete(pid);
      return n;
    });
  }

  async function deleteSelected() {
    const ids = Array.from(sel);
    // irreversible: removes DB rows and all rendered files
    const label = (pid: number) =>
      `"${projects?.find((p) => p.id === pid)?.topic || `Project ${pid}`}"`;
    const msg = ids.length === 1
      ? `Delete ${label(ids[0])} permanently? Its video files are removed too.`
      : `Delete ${ids.length} projects permanently? Their videos and files are removed too.`;
    if (!confirm(msg)) return;
    setBusy(true); setErr("");
    let failed = 0;
    for (const id of ids) {
      try { await api(`/projects/${id}`, { method: "DELETE" }); }
      catch { failed += 1; }
    }
    setSel(new Set()); setBusy(false); load();
    if (failed) setErr(`${failed} project${failed > 1 ? "s" : ""} could not be deleted (running?). Cancel them first.`);
  }

  const pending = projects?.filter((p) => p.status === "APPROVAL").length ?? 0;
  const batchCount = topic.split("\n").map((t) => t.trim()).filter(Boolean).length;
  const selCount = sel.size;

  const STATUSES = ["all", "IDEA", "APPROVAL", "STORYBOARDED", "EDITING", "STORYBOARDING",
                    "RENDERED", "FAILED", "CANCELLED", "COMPLETE"];
  const visible = (projects ?? []).filter((p) => {
    if (fstatus !== "all" && p.status !== fstatus) return false;
    // "16:9" = regular long-form, anything else = Short
    if (fkind === "regular" && p.aspect_ratio !== "16:9") return false;
    if (fkind === "short" && p.aspect_ratio === "16:9") return false;
    if (q && !(p.topic || "").toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });
  if (sort === "views") visible.sort((a, b) => (b.views ?? 0) - (a.views ?? 0));
  else if (sort === "topic")
    visible.sort((a, b) => (a.topic || "").localeCompare(b.topic || ""));

  const PAGE_SIZE = 20;
  const pageCount = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const cur = Math.min(page, pageCount);
  const paged = visible.slice((cur - 1) * PAGE_SIZE, cur * PAGE_SIZE);
  useEffect(() => setPage(1), [q, fstatus, fkind, sort, showArchived]);

  async function archiveSelected(archived: boolean) {
    const ids = Array.from(sel);
    setBusy(true); setErr("");
    let failed = 0;
    for (const id of ids) {
      try { await post(`/projects/${id}/archive`, { archived }); }
      catch { failed += 1; }
    }
    setSel(new Set()); setBusy(false); load();
    if (failed) setErr(`${failed} could not be updated`);
  }

  const totalViews = analyticsRows ? analyticsRows.reduce((a, r) => a + (r.views ?? 0), 0) : (projects ?? []).reduce((acc, p) => acc + (p.views ?? 0), 0);
  const activeCount = (projects ?? []).filter((p) => !TERMINAL.has(p.status) && p.status !== "APPROVAL").length;
  const completeCount = (projects ?? []).filter((p) => p.status === "COMPLETE" || p.status === "RENDERED").length;

  return (
    <>
      <Title>{"Dashboard | AVF Console"}</Title>
      <div className="pagehead">
        <div>
          <h1>Dashboard</h1>
          <p>
            {projects ? `${projects.length} projects` : "Loading…"}
            {pending > 0 && <> · <strong style={{ color: "var(--color-warning-text)" }}>
              {pending} awaiting approval</strong></>}
            {queue && queue.running.length > 0 && <> · producing
              project {queue.running.join(", ")}</>}
            {queue && queue.pending.length > 0 && <> · {queue.pending.length} queued</>}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {cancellable.length > 0 && (
            <button className="btn btn-secondary" disabled={busy}
                    onClick={cancelAll}>Cancel all ({cancellable.length})</button>
          )}
          <button className="btn btn-primary" onClick={() => setShowNew(true)}>
            <Icon d={ICONS.plus} size={16} /> New video
          </button>
        </div>
      </div>

      <div className="statgrid" style={{ marginBottom: "var(--space-6)" }}>
        <div className="stat-card">
          <span className="label">Total Videos</span>
          <span className="value">{projects ? projects.length : "-"}</span>
        </div>
        <div className="stat-card">
          <span className="label">Active Pipeline</span>
          <span className="value" style={{ color: activeCount > 0 ? "var(--color-accent-text)" : "inherit" }}>
            {projects ? activeCount : "-"}
          </span>
        </div>
        <div className="stat-card">
          <span className="label">Pending Approval</span>
          <span className="value" style={{ color: pending > 0 ? "var(--color-warning-text)" : "inherit" }}>
            {projects ? pending : "-"}
          </span>
        </div>
        <div className="stat-card">
          <span className="label">Total Views</span>
          <span className="value" style={{ color: "var(--color-success-text)" }}>
            {projects ? totalViews.toLocaleString() : "-"}
          </span>
        </div>
      </div>

      {err && (
        <p className="msg msg-err" role="alert" style={{ marginBottom: "var(--space-4)" }}>
          {err}
          <button className="banner-x" onClick={() => setErr("")} aria-label="Dismiss">×</button>
        </p>
      )}

      <div className="toolbar">
        <div className="segmented-control" role="tablist" aria-label="Filter projects by status">
          {STATUSES.map((s) => (
            <button key={s} className="segmented-btn" role="tab"
                    data-active={fstatus === s}
                    aria-selected={fstatus === s}
                    onClick={() => setFstatus(s)}>
              {s === "all" ? "All" : s}
            </button>
          ))}
        </div>

        <div className="toolbar-filters">
          <input className="input input-search" type="search" placeholder="Search topic…"
                 aria-label="Search projects by topic"
                 value={q}
                 onChange={(e) => setQ(e.target.value)} />
          <select className="select select-kind" aria-label="Filter by video type" value={fkind}
                  onChange={(e) => setFkind(e.target.value)}>
            <option value="all">All video types</option>
            <option value="short">Short (9:16)</option>
            <option value="regular">Regular (16:9)</option>
          </select>
          <select className="select select-sort" aria-label="Sort projects" value={sort}
                  onChange={(e) => setSort(e.target.value)}>
            <option value="new">Newest first</option>
            <option value="views">Most views</option>
            <option value="topic">Topic A–Z</option>
          </select>
          <label className="check">
            <input type="checkbox" checked={showArchived}
                   onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived
          </label>
        </div>
      </div>

      {selCount > 0 && (
        <div className="card selbar">
          <strong>{selCount} selected</strong>
          <button className="btn btn-danger btn-sm" disabled={busy} onClick={deleteSelected}>
            <Icon d={ICONS.x} size={14} /> Delete selected
          </button>
          <button className="btn btn-secondary btn-sm" disabled={busy}
                  onClick={() => archiveSelected(true)}>Archive selected</button>
          <button className="btn btn-secondary btn-sm" disabled={busy}
                  onClick={() => archiveSelected(false)}>Unarchive selected</button>
          <button className="btn btn-secondary btn-sm" onClick={() => setSel(new Set())}>
            Clear selection
          </button>
        </div>
      )}

      <div className="card table-card">
        {!projects ? (
          <div className="table-pad">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="skeleton skeleton-row" />
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="empty-card">
            <Icon d={ICONS.film} size={44} />
            <h3 className="empty-title">No video projects found</h3>
            <p className="empty-sub">
              {q || fstatus !== "all" ? "Try adjusting your search or status filter." : "Start producing your first AI-generated short video."}
            </p>
            <div className="formrow" style={{ justifyContent: "center" }}>
              <button className="btn btn-primary btn-sm" onClick={() => setShowNew(true)}>
                <Icon d={ICONS.plus} size={14} /> Produce New Video
              </button>
              {/* Hidden once the key exists — setup is done, and a button that
                  outlives its own wizard is just noise. */}
              {needsSetup && (
                <Link className="btn btn-secondary btn-sm" href="/setup">Finish setup</Link>
              )}
            </div>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th className="col-check">
                  {/* Scoped to the page, and the name says so: the previous label
                      ("Select all projects") promised the whole set while only
                      ticking the 20 visible rows, and it replaced the selection
                      rather than extending it, silently dropping any picks made
                      on another page. */}
                  <input type="checkbox" aria-label="Select all on this page"
                         checked={paged.length > 0 && paged.every((p) => sel.has(p.id))}
                         onChange={(e) => setSel((s) => {
                           const n = new Set(s);
                           for (const p of paged) {
                             if (e.target.checked) n.add(p.id); else n.delete(p.id);
                           }
                           return n;
                         })} />
                </th>
                <th>Project</th><th>Status</th><th className="num">Views</th>
                <th>Type</th><th></th>
              </tr>
            </thead>
            <tbody>
              {paged.map((p) => {
                const b = statusBadge(p.status);
                return (
                  <tr key={p.id}>
                    <td>
                      <input type="checkbox" aria-label={`Select ${p.topic || `Project ${p.id}`}`}
                             checked={sel.has(p.id)}
                             onChange={(e) => toggle(p.id, e.target.checked)} />
                    </td>
                    <td>
                      <Link href={`/project/${p.id}`} className="link-strong">
                        {p.topic || `Project ${p.id}`}
                      </Link>
                      <div style={{ color: "var(--color-muted)", fontSize: "var(--text-xs)", display: "flex", alignItems: "center", gap: 6 }}>
                        #{p.id}
                        {(p as any).storyboard_only === 1 && (p as any).aspect_ratio && (
                          <span className="badge badge-neutral">
                            {(p as any).aspect_ratio === "16:9" ? "16:9" : "9:16"}
                          </span>
                        )}
                      </div>
                    </td>
                    <td><span className={`badge ${b.cls}`}>{b.label}</span></td>
                    <td className="num">{(p.views ?? 0).toLocaleString()}</td>
                    <td>
                      {p.aspect_ratio === "16:9" ? (
                        <span className="badge badge-neutral">Regular</span>
                      ) : (
                        <span className="badge badge-neutral">Short</span>
                      )}
                    </td>
                    <td style={{ whiteSpace: "nowrap", textAlign: "right" }}>
                      {!TERMINAL.has(p.status) && p.status !== "APPROVAL" && (
                        <button className="btn btn-danger btn-sm" onClick={() => cancel(p.id)}>
                          Cancel
                        </button>
                      )}
                      {p.youtube_id && (
                        <a href={`https://youtube.com/watch?v=${p.youtube_id}`}
                           target="_blank" rel="noreferrer" aria-label="Watch on YouTube"
                           title="Watch on YouTube" style={{ marginLeft: 10, marginRight: 10 }}>
                          <Icon d={ICONS.yt} />
                        </a>
                      )}
                      <button className="btn btn-secondary btn-sm"
                              aria-label={`Delete ${p.topic || `Project ${p.id}`}`}
                              disabled={busy}
                              style={{ marginLeft: 10 }}
                              onClick={() => {
                                // irreversible: DB rows + video files gone
                                if (!confirm(`Delete "${p.topic || `Project ${p.id}`}" permanently? Its video files are removed too.`))
                                  return;
                                api(`/projects/${p.id}`, { method: "DELETE" })
                                  .then(load)
                                  .catch((e) => setErr(String(e)));
                              }}>
                        Delete
                      </button>
                      <button className="btn btn-secondary btn-sm"
                              disabled={busy} style={{ marginLeft: 6 }}
                              aria-label={`${p.archived ? "Unarchive" : "Archive"} ${p.topic || `Project ${p.id}`}`}
                              onClick={() =>
                                post(`/projects/${p.id}/archive`, { archived: !p.archived })
                                  .then(load)
                                  .catch((e) => setErr(String(e)))}>
                        {p.archived ? "Unarchive" : "Archive"}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {pageCount > 1 && (
          <div style={{ display: "flex", alignItems: "center", gap: 12,
                        justifyContent: "flex-end", marginTop: "var(--space-4)" }}>
            <span style={{ color: "var(--color-muted)", fontSize: "var(--text-sm)" }}>
              Page {cur} of {pageCount} · {visible.length} projects
            </span>
            <button className="btn btn-secondary btn-sm" disabled={cur <= 1}
                    aria-label="Previous page"
                    onClick={() => setPage(cur - 1)}>Prev</button>
            <button className="btn btn-secondary btn-sm" disabled={cur >= pageCount}
                    aria-label="Next page"
                    onClick={() => setPage(cur + 1)}>Next</button>
          </div>
        )}
      </div>

      {showNew && (
        <div className="overlay" onClick={() => !busy && setShowNew(false)}>
          <div className="modal card" role="dialog" aria-modal="true"
               aria-label="New video" onClick={(e) => e.stopPropagation()}>
            <h2>New video</h2>
            <div className="field" style={{ marginTop: "var(--space-3)" }}>
              <span className="field-label" id="new-format-label">Format</span>
              <div className="fmt-pick" role="radiogroup" aria-labelledby="new-format-label">
                {([["9:16", "📱", "Portrait 9:16", "YouTube Shorts"],
                   ["16:9", "🖥️", "Landscape 16:9", "Regular video"]] as const).map(
                  ([val, glyph, title, desc]) => (
                    <button key={val} type="button" role="radio"
                            aria-checked={ratio === val}
                            data-on={ratio === val ? "1" : "0"}
                            onClick={() => setRatio(val)}>
                      <span className="glyph" aria-hidden="true">{glyph}</span>
                      <span className="t">{title}</span>
                      <span className="d">{desc}</span>
                    </button>
                  ))}
              </div>
              <p className="hint">
                {ratio === "16:9"
                  ? "Landscape 1920×1080 · published as a regular YouTube video (description links are clickable)."
                  : "Vertical 1080×1920 · published as a Short with the #Shorts tag."}
                {" "}One format per batch.
              </p>
            </div>

            {ratio === "16:9" && (
              <>
                <div className="field">
                  <span className="field-label">Target length (minutes)</span>
                  <input className="input" type="number" min={5} max={20} step={0.5}
                         aria-label="Target length in minutes"
                         value={minutes}
                         onChange={(e) => setMinutes(Number(e.target.value) || 6)} />
                  <p className="hint">Minimum 5 minutes — enforced twice: the script
                    is budgeted for it, and the quality gate rejects any landscape
                    render under 5:00.</p>
                </div>
                <p className="msg msg-warn" role="note">
                  <Icon d={ICONS.warning} size={15} />
                  <span>Landscape renders take roughly 6× realtime — about 20–35
                    minutes for a 6-minute video, and the format applies to every
                    topic in the batch.</span>
                </p>
              </>
            )}

            <div className="field" style={{ marginTop: "var(--space-3)" }}>
              <span className="field-label">What should it be about?</span>
              <textarea className="input" autoFocus rows={4}
                        aria-label="Topic — one per line"
                        placeholder={ratio === "16:9"
                          ? "e.g. 5 Tempat Paling Ekstrem di Bumi"
                          : "e.g. Why the sky is blue"}
                        value={topic} onChange={(e) => setTopic(e.target.value)} />
              <p className="hint">One topic per line = batch queue, produced one at a
                time (max {ratio === "16:9" ? 2 : 20}). Leave empty and the AI picks a
                topic from your niche.</p>
            </div>
            <div className="formrow" style={{ justifyContent: "flex-end", marginTop: "var(--space-2)" }}>
              <button className="btn btn-secondary" disabled={busy}
                      onClick={() => setShowNew(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={create} disabled={busy}>
                <Icon d={ICONS.play} size={15} /> {busy ? "Starting…"
                  : batchCount > 1 ? `Queue ${Math.min(batchCount, 20)} videos` : "Start producing"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
