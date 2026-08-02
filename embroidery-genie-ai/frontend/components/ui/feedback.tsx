"use client";

import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from "lucide-react";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/15 text-primary",
        secondary: "border-transparent bg-secondary text-secondary-foreground",
        outline: "border-border text-muted-foreground",
        success: "border-transparent bg-[hsl(var(--success)/0.15)] text-[hsl(var(--success))]",
        warning: "border-transparent bg-[hsl(var(--warning)/0.15)] text-[hsl(var(--warning))]",
        destructive: "border-transparent bg-destructive/15 text-destructive",
        accent: "border-transparent bg-accent/15 text-accent",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export function Progress({
  value,
  className,
  tone = "primary",
}: {
  value: number;
  className?: string;
  tone?: "primary" | "success" | "warning" | "destructive";
}) {
  const tones = {
    primary: "bg-primary",
    success: "bg-[hsl(var(--success))]",
    warning: "bg-[hsl(var(--warning))]",
    destructive: "bg-destructive",
  } as const;
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-secondary", className)}>
      <div
        className={cn("h-full rounded-full transition-[width] duration-500", tones[tone])}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cn("relative overflow-hidden rounded-lg bg-secondary/60", className)}>
      <div className="absolute inset-0 -translate-x-full animate-shimmer bg-gradient-to-r from-transparent via-white/5 to-transparent" />
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-muted-foreground", className)} />;
}

const ALERT_ICONS = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
} as const;

export function Alert({
  level = "info",
  title,
  children,
  className,
  action,
}: {
  level?: keyof typeof ALERT_ICONS;
  title?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
  action?: React.ReactNode;
}) {
  const Icon = ALERT_ICONS[level];
  const styles = {
    info: "border-border bg-secondary/40 text-foreground",
    success: "border-[hsl(var(--success)/0.35)] bg-[hsl(var(--success)/0.1)]",
    warning: "border-[hsl(var(--warning)/0.35)] bg-[hsl(var(--warning)/0.1)]",
    error: "border-destructive/35 bg-destructive/10",
  } as const;
  const iconStyles = {
    info: "text-muted-foreground",
    success: "text-[hsl(var(--success))]",
    warning: "text-[hsl(var(--warning))]",
    error: "text-destructive",
  } as const;

  return (
    <div className={cn("flex gap-3 rounded-lg border p-3 text-sm", styles[level], className)}>
      <Icon className={cn("mt-0.5 size-4 shrink-0", iconStyles[level])} aria-hidden />
      <div className="min-w-0 flex-1">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? <div className="text-muted-foreground [&_p]:mt-1">{children}</div> : null}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-xl border border-dashed border-border/70 px-6 py-14 text-center",
        className,
      )}
    >
      {icon ? <div className="mb-3 text-muted-foreground">{icon}</div> : null}
      <p className="text-sm font-medium">{title}</p>
      {description ? (
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}

/** Maps engine issue levels onto the alert component. */
export function IssueList({ issues }: { issues: { level: string; code: string; message: string }[] }) {
  if (!issues?.length) return null;
  const level = (value: string) =>
    value === "error" ? "error" : value === "warning" ? "warning" : "info";
  return (
    <div className="space-y-2">
      {issues.map((issue, index) => (
        <Alert key={`${issue.code}-${index}`} level={level(issue.level) as "error" | "warning" | "info"}>
          {issue.message}
        </Alert>
      ))}
    </div>
  );
}
