"use client";

import { Plus, Search, Users } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, EmptyState, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Textarea } from "@/components/ui/form";
import { Dialog, useToast } from "@/components/ui/overlay";
import { api, ApiError, type Customer } from "@/lib/api";
import { formatDate, formatMoney, formatNumber, humanize } from "@/lib/utils";

export default function CustomersPage() {
  const toast = useToast();
  const [customers, setCustomers] = React.useState<Customer[] | null>(null);
  const [query, setQuery] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const [detail, setDetail] = React.useState<
    (Customer & { orders: { id: string; number: string; status: string; total: number; due_date: string | null }[]; designs: { id: string; name: string; status: string }[] }) | null
  >(null);

  const load = React.useCallback(() => {
    const params: Record<string, string | number> = { limit: 100 };
    if (query.trim()) params.q = query.trim();
    api
      .customers(params)
      .then((data) => setCustomers(data.items))
      .catch(() => setCustomers([]));
  }, [query]);

  React.useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [load]);

  const open = async (id: string) => {
    try {
      setDetail((await api.customer(id)) as never);
    } catch {
      toast.error("Could not load that customer");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Customers</h1>
          <p className="text-sm text-muted-foreground">
            {customers ? `${formatNumber(customers.length)} customers` : "Loading…"}
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus />
          New customer
        </Button>
      </div>

      <div className="relative max-w-md">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search by name, company or email"
          className="pl-9"
        />
      </div>

      {customers === null ? (
        <Skeleton className="h-72" />
      ) : customers.length === 0 ? (
        <EmptyState
          icon={<Users className="size-6" />}
          title={query ? "No customers match" : "No customers yet"}
          description="Customers link designs, orders and invoices together."
          action={<Button size="sm" onClick={() => setCreating(true)}>Add a customer</Button>}
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="border-b border-border/70 text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="p-3">Name</th>
                  <th className="p-3">Company</th>
                  <th className="p-3">Contact</th>
                  <th className="p-3 text-right">Orders</th>
                  <th className="p-3 text-right">Revenue</th>
                </tr>
              </thead>
              <tbody>
                {customers.map((customer) => (
                  <tr
                    key={customer.id}
                    onClick={() => open(customer.id)}
                    className="cursor-pointer border-b border-border/40 hover:bg-secondary/30"
                  >
                    <td className="p-3 font-medium">{customer.name}</td>
                    <td className="p-3 text-muted-foreground">{customer.company ?? "—"}</td>
                    <td className="p-3 text-muted-foreground">
                      {customer.email ?? customer.phone ?? "—"}
                    </td>
                    <td className="p-3 text-right tabular-nums">{customer.order_count ?? 0}</td>
                    <td className="p-3 text-right tabular-nums">
                      {formatMoney(customer.revenue ?? 0)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      <Dialog
        open={Boolean(detail)}
        onClose={() => setDetail(null)}
        title={detail?.name}
        description={detail?.company ?? detail?.email ?? undefined}
        size="lg"
      >
        {detail ? (
          <div className="space-y-5">
            <div className="grid gap-2 sm:grid-cols-2">
              <Detail label="Email" value={detail.email ?? "—"} />
              <Detail label="Phone" value={detail.phone ?? "—"} />
            </div>
            {detail.notes ? <p className="text-sm text-muted-foreground">{detail.notes}</p> : null}

            <section>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Orders ({detail.orders.length})
              </p>
              {detail.orders.length ? (
                <div className="space-y-1.5">
                  {detail.orders.map((order) => (
                    <div
                      key={order.id}
                      className="flex items-center justify-between gap-2 rounded-lg border border-border/60 p-2 text-sm"
                    >
                      <span className="font-mono text-xs">{order.number}</span>
                      <Badge variant="secondary">{humanize(order.status)}</Badge>
                      <span className="text-xs text-muted-foreground">
                        {formatDate(order.due_date)}
                      </span>
                      <span className="tabular-nums">{formatMoney(order.total)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No orders yet.</p>
              )}
            </section>

            <section>
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Designs ({detail.designs.length})
              </p>
              {detail.designs.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {detail.designs.map((design) => (
                    <Badge key={design.id} variant="outline">
                      {design.name}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No designs linked yet.</p>
              )}
            </section>
          </div>
        ) : null}
      </Dialog>

      <CreateCustomerDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          load();
        }}
      />
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-secondary/40 p-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="truncate text-sm">{value}</p>
    </div>
  );
}

function CreateCustomerDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [name, setName] = React.useState("");
  const [company, setCompany] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [phone, setPhone] = React.useState("");
  const [notes, setNotes] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await api.createCustomer({
        name,
        company: company || null,
        email: email || null,
        phone: phone || null,
        notes: notes || null,
      });
      toast.success("Customer added");
      setName("");
      setCompany("");
      setEmail("");
      setPhone("");
      setNotes("");
      onCreated();
    } catch (exception) {
      toast.error(
        "Could not add the customer",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New customer"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Add customer
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Company">
          <Input value={company} onChange={(event) => setCompany(event.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Email">
            <Input type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
          </Field>
          <Field label="Phone">
            <Input value={phone} onChange={(event) => setPhone(event.target.value)} />
          </Field>
        </div>
        <Field label="Notes">
          <Textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} />
        </Field>
      </div>
    </Dialog>
  );
}
