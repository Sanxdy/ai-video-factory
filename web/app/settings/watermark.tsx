"use client";

/**
 * Watermark settings: LIVE draggable preview.
 * The mark is rendered client-side (CSS position/scale/opacity), so every
 * drag/slider tick moves it instantly. Save persists to watermark.* settings;
 * every rendered video gets the same placement.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useToast } from "../toast";
import { SectionCard, Icon, ICONS } from "./ui";

type Wm = {
  enabled: boolean; x: number; y: number; scale: number; opacity: number;
  path: string; exists: boolean;
};

type WmResponse = { settings: Record<string, Wm>; defaults: Wm };

const RATIOS = ["9:16", "16:9"] as const;

/* Preview box in px, one per ratio. Has to match the frame the renderer crops
   to, because the drag math turns a box fraction into a frame fraction. */
const BOX = { "9:16": { w: 216, h: 384 }, "16:9": { w: 384, h: 216 } } as const;

export default function WatermarkSettings() {
  const { notify } = useToast();
  const [settings, setSettings] = useState<Record<string, Wm> | null>(null);
  const [activeRatio, setActiveRatio] = useState<string>("9:16");
  const [saving, setSaving] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [imgTick, setImgTick] = useState(0);
  const [ratio, setRatio] = useState(560 / 160);
  const [dirty, setDirty] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  /* Where inside the mark the pointer grabbed it, as a box fraction. Without
     this the mark re-centres under the cursor on pointer-down, so the first
     pixel of movement is a jump and short drags overshoot. */
  const grab = useRef({ dx: 0, dy: 0 });

  useEffect(() => {
    fetch("/api/settings/watermark").then((r) => r.json()).then((data: WmResponse) => {
      setSettings(data.settings);
    });
  }, []);

  const wm = settings?.[activeRatio];
  const box = BOX[activeRatio as keyof typeof BOX] ?? BOX["9:16"];

  const save = async () => {
    if (!wm) return;
    setSaving(true);
    try {
      await fetch("/api/settings/watermark", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ratio: activeRatio,
          enabled: wm.enabled, x: wm.x, y: wm.y,
          scale: wm.scale, opacity: wm.opacity,
        }),
      });
      setDirty(false);
      notify("Watermark saved.", "ok");
    } catch (e: any) {
      notify(String(e.message || e), "err");
    } finally {
      setSaving(false);
    }
  };

  /* The mark's rendered height as a fraction of the box. The <img> sets only
     width, so height follows the image's own aspect and must DIVIDE by ratio.
     Multiplying by it (what this did) made a 560x160 logo 12x too tall and
     clamped the drag to y=0.72, so the mark could never reach the bottom of
     the frame. */
  const hFrac = wm ? Math.min(1, (wm.scale * (box.w / box.h)) / ratio) : 0;

  const place = useCallback(
    (clientX: number, clientY: number, dx: number, dy: number) => {
      const el = boxRef.current;
      if (!el || !wm || !settings) return;
      const r = el.getBoundingClientRect();
      let x = (clientX - r.left) / r.width - dx;
      let y = (clientY - r.top) / r.height - dy;
      x = Math.min(Math.max(x, 0), 1 - wm.scale);
      y = Math.min(Math.max(y, 0), 1 - hFrac);
      setSettings({ ...settings, [activeRatio]: { ...wm, x: +x.toFixed(3), y: +y.toFixed(3) } });
      setDirty(true);
    },
    [wm, hFrac, settings, activeRatio],
  );

  const onPointerDown = (e: React.PointerEvent) => {
    const el = boxRef.current;
    if (!el || !wm || !settings) return;
    el.setPointerCapture?.(e.pointerId);
    const r = el.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width;
    const py = (e.clientY - r.top) / r.height;
    const onMark = px >= wm.x && px <= wm.x + wm.scale &&
                   py >= wm.y && py <= wm.y + hFrac;
    // Grabbing the mark keeps the grab point under the cursor; grabbing the
    // backdrop is a deliberate "put it here", so it centres on the cursor.
    grab.current = onMark ? { dx: px - wm.x, dy: py - wm.y }
                          : { dx: wm.scale / 2, dy: hFrac / 2 };
    setDragging(true);
    if (!onMark) place(e.clientX, e.clientY, grab.current.dx, grab.current.dy);
  };

  const updateWm = (patch: Partial<Wm>) => {
    if (!wm || !settings) return;
    setSettings({ ...settings, [activeRatio]: { ...wm, ...patch } });
    setDirty(true);
  };

  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!dragging) return;
      place(e.clientX, e.clientY, grab.current.dx, grab.current.dy);
    },
    [dragging, place],
  );

  if (!wm || !settings) return null;
  return (
    <SectionCard id="watermark" title="Watermark" description="Stamped on every published video. Drag the mark in the preview, then save." dirty={dirty}>
      <div className="wm-ratio">
        {RATIOS.map((r) => (
          <button
            key={r}
            onClick={() => setActiveRatio(r)}
            className={"btn btn-sm " + (activeRatio === r ? "btn-primary" : "btn-secondary")}
          >
            {r === "16:9" ? "16:9 Landscape" : "9:16 Shorts"}
          </button>
        ))}
      </div>

      <label style={{ display: "inline-flex", gap: 8, alignItems: "center", marginBottom: 12, cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={wm.enabled}
          onChange={(e) => { updateWm({ enabled: e.target.checked }); }}
        />
        Enable watermark overlay
        {!wm.exists && (
          <span style={{ color: "var(--color-danger-text)", fontSize: "var(--text-xs)" }}>
            <Icon d={ICONS.warning} size={12} /> no watermark image yet: upload one below
          </span>
        )}
      </label>

      <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
        <div
          ref={boxRef}
          onPointerDown={onPointerDown}
          onPointerUp={() => setDragging(false)}
          onPointerCancel={() => setDragging(false)}
          onPointerMove={onPointerMove}
          style={{
            position: "relative",
            /* Box sizes are the render frame's, but the preview has to fit the
               column: a fixed 384px box overflowed the settings pane on a narrow
               window. Everything downstream is a fraction of the rect, so a
               scaled-down box drags identically. */
            width: "100%",
            maxWidth: box.w,
            aspectRatio: `${box.w} / ${box.h}`,
            borderRadius: 10, overflow: "hidden",
            border: "1px solid var(--color-border)",
            cursor: dragging ? "grabbing" : "grab",
            touchAction: "none",
            background:
              "repeating-conic-gradient(var(--color-surface-3) 0% 25%, var(--color-surface-2) 0% 50%) 0 0 / 24px 24px",
          }}
        >
          {wm.enabled && (
            <img
              src={`/api/watermark/file?ratio=${activeRatio}&t=${imgTick}`}
              alt="watermark"
              draggable={false}
              onLoad={(e) => {
                const el = e.currentTarget;
                if (el.naturalWidth) setRatio(el.naturalWidth / el.naturalHeight);
              }}
              style={{
                position: "absolute",
                left: `${wm.x * 100}%`,
                top: `${wm.y * 100}%`,
                width: `${wm.scale * 100}%`,
                opacity: wm.opacity,
                pointerEvents: "none",
                filter: "drop-shadow(0 1px 3px rgba(0,0,0,.6))",
              }}
            />
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 220, flex: 1 }}>
          <label>
            Size <span className="hint">{Math.round(wm.scale * 100)}% of width</span>
            <input type="range" min={0.05} max={0.5} step={0.01} value={wm.scale}
                   onChange={(e) => updateWm({ scale: +e.target.value })} style={{ width: "100%" }} />
          </label>
          <label>
            Transparency <span className="hint">{Math.round(wm.opacity * 100)}% opaque</span>
            <input type="range" min={0.1} max={1} step={0.05} value={wm.opacity}
                   onChange={(e) => updateWm({ opacity: +e.target.value })} style={{ width: "100%" }} />
          </label>
          <label>
            Position X <span className="hint">{Math.round(wm.x * 100)}%</span>
            <input type="range" min={0} max={0.99} step={0.01} value={wm.x}
                   onChange={(e) => updateWm({ x: +e.target.value })} style={{ width: "100%" }} />
          </label>
          <label>
            Position Y <span className="hint">{Math.round(wm.y * 100)}%</span>
            <input type="range" min={0} max={0.99} step={0.01} value={wm.y}
                   onChange={(e) => updateWm({ y: +e.target.value })} style={{ width: "100%" }} />
          </label>

          <label className="hint" style={{ display: "block" }}>
            Your own watermark (.png/.webp, transparent background recommended):
            <input type="file" accept=".png,.webp" style={{ marginTop: 6, maxWidth: "100%" }}
                   onChange={async (e) => {
                     const f = e.target.files?.[0];
                     if (!f) return;
                     const fd = new FormData();
                     fd.append("file", f);
                     const r = await fetch("/api/watermark/upload", { method: "POST", body: fd });
                     if (r.ok) {
                       setImgTick((n) => n + 1);
                       updateWm({ exists: true });
                       notify("Watermark uploaded.", "ok");
                     } else {
                       notify((await r.json().catch(() => ({})))?.detail || "upload failed", "err");
                     }
                     e.target.value = "";
                   }} />
          </label>

          <button onClick={save} disabled={saving} className="btn btn-primary"
                  style={{ marginTop: 4, alignSelf: "flex-start" }}>
            {saving ? "Saving…" : "Save watermark"}
          </button>
        </div>
      </div>
    </SectionCard>
  );
}
