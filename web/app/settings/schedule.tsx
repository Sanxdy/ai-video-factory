"use client";

/** Daily upload schedule editor: flexible multi-slot collapsible schedule. */
import { useEffect, useState } from "react";
import { api, del as apiDel, post } from "../lib";
import { useToast } from "../toast";
import { SectionCard, Icon, ICONS } from "./ui";

type DayEntry = { topic: string; playlist: string; music: string };
type Schedule = {
  id: string;
  enabled: boolean;
  time: string;
  videos_per_run: number;
  privacy: string;
  format: string;          // "9:16" (Shorts) | "16:9" (landscape long-form)
  target_minutes: number;
  days: Record<string, DayEntry>;
};

const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const DAY_LABELS: Record<string, string> = {
  mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday",
  fri: "Friday", sat: "Saturday", sun: "Sunday",
};

// landscape renders ~6x realtime on the 2-core VPS, so it cannot be batched
const MAX_PER_RUN: Record<string, number> = { "9:16": 20, "16:9": 2 };

function defaultSchedule(): Schedule {
  return {
    id: crypto.randomUUID(),
    enabled: false,
    time: "09:00",
    videos_per_run: 1,
    privacy: "public",
    format: "9:16",
    target_minutes: 6,
    days: Object.fromEntries(DAY_KEYS.map((d) => [d, { topic: "", playlist: "", music: "" }])),
  };
}

function ScheduleCard({ sched, isOpen, onToggle, onUpdate, onRemove, tracks }: {
  sched: Schedule;
  isOpen: boolean;
  onToggle: () => void;
  onUpdate: (s: Schedule) => void;
  onRemove: () => void;
  tracks: string[];
}) {
  const dayFields = (d: string) => {
    const label = DAY_LABELS[d] || d;
    const e = sched.days[d] || { topic: "", playlist: "", music: "" };
    return (
      <div key={d} className="formrow" style={{ marginBottom: 8 }}>
        <span className="hint" style={{ width: 90, flexShrink: 0 }}>{label}</span>
        <input className="input" placeholder={`Theme for ${label}`}
               value={e.topic || ""}
               onChange={(ev) => onUpdate({ ...sched,
                 days: { ...sched.days, [d]: { ...e, topic: ev.target.value } } })} />
        <input className="input" placeholder="Playlist"
               value={e.playlist || ""}
               onChange={(ev) => onUpdate({ ...sched,
                 days: { ...sched.days, [d]: { ...e, playlist: ev.target.value } } })} />
        <select className="input" style={{ maxWidth: 150 }} title={`Music bed for ${label}`}
                value={e.music || ""}
                onChange={(ev) => onUpdate({ ...sched,
                  days: { ...sched.days, [d]: { ...e, music: ev.target.value } } })}>
          <option value="">auto (by theme)</option>
          {tracks.map((t) => <option key={t} value={t}>{t.replace(/\.mp3$/, "")}</option>)}
        </select>
      </div>
    );
  };

  return (
    <div className="card sched-card">
      {/* The header used to be a div with onClick, so it was unreachable by
          keyboard and announced as plain text. Now it is a real button; the
          remove control is its sibling rather than a button nested inside one. */}
      <div className="sched-head">
        <button type="button" className="sched-toggle" aria-expanded={isOpen} onClick={onToggle}>
          <span className="sched-sum">
            {sched.time} : {sched.videos_per_run} video{sched.videos_per_run > 1 ? "s" : ""}
            <span className="sched-chip">
              {sched.format === "16:9"
                ? `LANDSCAPE 16:9${sched.target_minutes ? ` · ${sched.target_minutes} min` : ""}`
                : "PORTRAIT 9:16"}
            </span>
            {sched.enabled ? "" : " (disabled)"}
          </span>
          <span className="sched-chev" data-open={isOpen ? "1" : undefined}>
            <Icon d={ICONS.chevronDown} size={16} />
          </span>
        </button>
        <button type="button" className="icon-btn icon-btn-danger"
                onClick={onRemove} title="Remove schedule" aria-label="Remove schedule">
          <Icon d={ICONS.x} size={14} />
        </button>
      </div>

      {isOpen && (
        <div className="sched-body">
          <div className="formrow" style={{ marginBottom: 12 }}>
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={sched.enabled}
                     onChange={(e) => onUpdate({ ...sched, enabled: e.target.checked })} />
              Enabled
            </label>
            <div className="field" style={{ maxWidth: 130, marginTop: 8 }}>
              <span>Produce at</span>
              <input className="input" type="time" value={sched.time}
                     onChange={(e) => onUpdate({ ...sched, time: e.target.value })} />
            </div>
            <div className="field" style={{ maxWidth: 130, marginTop: 8 }}>
              <span>Videos per run</span>
              <input className="input" type="number" min={1} max={MAX_PER_RUN[sched.format] ?? 20}
                     value={sched.videos_per_run}
                     onChange={(e) => onUpdate({ ...sched,
                       videos_per_run: Math.min(Number(e.target.value) || 1,
                                                MAX_PER_RUN[sched.format] ?? 20) })} />
            </div>
            <div className="field" style={{ maxWidth: 130, marginTop: 8 }}>
              <span>Visibility</span>
              <select className="input" value={sched.privacy}
                      onChange={(e) => onUpdate({ ...sched, privacy: e.target.value })}>
                <option value="public">public</option>
                <option value="unlisted">unlisted</option>
                <option value="private">private</option>
              </select>
            </div>
          </div>
          <div className="formrow" style={{ marginBottom: 12 }}>
            <div className="field" style={{ maxWidth: 260, marginTop: 8 }}>
              <span>Format</span>
              <select className="input" value={sched.format}
                      aria-label="Video format"
                      onChange={(e) => {
                        const format = e.target.value;
                        // landscape cannot batch like a Short — clamp immediately
                        // so the saved value is never silently reduced later
                        const cap = MAX_PER_RUN[format] ?? 20;
                        onUpdate({ ...sched, format,
                                   videos_per_run: Math.min(sched.videos_per_run, cap) });
                      }}>
                <option value="9:16">Portrait 9:16 — YouTube Shorts</option>
                <option value="16:9">Landscape 16:9 — regular video</option>
              </select>
            </div>
            {sched.format === "16:9" && (
              <div className="field" style={{ maxWidth: 160, marginTop: 8 }}>
                <span>Target length (min)</span>
                <input className="input" type="number" min={5} max={20} step={0.5}
                       aria-label="Target length in minutes"
                       value={sched.target_minutes}
                       onChange={(e) => onUpdate({ ...sched,
                         target_minutes: Number(e.target.value) || 6 })} />
              </div>
            )}
          </div>
          <p className="hint" style={{ marginTop: -4, marginBottom: 12 }}>
            {sched.format === "16:9"
              ? "Landscape: 1920×1080, minimum 5 minutes, max 2 per run (~20–35 min render each)."
              : "Portrait: 1080×1920, published as a Short with the #Shorts tag."}
          </p>
          {DAY_KEYS.map(dayFields)}
        </div>
      )}
    </div>
  );
}

