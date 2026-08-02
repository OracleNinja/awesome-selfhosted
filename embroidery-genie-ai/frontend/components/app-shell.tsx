"use client";

import { AnimatePresence, motion } from "framer-motion";
import {
  Boxes,
  LayoutDashboard,
  LogOut,
  Menu,
  Package,
  Palette,
  Settings,
  Sparkles,
  Users,
  Wand2,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as React from "react";

import { useSession } from "@/app/providers";
import { VoiceCommandButton } from "@/components/voice-command";
import { Button } from "@/components/ui/button";
import { Badge, Progress, Spinner } from "@/components/ui/feedback";
import { isSupabaseConfigured, signOut } from "@/lib/supabase";
import { cn, formatNumber } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/studio", label: "Design Studio", icon: Palette },
  { href: "/digitizer", label: "AI Digitizer", icon: Wand2 },
  { href: "/orders", label: "Orders", icon: Boxes },
  { href: "/customers", label: "Customers", icon: Users },
  { href: "/inventory", label: "Inventory", icon: Package },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { me, loading, error } = useSession();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => setMobileOpen(false), [pathname]);

  const subscription = me?.subscription;
  const usagePercent =
    subscription && subscription.designs_limit
      ? (subscription.designs_used / subscription.designs_limit) * 100
      : 0;

  const sidebar = (
    <div className="flex h-full flex-col gap-1">
      <Link href="/dashboard" className="mb-4 flex items-center gap-2.5 px-2">
        <span className="grid size-9 place-items-center rounded-xl bg-gradient-to-br from-primary to-accent shadow-lg shadow-primary/25">
          <Sparkles className="size-5 text-white" />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold leading-tight">Embroidery Genie</span>
          <span className="block text-[11px] leading-tight text-muted-foreground">
            {me?.organization.name ?? "AI digitizing"}
          </span>
        </span>
      </Link>

      <nav className="flex-1 space-y-0.5">
        {NAV.map((item) => {
          const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                active
                  ? "text-foreground"
                  : "text-muted-foreground hover:bg-secondary/50 hover:text-foreground",
              )}
            >
              {active ? (
                <motion.span
                  layoutId="nav-active"
                  className="absolute inset-0 rounded-lg border border-primary/25 bg-primary/12"
                  transition={{ type: "spring", stiffness: 380, damping: 32 }}
                />
              ) : null}
              <item.icon className="relative size-4 shrink-0" />
              <span className="relative truncate font-medium">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      {subscription ? (
        <div className="mt-4 rounded-lg border border-border/70 bg-secondary/30 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium">{subscription.plan.name} plan</span>
            <Badge variant={subscription.tier === "free" ? "outline" : "default"}>
              {subscription.tier}
            </Badge>
          </div>
          {subscription.designs_limit ? (
            <>
              <Progress
                className="mt-2"
                value={usagePercent}
                tone={usagePercent > 85 ? "warning" : "primary"}
              />
              <p className="mt-1.5 text-[11px] text-muted-foreground">
                {formatNumber(subscription.designs_used)} of{" "}
                {formatNumber(subscription.designs_limit)} designs this month
              </p>
            </>
          ) : (
            <p className="mt-1.5 text-[11px] text-muted-foreground">
              Unlimited designs · {formatNumber(subscription.designs_used)} used
            </p>
          )}
          {subscription.tier === "free" ? (
            <Link href="/settings?tab=billing">
              <Button size="sm" className="mt-2.5 w-full">
                Upgrade
              </Button>
            </Link>
          ) : null}
        </div>
      ) : null}

      {isSupabaseConfigured ? (
        <button
          onClick={async () => {
            await signOut();
            window.location.href = "/login";
          }}
          className="mt-2 flex items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted-foreground transition-colors hover:bg-secondary/50 hover:text-foreground"
        >
          <LogOut className="size-4" />
          Sign out
        </button>
      ) : (
        <p className="mt-2 px-3 text-[11px] text-muted-foreground">
          Local development mode — Supabase is not configured.
        </p>
      )}
    </div>
  );

  return (
    <div className="app-backdrop min-h-screen">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 hidden w-60 border-r border-border/70 bg-card/40 p-3 backdrop-blur-xl lg:block">
        {sidebar}
      </aside>

      {/* Mobile drawer */}
      <AnimatePresence>
        {mobileOpen ? (
          <div className="fixed inset-0 z-50 lg:hidden">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 bg-black/70"
              onClick={() => setMobileOpen(false)}
            />
            <motion.aside
              initial={{ x: -280 }}
              animate={{ x: 0 }}
              exit={{ x: -280 }}
              transition={{ type: "spring", stiffness: 320, damping: 32 }}
              className="relative z-10 h-full w-64 border-r border-border bg-card p-3"
            >
              <button
                onClick={() => setMobileOpen(false)}
                className="absolute right-3 top-3 rounded-md p-1 text-muted-foreground hover:bg-secondary"
                aria-label="Close navigation"
              >
                <X className="size-4" />
              </button>
              {sidebar}
            </motion.aside>
          </div>
        ) : null}
      </AnimatePresence>

      <div className="lg:pl-60">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border/70 bg-background/80 px-4 backdrop-blur-xl">
          <Button
            variant="ghost"
            size="icon-sm"
            className="lg:hidden"
            onClick={() => setMobileOpen(true)}
            aria-label="Open navigation"
          >
            <Menu />
          </Button>

          <div className="flex-1" />

          <VoiceCommandButton />

          <Link href="/digitizer">
            <Button size="sm">
              <Wand2 />
              <span className="hidden sm:inline">New design</span>
            </Button>
          </Link>

          {loading ? (
            <Spinner />
          ) : me ? (
            <div className="hidden items-center gap-2 sm:flex">
              <span className="grid size-8 place-items-center rounded-full bg-secondary text-xs font-semibold">
                {(me.user.full_name ?? me.user.email).slice(0, 2).toUpperCase()}
              </span>
            </div>
          ) : null}
        </header>

        {error ? (
          <div className="border-b border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive">
            {error}
          </div>
        ) : null}

        <main className="mx-auto w-full max-w-[1600px] p-4 sm:p-6">{children}</main>
      </div>
    </div>
  );
}
