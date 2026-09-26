"use client";

/** App shell: sidebar navigation with Lucide SVG icons (no emoji). */
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import "./globals.css";
import { ToastProvider } from "./toast";

const NAV = [
  { href: "/", label: "Dashboard", icon: MIcon("M3 12l9-8 9 8M5 10v10a1 1 0 001 1h4v-6h4v6h4a1 1 0 001-1V10") },
  { href: "/storyboard", label: "Storyboard", icon: MIcon("M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z") },
  { href: "/gallery", label: "Gallery", icon: MIcon("M3 5a2 2 0 012-2h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5zm3 11l3.5-4.5L12 15l2.5-3L18 16M8.5 8.5h.01") },
  { href: "/analytics", label: "Analytics", icon: MIcon("M4 20V10m6 10V4m6 16v-7m4 7H2") },
  { href: "/system", label: "System", icon: MIcon("M12 3v3m0 12v3M5.6 5.6l2.1 2.1m8.6 8.6l2.1 2.1M3 12h3m12 0h3M5.6 18.4l2.1-2.1m8.6-8.6l2.1-2.1") },
  { href: "/settings", label: "Settings", icon: MIcon("M4 21v-7m0-4V3m8 18v-9m0-4V3m8 18v-5m0-4V3M1 14h6m2-6h6m2 8h6") },
];

function MIcon(d: string) {
  return function Icon() {
    return (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"
           aria-hidden="true">
        <path d={d} />
      </svg>
    );
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  const router = useRouter();
  // A desktop install runs with AVF_AUTH_ENABLED=0, where /login is unreachable
  // — offering Logout there leads to a dead page. Starts false so the button
  // never flashes in front of a desktop user.
  const [authOn, setAuthOn] = useState(false);
  // Analytics reads the YouTube Analytics API, so with no channel connected the
  // page can only ever show zeros. Both start false: an item that appears and
  // then vanishes reads as a glitch, one that arrives a beat late does not.
  const [ytOn, setYtOn] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const [quitting, setQuitting] = useState(false);
  const [dark, setDark] = useState(false);

  // Theme: read localStorage on mount, fall back to system preference.
  useEffect(() => {
    const stored = localStorage.getItem("theme");
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = stored ? stored === "dark" : prefersDark;
    setDark(isDark);
    document.documentElement.dataset.theme = isDark ? "dark" : "light";
  }, []);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.dataset.theme = next ? "dark" : "light";
    localStorage.setItem("theme", next ? "dark" : "light");
  }

  useEffect(() => {
    fetch("/api/health")
      .then((r) => r.json())
      .then((d) => { setAuthOn(Boolean(d.auth_required)); setDesktop(Boolean(d.desktop)); })
      .catch(() => {});
    fetch("/api/settings/youtube")
      .then((r) => r.json())
      .then((d) => setYtOn(Boolean(d.connected)))
      .catch(() => {});
  }, []);

  const nav = ytOn ? NAV : NAV.filter((n) => n.href !== "/analytics");

  // First-run gate: without an LLM key the pipeline only dies later, at
  // RESEARCHING, and the reason never reaches the DB. /settings is exempt —
  // it is where you go to fix a missing key, so bouncing people off it would
  // be a dead end. The stock key is enforced server-side instead (409 from
  // POST /api/produce): gating on it here would also bounce people who only
  // want to watch a video they already rendered.
  const gated = pathname !== "/setup" && pathname !== "/settings";
  useEffect(() => {
    if (!gated) return;
    let cancelled = false;
    fetch("/api/settings/llm")
      .then((r) => r.json())
      .then((d) => {
        if (!cancelled && !d.has_api_key) router.replace("/setup");
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [gated, router]);
  return (
    <html lang="en">
      <head>
        <title>AVF Console</title>
        <meta name="description" content="AI Video Factory dashboard and controls" />
      </head>
      <body>
        <ToastProvider>
          <div className="shell">
            {/* Hidden on /setup: the wizard is the only thing to do there, and
                every other link would leave a half-configured install. */}
            {pathname !== "/setup" && (
            <nav className="sidebar" aria-label="Main navigation">
            <div className="brand">
              {/* One flat accent, no gradient: the mark has to read at 16px, and
                  a two-stop ramp turns to mud at favicon size. */}
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <rect x="2" y="4" width="20" height="16" rx="4.5" fill="var(--color-accent)" />
                <path d="M10 8.5L16 12L10 15.5V8.5Z" fill="var(--color-on-accent)" />
              </svg>
              AVF Console
            </div>
            {nav.map((n) => (
              <Link key={n.href} href={n.href} className="navitem"
                    data-active={pathname === n.href}
                    aria-current={pathname === n.href ? "page" : undefined}>
                <n.icon />
                {n.label}
              </Link>
            ))}
            <div className="sidebar-foot">
            <button
              className="navitem navitem-btn"
              title={dark ? "Switch to light theme" : "Switch to dark theme"}
              onClick={toggleTheme}>
              {dark ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="5" />
                  <path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72l1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                     strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                  <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
                </svg>
              )}
              {dark ? "Light mode" : "Dark mode"}
            </button>
            {authOn && (
            <button
              className="navitem navitem-btn"
              title="End session"
              onClick={async () => {
                await fetch("/api/auth/logout", { method: "POST" });
                location.href = "/login";
              }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M10 3a1 1 0 011 1v7a1 1 0 01-2 0V5H5v14h4v-6a1 1 0 012 0v7a1 1 0 01-1 1H4a1 1 0 01-1-1V4a1 1 0 011-1h6zm7.7 5.3a1 1 0 010 1.4L15.4 12l2.3 2.3a1 1 0 01-1.4 1.4l-3-3a1 1 0 010-1.4l3-3a1 1 0 011.4 0zM14 11a1 1 0 010 2H9a1 1 0 010-2h5z" />
              </svg>
              Logout
            </button>
            )}
            {/* Desktop only: in a server install this would kill someone else's
                service, so the server refuses it unless AVF_DESKTOP=1. Coral
                because it is destructive — it cancels a render in progress. */}
            {desktop && (
            <button
              className="navitem navitem-btn navitem-danger"
              title="Close AVF and stop the local server"
              disabled={quitting}
              onClick={async () => {
                if (!confirm("Quit AVF? A render in progress will be cancelled.")) return;
                setQuitting(true);
                try {
                  await fetch("/api/quit", { method: "POST" });
                  // the server is gone, so nothing will answer; say so instead of
                  // leaving the user staring at a frozen window
                  document.body.innerHTML =
                    '<div style="display:grid;place-items:center;height:100vh;' +
                    'font:14px system-ui;color:var(--color-muted)">AVF has quit. You can close this window.</div>';
                } catch {
                  setQuitting(false);
                }
              }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
                <path d="M12 3v9m6.4-6.4a9 9 0 11-12.8 0" />
              </svg>
              Quit
            </button>
            )}
            </div>
          </nav>
            )}
          <main className="main">{children}</main>
          </div>
        </ToastProvider>
      </body>
    </html>
  );
}
