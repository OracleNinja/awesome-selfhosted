"use client";

import { AlertTriangle, Lock, Minus, Package, Plus } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { useSession } from "@/app/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, StatCard } from "@/components/ui/card";
import { Badge, EmptyState, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select } from "@/components/ui/form";
import { Dialog, useToast } from "@/components/ui/overlay";
import { api, ApiError, type Product } from "@/lib/api";
import { cn, formatMoney, formatNumber } from "@/lib/utils";

export default function InventoryPage() {
  const toast = useToast();
  const { reference } = useSession();
  const [products, setProducts] = React.useState<Product[] | null>(null);
  const [value, setValue] = React.useState(0);
  const [locked, setLocked] = React.useState<string | null>(null);
  const [lowOnly, setLowOnly] = React.useState(false);
  const [creating, setCreating] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      const data = await api.products({ limit: 200, low_stock: lowOnly });
      setProducts(data.items);
      setValue(data.inventory_value);
      setLocked(null);
    } catch (exception) {
      setLocked(exception instanceof ApiError ? exception.message : "Could not load inventory.");
      setProducts([]);
    }
  }, [lowOnly]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const adjust = async (product: Product, delta: number) => {
    try {
      await api.adjustStock(product.id, delta);
      await load();
    } catch (exception) {
      toast.error(
        "Stock adjustment refused",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    }
  };

  if (locked) {
    return (
      <EmptyState
        icon={<Lock className="size-6" />}
        title="Inventory is a Business feature"
        description={locked}
        action={
          <Link href="/settings?tab=billing">
            <Button size="sm">Compare plans</Button>
          </Link>
        }
      />
    );
  }

  const lowStock = (products ?? []).filter((product) => product.needs_reorder).length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Inventory</h1>
          <p className="text-sm text-muted-foreground">Blanks on the shelf and what they cost.</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus />
          Add blank
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="SKUs"
          value={formatNumber(products?.length ?? 0)}
          icon={<Package className="size-4" />}
        />
        <StatCard label="Inventory value" value={formatMoney(value)} tone="accent" />
        <StatCard
          label="Need reorder"
          value={formatNumber(lowStock)}
          tone={lowStock ? "warning" : "default"}
          icon={lowStock ? <AlertTriangle className="size-4" /> : undefined}
        />
      </div>

      <Button variant={lowOnly ? "default" : "outline"} size="sm" onClick={() => setLowOnly((v) => !v)}>
        {lowOnly ? "Showing low stock" : "Show low stock only"}
      </Button>

      {products === null ? (
        <Skeleton className="h-72" />
      ) : products.length === 0 ? (
        <EmptyState
          icon={<Package className="size-6" />}
          title={lowOnly ? "Nothing needs reordering" : "No blanks tracked yet"}
          description="Track blank costs so the pricing assistant can quote real numbers."
          action={<Button size="sm" onClick={() => setCreating(true)}>Add a blank</Button>}
        />
      ) : (
        <Card>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead className="border-b border-border/70 text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="p-3">SKU</th>
                  <th className="p-3">Name</th>
                  <th className="p-3">Fabric</th>
                  <th className="p-3 text-right">Cost</th>
                  <th className="p-3 text-right">On hand</th>
                  <th className="p-3 text-right">Value</th>
                  <th className="p-3 text-right">Adjust</th>
                </tr>
              </thead>
              <tbody>
                {products.map((product) => (
                  <tr key={product.id} className="border-b border-border/40">
                    <td className="p-3 font-mono text-xs">{product.sku}</td>
                    <td className="p-3">{product.name}</td>
                    <td className="p-3 text-muted-foreground">
                      {reference?.fabrics.find((f) => f.key === product.fabric_profile)?.name ??
                        product.fabric_profile}
                    </td>
                    <td className="p-3 text-right tabular-nums">{formatMoney(product.blank_cost)}</td>
                    <td className="p-3 text-right">
                      <span
                        className={cn(
                          "tabular-nums",
                          product.needs_reorder && "font-semibold text-[hsl(var(--warning))]",
                        )}
                      >
                        {product.stock_quantity}
                      </span>
                      {product.needs_reorder ? (
                        <Badge variant="warning" className="ml-2">
                          reorder
                        </Badge>
                      ) : null}
                    </td>
                    <td className="p-3 text-right tabular-nums">
                      {formatMoney(product.stock_value ?? 0)}
                    </td>
                    <td className="p-3">
                      <div className="flex justify-end gap-1">
                        <Button size="icon-sm" variant="outline" onClick={() => adjust(product, -1)}>
                          <Minus />
                        </Button>
                        <Button size="icon-sm" variant="outline" onClick={() => adjust(product, 1)}>
                          <Plus />
                        </Button>
                        <Button size="sm" variant="secondary" onClick={() => adjust(product, 12)}>
                          +12
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}

      <CreateProductDialog
        open={creating}
        onClose={() => setCreating(false)}
        fabrics={reference?.fabrics ?? []}
        onCreated={() => {
          setCreating(false);
          void load();
        }}
      />
    </div>
  );
}

function CreateProductDialog({
  open,
  onClose,
  fabrics,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  fabrics: { key: string; name: string }[];
  onCreated: () => void;
}) {
  const toast = useToast();
  const [sku, setSku] = React.useState("");
  const [name, setName] = React.useState("");
  const [fabric, setFabric] = React.useState("cotton_shirt");
  const [cost, setCost] = React.useState(4.25);
  const [stock, setStock] = React.useState(0);
  const [reorder, setReorder] = React.useState(12);
  const [busy, setBusy] = React.useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      await api.createProduct({
        sku,
        name,
        fabric_profile: fabric,
        blank_cost: cost,
        stock_quantity: stock,
        reorder_level: reorder,
      });
      toast.success("Blank added");
      setSku("");
      setName("");
      onCreated();
    } catch (exception) {
      toast.error(
        "Could not add the blank",
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
      title="Add blank"
      description="Blank cost feeds straight into the pricing assistant."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!sku.trim() || !name.trim()}>
            Add blank
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="SKU">
            <Input value={sku} onChange={(event) => setSku(event.target.value)} placeholder="GIL-5000-NVY-L" />
          </Field>
          <Field label="Fabric profile">
            <Select value={fabric} onChange={(event) => setFabric(event.target.value)}>
              {fabrics.map((item) => (
                <option key={item.key} value={item.key}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Name">
          <Input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Gildan 5000 Heavy Cotton — Navy L"
          />
        </Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label="Blank cost">
            <Input
              type="number"
              step="0.01"
              value={cost}
              onChange={(event) => setCost(Number(event.target.value))}
            />
          </Field>
          <Field label="On hand">
            <Input
              type="number"
              value={stock}
              onChange={(event) => setStock(Number(event.target.value))}
            />
          </Field>
          <Field label="Reorder at">
            <Input
              type="number"
              value={reorder}
              onChange={(event) => setReorder(Number(event.target.value))}
            />
          </Field>
        </div>
      </div>
    </Dialog>
  );
}
