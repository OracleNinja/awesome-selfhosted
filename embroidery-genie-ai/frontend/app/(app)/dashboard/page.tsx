"use client";

import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowUpRight,
  Boxes,
  Clock,
  DollarSign,
  Package,
  Palette,
  Sparkles,
  Users,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, StatCard } from "@/components/ui/card";
import { Badge, EmptyState, Skeleton } from "@/components/ui/feedback";
import { api, ApiError, type Dashboard } from "@/lib/api";
import { formatDate, formatMoney, formatNumber, humanize, relativeTime } from "@/lib/utils";

const STATUS_TONE: Record<string, "default" | "secondary" | "success" | "warning" | "accent"> = {
  quote: "secondary",
  approved: "default",
  digitizing: "accent",
  sewing: "warning",
  completed: "success",
  delivered: "success",
};

export default function DashboardPage() {
  const [data, setData] = React.useState<Dashboard | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    api
      .dashboard()
      .then(setData)
      .catch((exception) =>
        setError(exception instanceof ApiError ? exception.message : "Could not load the dashboard."),
      );
  }, []);

  if (error) {
    return (
      <EmptyState
        icon={<AlertTriangle className="size-6" />}
        title="Dashboard unavailable"
        description={error}
      />
    );
  }

  if (!data) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-72" />
      </div>
    );
  }

  const currency = data.finance.currency;
  const production = data.subscription.plan.production_module;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            {data.designs.this_month} design{data.designs.this_month === 1 ? "" : "s"} created this
            month · {formatNumber(data.designs.total_stitches)} stitches produced all time
          </p>
        </div>
        <Link href="/digitizer">
          <Button>
            <Sparkles />
            Create new embroidery design
          </Button>
        </Link>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Designs"
          value={formatNumber(data.designs.total)}
          hint={`${data.designs.ready} ready to sew`}
          icon={<Palette className="size-4" />}
        />
        <StatCard
          label="Open orders"
          value={formatNumber(data.orders.open)}
          hint={
            data.orders.overdue
              ? `${data.orders.overdue} overdue`
              : production
                ? "All on schedule"
                : "Business plan feature"
          }
          tone={data.orders.overdue ? "warning" : "default"}
          icon={<Boxes className="size-4" />}
        />
        <StatCard
          label="Revenue this month"
          value={formatMoney(data.finance.revenue_month, currency)}
          hint={`${data.finance.margin_pct}% margin · ${formatMoney(
            data.finance.profit_month,
            currency,
          )} profit`}
          tone="success"
          icon={<DollarSign className="size-4" />}
        />
        <StatCard
          label="Outstanding"
          value={formatMoney(data.finance.outstanding_invoices, currency)}
          hint={`${data.customers.total} customers`}
          tone={data.finance.outstanding_invoices > 0 ? "warning" : "default"}
          icon={<Users className="size-4" />}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {/* ------------------------------------------------------ recent work */}
        <Card className="lg:col-span-2">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Recent designs</CardTitle>
            <Link href="/studio" className="text-xs text-muted-foreground hover:text-foreground">
              View all
            </Link>
          </CardHeader>
          <CardContent>
            {data.designs.recent.length === 0 ? (
              <EmptyState
                icon={<Palette className="size-6" />}
                title="No designs yet"
                description="Upload a logo, an image or an SVG and the digitizer will turn it into a stitch file."
                action={
                  <Link href="/digitizer">
                    <Button size="sm">Create your first design</Button>
                  </Link>
                }
              />
            ) : (
              <ul className="divide-y divide-border/60">
                {data.designs.recent.map((design, index) => (
                  <motion.li
                    key={design.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: index * 0.04 }}
                  >
                    <Link
                      href={`/studio/${design.id}`}
                      className="flex items-center gap-3 py-2.5 transition-colors hover:bg-secondary/30"
                    >
                      <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-secondary/70">
                        <Palette className="size-4 text-muted-foreground" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium">{design.name}</span>
                        <span className="block text-xs text-muted-foreground">
                          {design.stitch_count
                            ? `${formatNumber(design.stitch_count)} stitches · ${design.color_count} colours`
                            : "Not digitized yet"}
                          {" · "}
                          {relativeTime(design.created_at)}
                        </span>
                      </span>
                      {design.compatibility_score !== null ? (
                        <Badge
                          variant={
                            design.compatibility_score >= 85
                              ? "success"
                              : design.compatibility_score >= 60
                                ? "warning"
                                : "destructive"
                          }
                        >
                          {design.compatibility_score}%
                        </Badge>
                      ) : null}
                      <Badge variant="outline">{humanize(design.status)}</Badge>
                      <ArrowUpRight className="size-4 shrink-0 text-muted-foreground" />
                    </Link>
                  </motion.li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* ----------------------------------------------------- production */}
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Production queue</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {!production ? (
                <p className="text-sm text-muted-foreground">
                  Orders, inventory and invoicing are part of the Business plan.{" "}
                  <Link href="/settings?tab=billing" className="text-primary hover:underline">
                    Compare plans
                  </Link>
                </p>
              ) : Object.keys(data.orders.by_status).length === 0 ? (
                <p className="text-sm text-muted-foreground">No orders yet.</p>
              ) : (
                Object.entries(data.orders.by_status).map(([status, count]) => (
                  <div key={status} className="flex items-center justify-between text-sm">
                    <Badge variant={STATUS_TONE[status] ?? "secondary"}>{humanize(status)}</Badge>
                    <span className="tabular-nums text-muted-foreground">{count}</span>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          {data.orders.due_soon.length ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Clock className="size-4" />
                  Due in the next 7 days
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                {data.orders.due_soon.map((order) => (
                  <Link
                    key={order.id}
                    href="/orders"
                    className="flex items-center justify-between gap-2 rounded-lg border border-border/60 p-2 text-sm hover:bg-secondary/40"
                  >
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{order.number}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {order.customer ?? "No customer"} · {formatDate(order.due_date)}
                      </span>
                    </span>
                    <span className="shrink-0 tabular-nums text-xs">
                      {formatMoney(order.total, currency)}
                    </span>
                  </Link>
                ))}
              </CardContent>
            </Card>
          ) : null}

          {data.inventory.low_stock > 0 ? (
            <Card className="border-[hsl(var(--warning)/0.4)]">
              <CardContent className="flex items-center gap-3 p-4">
                <Package className="size-5 text-[hsl(var(--warning))]" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">
                    {data.inventory.low_stock} blank{data.inventory.low_stock === 1 ? "" : "s"} at or
                    below reorder level
                  </p>
                  <Link href="/inventory" className="text-xs text-primary hover:underline">
                    Review inventory
                  </Link>
                </div>
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
