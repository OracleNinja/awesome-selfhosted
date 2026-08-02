"use client";

import { motion } from "framer-motion";
import { Boxes, Lock, Plus } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge, EmptyState, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/form";
import { Dialog, useToast } from "@/components/ui/overlay";
import { Tabs } from "@/components/ui/tabs";
import { api, ApiError, type Customer, type Design, type Order } from "@/lib/api";
import { cn, formatDate, formatMoney, formatNumber, humanize } from "@/lib/utils";

const COLUMNS: { key: string; label: string; tone: string }[] = [
  { key: "quote", label: "Quote", tone: "bg-secondary" },
  { key: "approved", label: "Approved", tone: "bg-primary/70" },
  { key: "digitizing", label: "Digitizing", tone: "bg-accent/70" },
  { key: "sewing", label: "Sewing", tone: "bg-[hsl(var(--warning))]" },
  { key: "completed", label: "Completed", tone: "bg-[hsl(var(--success))]" },
  { key: "delivered", label: "Delivered", tone: "bg-[hsl(var(--success))]/60" },
];

export default function OrdersPage() {
  const toast = useToast();
  const [board, setBoard] = React.useState<Record<string, Order[]> | null>(null);
  const [locked, setLocked] = React.useState<string | null>(null);
  const [view, setView] = React.useState("board");
  const [creating, setCreating] = React.useState(false);
  const [active, setActive] = React.useState<Order | null>(null);

  const load = React.useCallback(async () => {
    try {
      const data = await api.orderBoard();
      setBoard(data.columns);
      setLocked(null);
    } catch (exception) {
      if (exception instanceof ApiError && exception.isUpgradeRequired) {
        setLocked(exception.message);
      } else {
        setLocked(exception instanceof ApiError ? exception.message : "Could not load orders.");
      }
      setBoard({});
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const move = async (order: Order, status: string) => {
    try {
      await api.setOrderStatus(order.id, status);
      toast.success(`${order.number} moved to ${humanize(status)}`);
      setActive(null);
      await load();
    } catch (exception) {
      toast.error(
        "Could not change the status",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    }
  };

  if (locked) {
    return (
      <EmptyState
        icon={<Lock className="size-6" />}
        title="Production management is a Business feature"
        description={locked}
        action={
          <Link href="/settings?tab=billing">
            <Button size="sm">Compare plans</Button>
          </Link>
        }
      />
    );
  }

  const all = board ? Object.values(board).flat() : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Orders</h1>
          <p className="text-sm text-muted-foreground">
            {formatNumber(all.length)} active order{all.length === 1 ? "" : "s"} on the floor.
          </p>
        </div>
        <div className="flex gap-2">
          <Tabs
            value={view}
            onValueChange={setView}
            size="sm"
            tabs={[
              { value: "board", label: "Board" },
              { value: "list", label: "List" },
            ]}
          />
          <Button onClick={() => setCreating(true)}>
            <Plus />
            New order
          </Button>
        </div>
      </div>

      {!board ? (
        <Skeleton className="h-96" />
      ) : all.length === 0 ? (
        <EmptyState
          icon={<Boxes className="size-6" />}
          title="No orders yet"
          description="Create a quote, approve it, and track it through digitizing, sewing and delivery."
          action={<Button size="sm" onClick={() => setCreating(true)}>Create an order</Button>}
        />
      ) : view === "board" ? (
        <div className="scrollbar-thin grid grid-flow-col gap-3 overflow-x-auto pb-3 [grid-auto-columns:minmax(240px,1fr)]">
          {COLUMNS.map((column) => (
            <div key={column.key} className="min-w-0">
              <div className="mb-2 flex items-center gap-2">
                <span className={cn("size-2 rounded-full", column.tone)} />
                <span className="text-sm font-medium">{column.label}</span>
                <span className="text-xs text-muted-foreground">
                  {board[column.key]?.length ?? 0}
                </span>
              </div>
              <div className="space-y-2">
                {(board[column.key] ?? []).map((order, index) => (
                  <motion.button
                    key={order.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: index * 0.03 }}
                    onClick={() => setActive(order)}
                    className="w-full rounded-lg border border-border/70 bg-card p-3 text-left transition-colors hover:border-primary/40"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs font-semibold">{order.number}</span>
                      {order.rush ? <Badge variant="destructive">Rush</Badge> : null}
                    </div>
                    <p className="mt-1 truncate text-sm">{order.customer_name ?? "No customer"}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {order.quantity} pcs · {formatMoney(order.total)}
                    </p>
                    {order.due_date ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Due {formatDate(order.due_date)}
                      </p>
                    ) : null}
                  </motion.button>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="border-b border-border/70 text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="p-3">Order</th>
                  <th className="p-3">Customer</th>
                  <th className="p-3">Status</th>
                  <th className="p-3 text-right">Qty</th>
                  <th className="p-3 text-right">Total</th>
                  <th className="p-3 text-right">Margin</th>
                  <th className="p-3">Due</th>
                </tr>
              </thead>
              <tbody>
                {all.map((order) => (
                  <tr
                    key={order.id}
                    onClick={() => setActive(order)}
                    className="cursor-pointer border-b border-border/40 hover:bg-secondary/30"
                  >
                    <td className="p-3 font-mono text-xs">{order.number}</td>
                    <td className="p-3">{order.customer_name ?? "—"}</td>
                    <td className="p-3">
                      <Badge variant="secondary">{humanize(order.status)}</Badge>
                    </td>
                    <td className="p-3 text-right tabular-nums">{order.quantity}</td>
                    <td className="p-3 text-right tabular-nums">{formatMoney(order.total)}</td>
                    <td className="p-3 text-right tabular-nums">{order.margin_pct}%</td>
                    <td className="p-3">{formatDate(order.due_date)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      <OrderDetail order={active} onClose={() => setActive(null)} onMove={move} />
      <CreateOrderDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setCreating(false);
          void load();
        }}
      />
    </div>
  );
}

function OrderDetail({
  order,
  onClose,
  onMove,
}: {
  order: Order | null;
  onClose: () => void;
  onMove: (order: Order, status: string) => void;
}) {
  if (!order) return null;
  const next = COLUMNS.map((column) => column.key).filter((key) => key !== order.status);

  return (
    <Dialog
      open={Boolean(order)}
      onClose={onClose}
      title={order.number}
      description={`${order.customer_name ?? "No customer"} · ${humanize(order.status)}`}
      size="lg"
    >
      <div className="space-y-5">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric label="Quantity" value={String(order.quantity)} />
          <Metric label="Total" value={formatMoney(order.total)} />
          <Metric label="Cost" value={formatMoney(order.cost_total)} />
          <Metric label="Profit" value={`${formatMoney(order.profit)} (${order.margin_pct}%)`} />
        </div>

        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">Items</p>
          <div className="space-y-1.5">
            {order.items.map((item) => (
              <div key={item.id} className="flex items-center justify-between gap-3 rounded-lg border border-border/60 p-2 text-sm">
                <span className="min-w-0">
                  <span className="block truncate">{item.description}</span>
                  <span className="block text-xs text-muted-foreground">
                    {item.design_name ? `${item.design_name} · ` : ""}
                    {item.stitch_count ? `${formatNumber(item.stitch_count)} stitches · ` : ""}
                    {item.quantity} × {formatMoney(item.unit_price)}
                  </span>
                </span>
                <span className="shrink-0 tabular-nums">{formatMoney(item.line_total)}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Move to
          </p>
          <div className="flex flex-wrap gap-2">
            {next.map((status) => (
              <Button key={status} size="sm" variant="outline" onClick={() => onMove(order, status)}>
                {humanize(status)}
              </Button>
            ))}
          </div>
        </div>

        <div>
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            History
          </p>
          <ol className="space-y-1.5 border-l border-border/60 pl-4 text-sm">
            {order.events.map((event) => (
              <li key={event.id} className="relative">
                <span className="absolute -left-[21px] top-1.5 size-2 rounded-full bg-primary" />
                <span className="font-medium">{humanize(event.to_status)}</span>
                {event.note ? <span className="text-muted-foreground"> — {event.note}</span> : null}
                <span className="block text-xs text-muted-foreground">{formatDate(event.created_at)}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </Dialog>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-secondary/40 p-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold tabular-nums">{value}</p>
    </div>
  );
}

function CreateOrderDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [customers, setCustomers] = React.useState<Customer[]>([]);
  const [designs, setDesigns] = React.useState<Design[]>([]);
  const [customerId, setCustomerId] = React.useState("");
  const [designId, setDesignId] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [quantity, setQuantity] = React.useState(24);
  const [unitPrice, setUnitPrice] = React.useState(18.5);
  const [unitCost, setUnitCost] = React.useState(9.2);
  const [dueDate, setDueDate] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    if (!open) return;
    api.customers({ limit: 100 }).then((data) => setCustomers(data.items)).catch(() => undefined);
    api.designs({ limit: 100, status: "ready" }).then((data) => setDesigns(data.items)).catch(() => undefined);
  }, [open]);

  const submit = async () => {
    setBusy(true);
    try {
      await api.createOrder({
        customer_id: customerId || null,
        due_date: dueDate || null,
        items: [
          {
            design_id: designId || null,
            description: description || "Embroidered garment",
            quantity,
            unit_price: unitPrice,
            unit_cost: unitCost,
          },
        ],
      });
      toast.success("Order created");
      onCreated();
    } catch (exception) {
      toast.error(
        "Could not create the order",
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
      title="New order"
      description="Starts as a quote. Approve it to move into production."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy}>
            Create order
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Customer">
            <Select value={customerId} onChange={(event) => setCustomerId(event.target.value)}>
              <option value="">No customer</option>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Due date">
            <Input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
          </Field>
        </div>

        <Field label="Design" help="Ready designs only — the stitch count drives the cost.">
          <Select
            value={designId}
            onChange={(event) => {
              setDesignId(event.target.value);
              const design = designs.find((item) => item.id === event.target.value);
              if (design && !description) setDescription(design.name);
            }}
          >
            <option value="">No design</option>
            {designs.map((design) => (
              <option key={design.id} value={design.id}>
                {design.name} ({formatNumber(design.stitch_count)} st)
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Description">
          <Input
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Navy tee, left chest"
          />
        </Field>

        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Quantity">
            <Input
              type="number"
              min={1}
              value={quantity}
              onChange={(event) => setQuantity(Math.max(1, Number(event.target.value)))}
            />
          </Field>
          <Field label="Unit price">
            <Input
              type="number"
              step="0.01"
              value={unitPrice}
              onChange={(event) => setUnitPrice(Number(event.target.value))}
            />
          </Field>
          <Field label="Unit cost">
            <Input
              type="number"
              step="0.01"
              value={unitCost}
              onChange={(event) => setUnitCost(Number(event.target.value))}
            />
          </Field>
        </div>

        <p className="text-xs text-muted-foreground">
          Line total {formatMoney(quantity * unitPrice)} · margin{" "}
          {unitPrice > 0 ? (((unitPrice - unitCost) / unitPrice) * 100).toFixed(1) : "0"}%
        </p>
      </div>
    </Dialog>
  );
}
