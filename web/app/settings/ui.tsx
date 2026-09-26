"use client";

/** Reusable settings building blocks: form field, section card, icons. */
import { ReactNode, useEffect, useRef, useState } from "react";
import { useToast } from "../toast";

export function SectionCard({
  id,
  title,
  description,
  children,
  action,
  dirty = false,
}: {
  id?: string;
  title: string;
  description?: string;
  children: ReactNode;
  action?: ReactNode;
  dirty?: boolean;
}) {
  return (
    <section id={id} className="card card-section">
      <div className="card-head">
        <div className="card-head-row">
          <h2>{title}</h2>
          {dirty && <span className="badge badge-dirty">Unsaved</span>}
        </div>
        {description && <p className="hint">{description}</p>}
      </div>
      {children}
      {action && <div className="card-action">{action}</div>}
    </section>
  );
}

export function Field({
  label,
  hint,
  children,
  error,
  group = false,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  error?: string;
  group?: boolean;
}) {
  /* A <label> wrapping the control, not a <div> with a <span> above it. Wrapping
     associates the label with the input implicitly, so no id has to be threaded
     through every call site — and clicking the label now focuses the field. The
     previous markup left ~20 inputs unnamed in the accessibility tree. */
  const body = (
    <>
      <span className="field-label">{label}</span>
      {children}
      {error ? <p className="hint hint-err">{error}</p> : null}
      {hint && !error ? <p className="hint">{hint}</p> : null}
    </>
  );
  if (!group) return <label className="field">{body}</label>;

  /* `group` for a field whose content is several controls rather than one. A
     label may hold at most one labelable descendant and clicking it activates
     that one, so the bed list — fifteen play buttons inside a label — fired its
     first button from anywhere in the field: clicking the lofi row played
     calm-drone. Same misfire on the stock checkboxes, the preset grid, and the
     voice/volume rows. A group is what these are, and role=group keeps the name
     announced. */
  return (
    <div className="field" role="group" aria-label={label}>
      {body}
    </div>
  );
}

/* One icon set, not two. This file used to carry its own Icon and ICONS, and the
   two maps had already drifted — neither was a superset of the other, so a glyph
   was only reachable from whichever file you happened to import. */
export { Icon, ICONS } from "../lib";

/** Fetch-then-play audio preview. Lives here, not in the settings page, because
 *  the wizard previews the same two endpoints.
 *
 *  Fetching first rather than handing the URL to <audio> is deliberate: a plain
 *  <audio src> that 404s fires an error event nothing was listening for, so the
 *  button looked dead and said nothing. Fetching turns the endpoint's own message
 *  ("no such track") into the toast, and the bytes are already in hand when
 *  play() runs, so a slow TTS render shows as "Loading…" rather than nothing. */
export function PreviewButton({ url, children, title, className = "btn" }: {
  url: string; children: ReactNode; title: string; className?: string;
}) {
  const { notify } = useToast();
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const blobRef = useRef<string | null>(null);

  useEffect(() => () => {
    audioRef.current?.pause();
    if (blobRef.current) URL.revokeObjectURL(blobRef.current);
  }, []);

  const stop = () => {
    audioRef.current?.pause();
    audioRef.current = null;
    setState("idle");
  };

  const toggle = async () => {
    if (state === "playing") { stop(); return; }
    if (state === "loading") return;
    setState("loading");
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error((await r.text()).slice(0, 160) || `HTTP ${r.status}`);
      if (blobRef.current) URL.revokeObjectURL(blobRef.current);
      blobRef.current = URL.createObjectURL(await r.blob());
      const a = new Audio(blobRef.current);
      a.addEventListener("ended", () => setState("idle"));
      await a.play();
      audioRef.current = a;
      setState("playing");
    } catch (e: any) {
      setState("idle");
      notify(`Preview failed: ${String(e?.message || e)}`, "err");
    }
  };

  return (
    <button className={className} type="button" title={title}
            onClick={toggle} disabled={state === "loading"}>
      {state === "loading" ? "Loading…" : state === "playing" ? "Stop" : children}
    </button>
  );
}

/** Voices bucketed by their language heading, order preserved from the server
 *  (which groups by language already), so a 41-item list reads as 7 short ones.
 *
 *  Array.from, not a spread: the build target is below es2015, where spreading a
 *  Map iterator is a type error. */
export function groupVoices(voices: { id: string; group: string; label: string }[]) {
  const byGroup = new Map<string, typeof voices>();
  for (const v of voices) {
    const list = byGroup.get(v.group);
    if (list) list.push(v); else byGroup.set(v.group, [v]);
  }
  return Array.from(byGroup.entries());
}
