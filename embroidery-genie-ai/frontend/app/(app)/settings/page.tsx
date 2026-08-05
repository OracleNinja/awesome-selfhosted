"use client";

import { Check, Cpu, Plus, Trash2, User } from "lucide-react";
import * as React from "react";

import { useSession } from "@/app/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Badge, EmptyState, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/form";
import { Dialog, useToast } from "@/components/ui/overlay";
import { Tabs } from "@/components/ui/tabs";
import { api, ApiError, type Machine, type Plan } from "@/lib/api";
import { cn, formatMoney, formatNumber } from "@/lib/utils";

export default function SettingsPage() {
  const [tab, setTab] = React.useState("profile");

  React.useEffect(() => {
    const requested = new URLSearchParams(window.location.search).get("tab");
    if (requested) setTab(requested);
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Profile, machines and subscription for this workspace.
        </p>
      </div>

      <Tabs
        value={tab}
        onValueChange={setTab}
        tabs={[
          { value: "profile", label: "Profile" },
          { value: "machines", label: "Machines" },
          { value: "billing", label: "Plan" },
        ]}
      />

      {tab === "profile" ? <ProfileTab /> : null}
      {tab === "machines" ? <MachinesTab /> : null}
      {tab === "billing" ? <BillingTab /> : null}
    </div>
  );
}

function ProfileTab() {
  const { me, refresh } = useSession();
  const toast = useToast();
  const [fullName, setFullName] = React.useState("");
  const [company, setCompany] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (me) {
      setFullName(me.user.full_name ?? "");
      setCompany(me.user.company ?? "");
    }
  }, [me]);

  if (!me) return <Skeleton className="h-64" />;

  const save = async () => {
    setBusy(true);
    try {
      await api.updateProfile({ full_name: fullName, company, onboarded: true });
      await refresh();
      toast.success("Profile saved");
    } catch (exception) {
      toast.error(
        "Could not save",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <User className="size-4" />
            Your profile
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Email" help="Managed by your sign-in provider.">
            <Input value={me.user.email} disabled />
          </Field>
          <Field label="Full name">
            <Input value={fullName} onChange={(event) => setFullName(event.target.value)} />
          </Field>
          <Field label="Company">
            <Input value={company} onChange={(event) => setCompany(event.target.value)} />
          </Field>
          <Button onClick={save} loading={busy}>
            Save profile
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Workspace</CardTitle>
          <CardDescription>
            Designs, orders and customers all belong to a workspace, so a team shares one set of
            data.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <Row label="Name" value={me.organization.name} />
          <Row label="Slug" value={me.organization.slug} />
          <Row label="Currency" value={me.organization.currency} />
          <Row label="Your role" value={me.role} />
          <Row label="Members" value={String(me.memberships.length)} />
        </CardContent>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border/40 py-1.5">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium capitalize">{value}</span>
    </div>
  );
}

function MachinesTab() {
  const { reference } = useSession();
  const toast = useToast();
  const [machines, setMachines] = React.useState<Machine[] | null>(null);
  const [adding, setAdding] = React.useState(false);
  const [preset, setPreset] = React.useState("");

  const load = React.useCallback(() => {
    api.machines().then(setMachines).catch(() => setMachines([]));
  }, []);

  React.useEffect(load, [load]);

  const addPreset = async () => {
    if (!preset) return;
    try {
      await api.machineFromPreset(preset);
      toast.success("Machine added");
      setAdding(false);
      setPreset("");
      load();
    } catch (exception) {
      toast.error(
        "Could not add the machine",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    }
  };

  const remove = async (machine: Machine) => {
    if (!confirm(`Remove ${machine.name}?`)) return;
    try {
      await api.deleteMachine(machine.id);
      load();
    } catch {
      toast.error("Could not remove the machine");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setAdding(true)}>
          <Plus />
          Add machine
        </Button>
      </div>

      {machines === null ? (
        <Skeleton className="h-48" />
      ) : machines.length === 0 ? (
        <EmptyState
          icon={<Cpu className="size-6" />}
          title="No machines yet"
          description="Adding a machine turns on hoop-size and stitch-count pre-flight checks before export."
          action={<Button size="sm" onClick={() => setAdding(true)}>Add from a preset</Button>}
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {machines.map((machine) => (
            <Card key={machine.id}>
              <CardContent className="space-y-3 p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{machine.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {machine.brand}
                      {machine.model ? ` · ${machine.model}` : ""}
                    </p>
                  </div>
                  {machine.is_default ? <Badge variant="success">Default</Badge> : null}
                </div>

                <dl className="grid grid-cols-2 gap-1.5 text-xs">
                  <Spec label="Hoop" value={`${machine.hoop_width_mm}×${machine.hoop_height_mm} mm`} />
                  <Spec label="Needles" value={String(machine.needle_count)} />
                  <Spec label="Heads" value={String(machine.heads)} />
                  <Spec label="Max speed" value={`${formatNumber(machine.max_speed_spm)} spm`} />
                  <Spec label="Max stitches" value={formatNumber(machine.max_stitch_count)} />
                  <Spec label="Formats" value={machine.supported_formats.join(", ").toUpperCase()} />
                </dl>

                {machine.notes ? (
                  <p className="text-xs text-muted-foreground">{machine.notes}</p>
                ) : null}

                <Button size="sm" variant="ghost" onClick={() => remove(machine)}>
                  <Trash2 />
                  Remove
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog
        open={adding}
        onClose={() => setAdding(false)}
        title="Add a machine"
        description="Start from a preset; hoop sizes and formats are pre-filled."
        footer={
          <>
            <Button variant="ghost" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button onClick={addPreset} disabled={!preset}>
              Add machine
            </Button>
          </>
        }
      >
        <Field label="Preset">
          <Select value={preset} onChange={(event) => setPreset(event.target.value)}>
            <option value="">Choose a machine…</option>
            {(reference?.machine_presets ?? []).map((item) => (
              <option key={item.key} value={item.key}>
                {item.brand} {item.model} — {item.needle_count} needle,{" "}
                {item.hoop_width_mm}×{item.hoop_height_mm} mm
              </option>
            ))}
          </Select>
        </Field>
      </Dialog>
    </div>
  );
}

function Spec({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-secondary/40 px-2 py-1">
      <dt className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium" title={value}>
        {value}
      </dd>
    </div>
  );
}

function BillingTab() {
  const { me } = useSession();
  const [plans, setPlans] = React.useState<Plan[] | null>(null);

  React.useEffect(() => {
    api.plans().then((data) => setPlans(data.plans)).catch(() => setPlans([]));
  }, []);

  if (!plans || !me) return <Skeleton className="h-72" />;

  return (
    <div className="space-y-4">
      <Alert level="info" title="Billing provider not connected">
        Plans and quotas are enforced by the API today. Wire up a payment provider (the
        subscription table already carries provider ids) to make upgrades self-serve.
      </Alert>

      <div className="grid gap-4 md:grid-cols-3">
        {plans.map((plan) => {
          const current = plan.tier === me.subscription.tier;
          return (
            <Card
              key={plan.tier}
              className={cn(
                "relative overflow-hidden",
                current && "border-primary shadow-lg shadow-primary/10",
              )}
            >
              {plan.tier === "pro" ? (
                <span className="absolute right-3 top-3">
                  <Badge>Most popular</Badge>
                </span>
              ) : null}
              <CardHeader>
                <CardTitle>{plan.name}</CardTitle>
                <CardDescription>
                  <span className="text-2xl font-semibold text-foreground">
                    {plan.price_monthly === 0 ? "Free" : formatMoney(plan.price_monthly)}
                  </span>
                  {plan.price_monthly > 0 ? <span className="text-sm"> / month</span> : null}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <ul className="space-y-1.5 text-sm">
                  {plan.features.map((feature) => (
                    <li key={feature} className="flex gap-2">
                      <Check className="mt-0.5 size-4 shrink-0 text-[hsl(var(--success))]" />
                      {feature}
                    </li>
                  ))}
                </ul>
                <div className="text-xs text-muted-foreground">
                  Exports: {plan.export_formats.join(", ").toUpperCase()}
                </div>
                <Button className="w-full" variant={current ? "outline" : "default"} disabled={current}>
                  {current ? "Current plan" : `Upgrade to ${plan.name}`}
                </Button>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
