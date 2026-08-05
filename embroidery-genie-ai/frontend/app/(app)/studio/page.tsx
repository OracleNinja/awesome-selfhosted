"use client";

import { motion } from "framer-motion";
import { Palette, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, EmptyState, Skeleton } from "@/components/ui/feedback";
import { Input, Select } from "@/components/ui/form";
import { api, type Design } from "@/lib/api";
import { formatMm, formatNumber, humanize, relativeTime } from "@/lib/utils";

const STATUS_VARIANT: Record<string, "default" | "secondary" | "success" | "warning" | "destructive"> = {
  draft: "secondary",
  analyzing: "secondary",
  vectorized: "default",
  digitizing: "warning",
  ready: "success",
  failed: "destructive",
  archived: "secondary",
};

export default function StudioPage() {
  const [designs, setDesigns] = React.useState<Design[] | null>(null);
  const [query, setQuery] = React.useState("");
  const [status, setStatus] = React.useState("");

  React.useEffect(() => {
    const timer = setTimeout(() => {
      const params: Record<string, string | number> = { limit: 60 };
      if (query.trim()) params.q = query.trim();
      if (status) params.status = status;
      api
        .designs(params)
        .then((data) => setDesigns(data.items))
        .catch(() => setDesigns([]));
    }, 250);
    return () => clearTimeout(timer);
  }, [query, status]);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Design Studio</h1>
          <p className="text-sm text-muted-foreground">
            Every design in this workspace, with its stitch data and pre-flight status.
          </p>
        </div>
        <Link href="/digitizer">
          <Button>
            <Sparkles />
            New design
          </Button>
        </Link>
      </div>

      <div className="flex flex-wrap gap-2">
        <div className="relative min-w-56 flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search designs"
            className="pl-9"
          />
        </div>
        <Select
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          className="w-44"
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          {["draft", "vectorized", "digitizing", "ready", "failed", "archived"].map((value) => (
            <option key={value} value={value}>
              {humanize(value)}
            </option>
          ))}
        </Select>
      </div>

      {designs === null ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-44" />
          ))}
        </div>
      ) : designs.length === 0 ? (
        <EmptyState
          icon={<Palette className="size-6" />}
          title={query || status ? "No designs match that filter" : "No designs yet"}
          description={
            query || status
              ? "Try a different search or clear the status filter."
              : "Upload a logo, an image or an SVG and the digitizer will turn it into a stitch file."
          }
          action={
            <Link href="/digitizer">
              <Button size="sm">Create a design</Button>
            </Link>
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {designs.map((design, index) => (
            <motion.div
              key={design.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(index * 0.03, 0.3) }}
            >
              <Link href={`/studio/${design.id}`}>
                <Card className="group h-full overflow-hidden transition-colors hover:border-primary/40">
                  <div className="relative grid h-32 place-items-center overflow-hidden border-b border-border/60 bg-secondary/30 grid-paper">
                    <DesignThumb design={design} />
                    <Badge
                      variant={STATUS_VARIANT[design.status] ?? "secondary"}
                      className="absolute right-2 top-2"
                    >
                      {humanize(design.status)}
                    </Badge>
                  </div>
                  <CardContent className="space-y-2 p-4">
                    <p className="truncate text-sm font-medium">{design.name}</p>
                    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                      {design.stitch_count ? (
                        <>
                          <span>{formatNumber(design.stitch_count)} stitches</span>
                          <span>·</span>
                          <span>{design.color_count} colours</span>
                          <span>·</span>
                          <span>
                            {formatMm(design.width_mm)} × {formatMm(design.height_mm)}
                          </span>
                        </>
                      ) : (
                        <span>Not digitized yet</span>
                      )}
                    </div>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs text-muted-foreground">
                        {relativeTime(design.created_at)}
                      </span>
                      {design.thread_chart?.length ? (
                        <span className="flex -space-x-1">
                          {design.thread_chart.slice(0, 6).map((color) => (
                            <span
                              key={color.index}
                              title={`${color.name} ${color.code}`}
                              className="size-4 rounded-full border border-background"
                              style={{ backgroundColor: color.hex }}
                            />
                          ))}
                        </span>
                      ) : null}
                    </div>
                  </CardContent>
                </Card>
              </Link>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  );
}

function DesignThumb({ design }: { design: Design }) {
  const preview =
    design.files.find((file) => file.kind === "preview" && file.content_type === "image/png") ??
    design.files.find((file) => file.kind === "thumbnail") ??
    design.files.find((file) => file.kind === "original");

  if (!preview) {
    return <Palette className="size-8 text-muted-foreground/50" />;
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={preview.url}
      alt=""
      className="h-full w-full object-contain p-3 transition-transform duration-300 group-hover:scale-105"
      loading="lazy"
    />
  );
}
