/** Shared client helpers: API base + live SSE hook + UI primitives. */
import { useEffect, useState } from "react";

export const API = process.env.NEXT_PUBLIC_API || "";

export type Project = {
  id: number;
  status: string;
  topic: string | null;
  youtube_id: string | null;
  views: number;
  likes: number;
  comments: number;
  has_video: boolean;
  video_v?: number | null;
  archived?: number | boolean | null;
  /** "16:9" = regular long-form, "9:16" = Short */
  aspect_ratio?: string | null;
  storyboard_only?: number | null;
};

export type SystemInfo = {
  ram_total_mb: number;
  ram_free_mb: number;
  disk_free_gb: number;
  models: { name: string; task?: string; status?: string; available: boolean }[];
};

export function useSSE(onEvent: (e: MessageEvent) => void) {
  useEffect(() => {
    const es = new EventSource(`${API}/api/events`);
    es.onmessage = onEvent;
    return () => es.close();
  }, [onEvent]);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`${API}/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export async function post<T = any>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
}

/** Multipart file upload: no Content-Type header (browser sets boundary). */
export async function upload<T = any>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch(`${API}/api${path}`, { method: "POST", body: fd });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export async function del<T = any>(path: string): Promise<T> {
  const r = await fetch(`${API}/api${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

/** Status → badge class + human label. */
export function statusBadge(status: string): { cls: string; label: string } {
  if (status === "COMPLETE") return { cls: "badge-complete", label: "Complete" };
  if (status === "APPROVAL") return { cls: "badge-approval", label: "Awaiting approval" };
  if (status === "FAILED") return { cls: "badge-failed", label: "Failed" };
  if (status === "IDEA") return { cls: "badge-idea", label: "Idea" };
  if (status === "CANCELLED") return { cls: "badge-cancelled", label: "Cancelled" };
  return { cls: "badge-working", label: status.charAt(0) + status.slice(1).toLowerCase() };
}

export function Icon({ d, size = 18 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
         strokeLinejoin="round" aria-hidden="true" style={{ flexShrink: 0 }}>
      <path d={d} />
    </svg>
  );
}

export const ICONS = {
  plus: "M12 5v14M5 12h14",
  check: "M20 6L9 17l-5-5",
  x: "M18 6L6 18M6 6l12 12",
  pause: "M6 4h4v16H6V4zm8 0h4v16h-4V4z",
  chevronDown: "M6 9l6 6 6-6",
  chevronRight: "M9 18l6-6-6-6",
  music: "M9 18V6l9-3v12",
  refresh: "M21 12a9 9 0 11-2.6-6.4M21 3v6h-6",
  copy: "M8 8h12v12H8zM4 16V4h12",
  download: "M12 3v12m0 0l-5-5m5 5l5-5M4 21h16",
  play: "M7 4l13 8-13 8V4z",
  yt: "M22.5 8.5a2.8 2.8 0 00-2-2C18.9 6 12 6 12 6s-6.9 0-8.5.5a2.8 2.8 0 00-2 2A29 29 0 001 12a29 29 0 00.5 3.5 2.8 2.8 0 002 2C5.1 18 12 18 12 18s6.9 0 8.5-.5a2.8 2.8 0 002-2A29 29 0 0023 12a29 29 0 00-.5-3.5zM10 15V9l5.2 3L10 15z",
  film: "M4 4h16a1 1 0 011 1v14a1 1 0 01-1 1H4a1 1 0 01-1-1V5a1 1 0 011-1zm4 0v16m8-16v16M3 9h5m8 0h5M3 15h5m8 0h5",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7zm10 3a3 3 0 100-6 3 3 0 000 6z",
  chip: "M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 5h10a2 2 0 012 2v10a2 2 0 01-2 2H7a2 2 0 01-2-2V7a2 2 0 012-2zm3 4h4v6h-4V9z",
  heart: "M12 21C7 17 2 13.5 2 8.8 2 5.6 4.5 3.5 7.2 3.5c1.9 0 3.7 1 4.8 2.7 1.1-1.7 2.9-2.7 4.8-2.7 2.7 0 5.2 2.1 5.2 5.3 0 4.7-5 8.2-10 12.2z",
  chat: "M21 12a8 8 0 01-8 8H4l2.5-2.5A8 8 0 1121 12z",
  trash: "M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16",
  warning: "M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z",
};

export function Title({ children }: { children: string }) {
  useEffect(() => { document.title = children; }, [children]);
  return null;
}
