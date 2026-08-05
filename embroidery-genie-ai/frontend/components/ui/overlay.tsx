"use client";

import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, X, XCircle } from "lucide-react";
import * as React from "react";
import { createPortal } from "react-dom";

import { cn } from "@/lib/utils";

// --------------------------------------------------------------------- dialog
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  footer?: React.ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    // Prevent the page behind the dialog from scrolling under the overlay.
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [open, onClose]);

  if (!mounted) return null;

  const widths = { sm: "max-w-sm", md: "max-w-lg", lg: "max-w-2xl", xl: "max-w-4xl" } as const;

  return createPortal(
    <AnimatePresence>
      {open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 bg-black/70 backdrop-blur-sm"
          />
          <motion.div
            role="dialog"
            aria-modal="true"
            initial={{ opacity: 0, scale: 0.96, y: 12 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: 8 }}
            transition={{ type: "spring", stiffness: 320, damping: 28 }}
            className={cn(
              "relative z-10 w-full overflow-hidden rounded-xl border border-border bg-card shadow-2xl",
              widths[size],
            )}
          >
            {title ? (
              <div className="flex items-start justify-between gap-4 border-b border-border/70 p-5">
                <div className="min-w-0">
                  <h2 className="text-base font-semibold">{title}</h2>
                  {description ? (
                    <p className="mt-1 text-sm text-muted-foreground">{description}</p>
                  ) : null}
                </div>
                <button
                  onClick={onClose}
                  aria-label="Close"
                  className="rounded-md p-1 text-muted-foreground hover:bg-secondary hover:text-foreground"
                >
                  <X className="size-4" />
                </button>
              </div>
            ) : null}
            <div className="scrollbar-thin max-h-[70vh] overflow-y-auto p-5">{children}</div>
            {footer ? (
              <div className="flex items-center justify-end gap-2 border-t border-border/70 bg-secondary/20 p-4">
                {footer}
              </div>
            ) : null}
          </motion.div>
        </div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}

// ---------------------------------------------------------------------- toast
type Toast = { id: number; title: string; description?: string; tone: "success" | "error" | "info" };

const ToastContext = React.createContext<{
  toast: (toast: Omit<Toast, "id">) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
} | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = React.useState<Toast[]>([]);

  const push = React.useCallback((toast: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { ...toast, id }]);
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 6000);
  }, []);

  const value = React.useMemo(
    () => ({
      toast: push,
      success: (title: string, description?: string) => push({ title, description, tone: "success" }),
      error: (title: string, description?: string) => push({ title, description, tone: "error" }),
    }),
    [push],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2">
        <AnimatePresence>
          {toasts.map((toast) => (
            <motion.div
              key={toast.id}
              layout
              initial={{ opacity: 0, x: 40, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.96 }}
              className={cn(
                "pointer-events-auto flex gap-3 rounded-lg border p-3 shadow-xl backdrop-blur-md",
                toast.tone === "error"
                  ? "border-destructive/40 bg-destructive/15"
                  : toast.tone === "success"
                    ? "border-[hsl(var(--success)/0.4)] bg-[hsl(var(--success)/0.12)]"
                    : "border-border bg-card/95",
              )}
            >
              {toast.tone === "error" ? (
                <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
              ) : toast.tone === "success" ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-[hsl(var(--success))]" />
              ) : null}
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{toast.title}</p>
                {toast.description ? (
                  <p className="mt-0.5 text-xs text-muted-foreground">{toast.description}</p>
                ) : null}
              </div>
              <button
                onClick={() => setToasts((current) => current.filter((t) => t.id !== toast.id))}
                className="text-muted-foreground hover:text-foreground"
                aria-label="Dismiss"
              >
                <X className="size-3.5" />
              </button>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = React.useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside <ToastProvider>");
  return context;
}
