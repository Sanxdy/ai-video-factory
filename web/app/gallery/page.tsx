"use client";

/** Gallery: every project with a rendered final.mp4, click to play inline + bulk/individual delete. */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, del, post, Project, statusBadge, Icon, ICONS , Title} from "../lib";

export default function Gallery() {
  const [vids, setVids] = useState<Project[] | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [deleting, setDeleting] = useState<boolean>(false);

  const load = useCallback(() => {
    api<Project[]>("/projects")
      .then((all) => setVids(all.filter((p) => p.has_video)))
      .catch(() => setVids([]));
  }, []);

  useEffect(load, [load]);

  const toggleSelect = (id: number) => {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  const toggleSelectAll = () => {
    if (!vids) return;
    if (selected.length === vids.length) {
      setSelected([]);
    } else {
      setSelected(vids.map((v) => v.id));
    }
  };

  const handleDeleteOne = async (id: number, topic: string | null) => {
    const name = topic || `Project ${id}`;
    if (!window.confirm(`Delete the rendered video for "${name}" and free its disk space?\n\nThe project stays on the Dashboard: only the local video file is removed.`)) return;

    setDeleting(true);
    try {
      await del(`/projects/${id}/video`);
      setSelected((prev) => prev.filter((i) => i !== id));
      load();
    } catch (err: any) {
      alert(`Failed to delete video for project ${id}: ${err.message || err}`);
    } finally {
      setDeleting(false);
    }
  };

  const handleDeleteBulk = async () => {
    if (selected.length === 0) return;
    if (
      !window.confirm(
        `Delete ${selected.length} rendered video(s) and free disk space?\n\nThe projects stay on the Dashboard: only the local video files are removed.`
      )
    )
      return;

    setDeleting(true);
    try {
      const res = await post<{ ok: boolean; deleted: number; freed_bytes: number }>(
        "/projects/bulk-delete-videos",
        { ids: selected }
      );
      const freedMb = (res.freed_bytes / (1024 * 1024)).toFixed(1);
      alert(`Removed ${res.deleted} video(s). Freed ${freedMb} MB. Projects kept on the Dashboard.`);
      setSelected([]);
      load();
    } catch (err: any) {
      alert(`Failed to delete selected videos: ${err.message || err}`);
    } finally {
      setDeleting(false);
    }
  };

  const allSelected = vids && vids.length > 0 && selected.length === vids.length;

  return (
    <>
      <Title>{"Gallery | AVF Console"}</Title>
      <div className="pagehead" style={{ position: "sticky", top: 0, zIndex: 100, background: "var(--color-background)", paddingTop: "var(--space-4)", paddingBottom: "var(--space-4)" }}>
        <div>
          <h1>Gallery</h1>
          <p>{vids ? `${vids.length} rendered video${vids.length === 1 ? "" : "s"}` : "Loading…"}</p>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          {vids && vids.length > 0 && (
            <button className="btn btn-secondary" onClick={toggleSelectAll} disabled={deleting}>
              <Icon d={allSelected ? ICONS.x : ICONS.check} size={16} />
              {allSelected ? "Deselect All" : "Select All"}
            </button>
          )}

          {selected.length > 0 && (
            <button
              className="btn btn-danger"
              onClick={handleDeleteBulk}
              disabled={deleting}
            >
              <Icon d={ICONS.trash} size={16} />
              Delete Selected ({selected.length})
            </button>
          )}

          <button className="btn btn-secondary" onClick={load} disabled={deleting}>
            <Icon d={ICONS.refresh} size={16} /> Refresh
          </button>
        </div>
      </div>

      {vids && vids.length === 0 && (
        <div className="empty">
          <Icon d={ICONS.film} size={40} />
          <p>No finished videos yet. Produce one from the Dashboard.</p>
        </div>
      )}

      <div className="gallery">
        {(vids ?? []).map((p) => {
          const src = `/api/video/${p.id}${p.video_v ? `?v=${p.video_v}` : ""}`;
          const b = statusBadge(p.status);
          const isSelected = selected.includes(p.id);

          return (
            <figure
              className="card gallery-item"
              key={p.id}
              data-selected={isSelected ? "1" : undefined}
            >
              <div className="gal-pick">
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => toggleSelect(p.id)}
                  aria-label={`Select ${p.topic || `Project ${p.id}`}`}
                />
              </div>

              {/* preload=metadata shows the first frame as a free poster */}
              <video
                src={src}
                controls
                preload="metadata"
                playsInline
                aria-label={`Play ${p.topic || `Project ${p.id}`}`}
              />

              <figcaption>
                <Link href={`/project/${p.id}`} className="link-strong topic">
                  {p.topic || `Project ${p.id}`}
                </Link>
                <span className="gal-actions">
                  <span className={`badge ${b.cls}`}>{b.label}</span>
                  {p.youtube_id && (
                    <a
                      href={`https://youtube.com/watch?v=${p.youtube_id}`}
                      target="_blank"
                      rel="noreferrer"
                      aria-label="Watch on YouTube"
                      title="Watch on YouTube"
                    >
                      <Icon d={ICONS.yt} />
                    </a>
                  )}
                  {/* An icon button, but a real one: the previous version had no
                      hover rule at all and sat at a permanent 0.8 opacity, so it
                      read as disabled next to the working buttons elsewhere. */}
                  <button
                    className="icon-btn icon-btn-danger"
                    onClick={() => handleDeleteOne(p.id, p.topic)}
                    disabled={deleting}
                    title="Delete video"
                    aria-label={`Delete ${p.topic || `Project ${p.id}`}`}
                  >
                    <Icon d={ICONS.trash} size={16} />
                  </button>
                </span>
              </figcaption>
            </figure>
          );
        })}
      </div>
    </>
  );
}
