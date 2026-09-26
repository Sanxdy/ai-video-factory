"use client";

/** Analytics dashboard: per-video stats + sync button + sticky header + pagination. */
import { useCallback, useEffect, useState } from "react";
import { api, post, Project, Icon, ICONS, useSSE , Title} from "../lib";
import { useToast } from "../toast";

const PAGE_SIZE_OPTIONS = [10, 15, 25, 50];

export default function AnalyticsPage() {
  const [rows, setRows] = useState<Project[] | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(15);
  const { notify } = useToast();

  const load = useCallback(() => {
    api<Project[]>("/analytics").then(setRows).catch(() => setRows([]));
  }, []);
  useEffect(load, [load]);

  const onEvent = useCallback((e: MessageEvent) => {
    if (JSON.parse(e.data).event === "analytics_synced") load();
  }, []);
  useSSE(onEvent);

  /* Sync is a network round-trip against YouTube that can fail on quota, on an
     expired token, or on no connection at all. The previous version had no catch
     and no message: a failure surfaced as an unhandled rejection in the console
     and nothing at all in the UI, so the button looked broken either way. */
  async function sync() {
    setSyncing(true);
    try {
      await post("/analytics/sync");
      notify("Sync started — figures update as YouTube responds.", "ok");
      load();
    } catch (e) {
      notify(`Sync failed: ${String(e)}`, "err");
    } finally {
      setSyncing(false);
    }
  }

  const totalViews = rows?.reduce((a, r) => a + (r.views ?? 0), 0) ?? 0;
  const totalLikes = rows?.reduce((a, r) => a + (r.likes ?? 0), 0) ?? 0;
  const totalComments = rows?.reduce((a, r) => a + (r.comments ?? 0), 0) ?? 0;

  const totalItems = rows?.length ?? 0;
  const totalPages = Math.ceil(totalItems / pageSize) || 1;
  const safePage = Math.min(page, totalPages);
  const startIndex = (safePage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, totalItems);
  const paginatedRows = rows?.slice(startIndex, endIndex) ?? [];

  return (
    <>
      <Title>{"Analytics | AVF Console"}</Title>
      {/* Only the title row sticks. The stat grid used to be inside the sticky
          block too, which pinned ~190px of cards over the table permanently. */}
      <div className="pagehead sticky-head">
        <div>
          <h1>Analytics</h1>
          <p>Performance across all published videos</p>
        </div>
        <button className="btn btn-secondary" onClick={sync} disabled={syncing}>
          <Icon d={ICONS.refresh} size={15} />
          {syncing ? "Syncing…" : "Sync now"}
        </button>
      </div>

      <div className="statgrid" aria-live="polite">
        <div className="card stat">
          <div className="label">Total views</div>
          <div className="value">{totalViews.toLocaleString()}</div>
        </div>
        <div className="card stat">
          <div className="label">Likes</div>
          <div className="value">{totalLikes.toLocaleString()}</div>
        </div>
        <div className="card stat">
          <div className="label">Comments</div>
          <div className="value">{totalComments.toLocaleString()}</div>
        </div>
        <div className="card stat">
          <div className="label">Published</div>
          <div className="value">{totalItems}</div>
        </div>
      </div>

      {/* Video Table */}
      <div className="card table-card">
        {!rows ? (
          <div className="table-pad">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="skeleton skeleton-row" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="empty">
            <Icon d={ICONS.eye} size={40} />
            <p>No published videos yet.</p>
          </div>
        ) : (
          <>
            <table className="table">
              <thead>
                <tr>
                  <th>Video</th>
                  <th className="num">Views</th>
                  <th className="num">Likes</th>
                  <th className="num">Comments</th>
                </tr>
              </thead>
              <tbody>
                {paginatedRows.map((r, idx) => (
                  <tr key={r.youtube_id || r.id || idx}>
                    <td>
                      {/* Guarded: without a youtube_id this rendered a link to
                          ?v=undefined that opened a broken YouTube page. The
                          gallery and the dashboard already guard it. */}
                      {r.youtube_id ? (
                        <a
                          href={`https://youtube.com/watch?v=${r.youtube_id}`}
                          target="_blank"
                          rel="noreferrer"
                          className="link-strong"
                          style={{ display: "inline-flex", alignItems: "center", gap: 8 }}
                        >
                          <Icon d={ICONS.yt} size={16} />
                          {r.topic || (r.id ? `Project ${r.id}` : "Untitled")}
                        </a>
                      ) : (
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                          {r.topic || (r.id ? `Project ${r.id}` : "Untitled")}
                        </span>
                      )}
                    </td>
                    <td className="num">{(r.views ?? 0).toLocaleString()}</td>
                    <td className="num">{(r.likes ?? 0).toLocaleString()}</td>
                    <td className="num">{(r.comments ?? 0).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            {/* Pagination Bar */}
            <div className="pager">
              <div>
                Showing {totalItems === 0 ? 0 : startIndex + 1}–{endIndex} of {totalItems} videos
              </div>

              <div className="pager-controls">
                <label className="pager-size">
                  Per page:
                  <select className="select select-tiny" value={pageSize}
                          onChange={(e) => {
                            setPageSize(Number(e.target.value));
                            setPage(1);
                          }}>
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ))}
                  </select>
                </label>

                <div className="pager-nav">
                  <button className="btn btn-secondary btn-sm"
                          onClick={() => setPage((p) => Math.max(1, p - 1))}
                          disabled={safePage <= 1}>
                    Prev
                  </button>
                  <span>
                    {safePage} / {totalPages}
                  </span>
                  <button className="btn btn-secondary btn-sm"
                          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                          disabled={safePage >= totalPages}>
                    Next
                  </button>
                </div>
              </div>
            </div>
          </>
        )}
      </div>
    </>
  );
}
