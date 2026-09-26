"use client";

/** System/diagnostics page: RAM, disk, model availability. */
import { useCallback, useEffect, useState } from "react";
import { api, SystemInfo, Icon, ICONS , Title} from "../lib";

export default function SystemPage() {
  const [s, setS] = useState<SystemInfo | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    setFailed(false);
    api<SystemInfo>("/system").then(setS).catch(() => setFailed(true));
  }, []);
  useEffect(load, [load]);

  if (failed) {
    return (
      <>
      <Title>{"System | AVF Console"}</Title>
        <div className="pagehead"><div><h1>System</h1></div></div>
        <div className="card">
          <p className="msg msg-err">Could not load system stats: is the server running?</p>
          <button className="btn btn-secondary" style={{ marginTop: "var(--space-3)" }}
                  onClick={load}>Retry</button>
        </div>
      </>
    );
  }

  if (!s) {
    return (
      <>
        <Title>{"System | AVF Console"}</Title>
        <div className="pagehead"><div><h1>System</h1></div></div>
        <div className="statgrid" style={{ marginBottom: "var(--space-5)" }}>
          {[...Array(2)].map((_, i) => (
            <div key={i} className="skeleton" style={{ height: 96 }} />
          ))}
        </div>
        <div className="skeleton" style={{ height: 300 }} />
      </>
    );
  }

  const ramPct = Math.round((1 - s.ram_free_mb / s.ram_total_mb) * 100);

  return (
    <>
      {/* The loaded branch had no <Title> at all, so arriving here from the
          dashboard left the tab reading "Dashboard | AVF Console". */}
      <Title>{"System | AVF Console"}</Title>
      <div className="pagehead">
        <div>
          <h1>System</h1>
          <p>Host resources and local model availability</p>
        </div>
      </div>

      <div className="statgrid" style={{ marginBottom: "var(--space-5)" }}>
        <div className="card stat">
          <div className="label">RAM free</div>
          <div className="value">{(s.ram_free_mb / 1024).toFixed(1)} GB</div>
          <div className="sub">
            <div className="meter" role="img"
                 aria-label={`${ramPct}% of memory in use`}>
              <div className="meter-fill" data-high={ramPct > 85 ? "1" : undefined}
                   style={{ width: `${ramPct}%` }} />
            </div>
            <span>{(s.ram_total_mb / 1024).toFixed(0)} GB total · {ramPct}% used</span>
          </div>
        </div>
        <div className="card stat">
          <div className="label">Disk free</div>
          <div className="value">{s.disk_free_gb} GB</div>
          <div className="sub">on content volume</div>
        </div>
      </div>

      <h2 className="section-title">Models</h2>
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr><th>Model</th><th>Task</th><th>Role</th><th>Status</th></tr>
          </thead>
          <tbody>
            {s.models.map((m) => (
              <tr key={m.name}>
                <td style={{ fontWeight: 500 }}>{m.name}</td>
                <td style={{ textTransform: "capitalize", color: "var(--color-muted)" }}>
                  {m.task ?? "-"}
                </td>
                <td style={{ textTransform: "capitalize", color: "var(--color-muted)" }}>
                  {m.status ?? "-"}
                </td>
                <td>
                  {m.available ? (
                    <span className="badge badge-complete">Available</span>
                  ) : (
                    <span className="badge badge-failed">Missing</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
