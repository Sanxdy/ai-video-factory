"use client";

/** Lightweight toast notification system: no external deps. */
import { createContext, useContext, useState, useCallback, ReactNode } from "react";

type ToastType = "ok" | "err" | "info";

interface Toast {
  id: string;
  text: string;
  type: ToastType;
}

interface ToastCtx {
  notify: (text: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastCtx | null>(null);

let toastId = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const notify = useCallback((text: string, type: ToastType = "info") => {
    const id = `toast-${++toastId}`;
    setToasts((prev) => [...prev, { id, text, type }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const remove = (id: string) => setToasts((prev) => prev.filter((t) => t.id !== id));

  return (
    <ToastContext.Provider value={{ notify }}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            /* `msg` carries the shared shape (padding, radius, overflow-wrap);
               the variant only adds the tint. Without it a long unbroken token
               ran straight out of the box, since maxWidth alone does not wrap. */
            className={`msg ${t.type === "ok" ? "msg-ok" : t.type === "err" ? "msg-err" : "msg-info"}`}
          >
            <span className="toast-text">{t.text}</span>
            <button className="icon-btn toast-x" onClick={() => remove(t.id)} aria-label="Dismiss">
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}