export default function DailySchedule() {
  const { notify } = useToast();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [tracks, setTracks] = useState<string[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  // Host fact, not schedule data: kept out of `schedules` so the save response
  // (which does not carry it) cannot blank it.
  const [cronOk, setCronOk] = useState(true);

  useEffect(() => {
    api<(Schedule & { cron_available?: boolean })[]>("/settings/schedules")
      .then((s) => { setSchedules(s); setCronOk(s[0]?.cron_available ?? true); })
      .catch(() => {});
    api<string[]>("/music").then(setTracks).catch(() => {});
  }, []);

  const updateSchedule = (updated: Schedule) => {
    setSchedules(schedules.map((s) => (s.id === updated.id ? updated : s)));
    setDirty(true);
  };

  const handleDelete = (id: string) => {
    if (!confirm("Remove this schedule?")) return;
    apiDel(`/settings/schedules/${id}`).then(() => {
      setSchedules(schedules.filter((s) => s.id !== id));
      notify("Schedule removed.", "ok");
    }).catch((e) => notify(String(e.message || e), "err"));
  };

  const handleAdd = () => {
    const newSched = defaultSchedule();
    setSchedules([...schedules, newSched]);
    setOpenId(newSched.id);
    setDirty(true);
  };

  const save = async () => {
    setBusy(true);
    try {
      const out = await post<Schedule[]>("/settings/schedules", schedules);
      setSchedules(out);
      setDirty(false);
      notify("All schedules saved.", "ok");
    } catch (e: any) {
      notify(String(e.message || e), "err");
    } finally {
      setBusy(false);
    }
  };

  const totalVideos = schedules.filter((s) => s.enabled).reduce((sum, s) => sum + s.videos_per_run, 0);
  const active = schedules.filter((s) => s.enabled);
  const longCount = active.filter((s) => s.format === "16:9")
    .reduce((sum, s) => sum + s.videos_per_run, 0);
  const shortCount = totalVideos - longCount;

  return (
    <SectionCard id="schedules" title="Daily upload schedules" description="One or more schedules. Each has a format: portrait 9:16 (YouTube Shorts, up to 20 per run) or landscape 16:9 (regular video, up to 2 per run)." dirty={dirty}>
      {!cronOk && (
        <div className="msg msg-warn" style={{ marginBottom: 12 }}>
          <span>
            <strong>Schedules are saved but will not run on this machine.</strong> AVF
            installs them with <code>crontab</code>, which Windows does not have. Use{" "}
            <code>avf produce</code> from a task you schedule yourself, or run AVF on
            macOS or Linux to use this feature.
          </span>
        </div>
      )}
      <div style={{ marginBottom: 12 }}>
        <button className="btn btn-secondary" onClick={handleAdd} disabled={busy}>
          <Icon d={ICONS.plus} size={14} /> Add schedule
        </button>
      </div>

      {schedules.length === 0 && (
        <p style={{ color: "var(--color-muted)" }}>No schedules configured yet. Click "Add schedule" to start.</p>
      )}

      {schedules.map((s) => (
        <ScheduleCard
          key={s.id}
          sched={s}
          isOpen={openId === s.id}
          onToggle={() => setOpenId(openId === s.id ? null : s.id)}
          onUpdate={updateSchedule}
          onRemove={() => handleDelete(s.id)}
          tracks={tracks}
        />
      ))}

      <div style={{ marginTop: 16, padding: "12px", background: "var(--color-surface-2)", borderRadius: 8 }}>
        <p style={{ margin: 0, fontSize: "var(--text-sm)", color: "var(--color-muted)" }}>
          Total videos/day: <strong>{totalVideos}</strong> ({active.length} active schedule{active.length !== 1 ? "s" : ""})
          {shortCount > 0 && <> · <span style={{ color: "var(--color-muted)" }}>{shortCount} portrait</span></>}
          {longCount > 0 && <> · <span style={{ color: "var(--color-muted)" }}>{longCount} landscape</span></>}
        </p>
        {longCount > 0 && (
          <p style={{ margin: "6px 0 0", fontSize: "var(--text-xs)", color: "var(--color-muted)" }}>
            Landscape renders take ~20–35 min each on this server, and run one at a time.
          </p>
        )}
      </div>

      <div className="formrow" style={{ marginTop: 16 }}>
        <button className="btn btn-primary" disabled={busy} onClick={save}>Save all schedules</button>
      </div>
    </SectionCard>
  );
}
