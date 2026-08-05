"use client";

import { motion } from "framer-motion";
import * as React from "react";

import { cn } from "@/lib/utils";

export function Tabs({
  tabs,
  value,
  onValueChange,
  className,
  size = "default",
}: {
  tabs: { value: string; label: React.ReactNode; badge?: React.ReactNode }[];
  value: string;
  onValueChange: (value: string) => void;
  className?: string;
  size?: "sm" | "default";
}) {
  const layoutId = React.useId();

  return (
    <div
      role="tablist"
      className={cn(
        "inline-flex items-center gap-1 rounded-lg border border-border/70 bg-secondary/40 p-1",
        className,
      )}
    >
      {tabs.map((tab) => {
        const active = tab.value === value;
        return (
          <button
            key={tab.value}
            role="tab"
            aria-selected={active}
            onClick={() => onValueChange(tab.value)}
            className={cn(
              "relative rounded-md font-medium transition-colors",
              size === "sm" ? "px-2.5 py-1 text-xs" : "px-3 py-1.5 text-sm",
              active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {active ? (
              <motion.span
                layoutId={layoutId}
                className="absolute inset-0 rounded-md bg-card shadow-sm"
                transition={{ type: "spring", stiffness: 380, damping: 30 }}
              />
            ) : null}
            <span className="relative flex items-center gap-1.5">
              {tab.label}
              {tab.badge}
            </span>
          </button>
        );
      })}
    </div>
  );
}
