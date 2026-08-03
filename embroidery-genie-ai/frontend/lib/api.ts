/**
 * Typed API client.
 *
 * One place that knows how to talk to the backend: attaches the Supabase
 * bearer token, unwraps errors into readable messages, and exposes the
 * endpoints as plain functions the components call.
 */
import { getAccessToken } from "@/lib/supabase";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export class ApiError extends Error {
  status: number;
  problems: { field: string; message: string }[];

  constructor(status: number, message: string, problems: { field: string; message: string }[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problems = problems;
  }

  /** 402 means the plan does not include this, not that payment failed. */
  get isUpgradeRequired() {
    return this.status === 402;
  }

  get isUnauthorized() {
    return this.status === 401;
  }
}

type RequestOptions = {
  method?: string;
  body?: unknown;
  formData?: FormData;
  signal?: AbortSignal;
  raw?: boolean;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = await getAccessToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  const response = await fetch(`${BASE}/api/v1${path}`, {
    method: options.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    signal: options.signal,
  });

  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    let problems: { field: string; message: string }[] = [];
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
      if (Array.isArray(payload.problems)) problems = payload.problems;
    } catch {
      // Non-JSON error body (a proxy page, a gateway timeout). Keep the status.
    }
    throw new ApiError(response.status, message, problems);
  }

  if (options.raw) return (await response.blob()) as unknown as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ------------------------------------------------------------------- types
export type Plan = {
  tier: "free" | "pro" | "business";
  name: string;
  price_monthly: number;
  designs_per_month: number;
  unlimited: boolean;
  seats: number;
  export_formats: string[];
  features: string[];
  commercial_license: boolean;
  production_module: boolean;
};

export type Subscription = {
  tier: Plan["tier"];
  plan: Plan;
  status: string;
  designs_used: number;
  designs_limit: number | null;
  designs_remaining: number | null;
  period_end: string;
  seats: number;
};

export type Me = {
  user: {
    id: string;
    email: string;
    full_name: string | null;
    avatar_url: string | null;
    company: string | null;
    onboarded: boolean;
  };
  organization: { id: string; name: string; slug: string; currency: string };
  role: string;
  subscription: Subscription;
  memberships: { organization_id: string; name: string; role: string; is_active: boolean }[];
};

export type DesignFile = {
  id: string;
  kind: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  url: string;
};

export type ThreadColor = {
  index: number;
  hex: string;
  name: string;
  code: string;
  catalog: string;
  stitches: number;
  technique: string;
  label: string;
};

export type Issue = { level: "error" | "warning" | "info"; code: string; message: string };

export type Analysis = {
  compatibility_score: number;
  verdict: string;
  detected: Record<string, unknown>;
  colors: { hex: string; share: number; rgb: number[]; thread?: { name: string; code: string } }[];
  recommendations: string[];
  issues: Issue[];
  metrics: Record<string, number | boolean | string>;
  recommended_placements: {
    key: string;
    name: string;
    fabric: string;
    score: number;
    width_mm: number;
    height_mm: number;
    notes: string[];
  }[];
  suggested_fabric: string;
  suggested_colors: number;
  ai: Record<string, unknown> | null;
  /**
   * Why the AI layer did or did not produce a description — `cached`,
   * `blocked` by a cost budget, `skipped` because it is switched off. When it
   * carries a message the backend has already appended it to
   * `recommendations`, so the panel explains itself without extra wiring.
   */
  ai_status: {
    status: "ok" | "cached" | "skipped" | "blocked" | "failed";
    reason: string | null;
    message: string;
    model: string | null;
    cached: boolean;
    estimated_cost_usd: number | null;
  } | null;
};

export type Design = {
  id: string;
  name: string;
  description: string | null;
  status: "draft" | "analyzing" | "vectorized" | "digitizing" | "ready" | "failed" | "archived";
  source: string;
  tags: string[];
  customer_id: string | null;
  compatibility_score: number | null;
  analysis: Analysis | Record<string, never>;
  stitch_count: number | null;
  color_count: number | null;
  width_mm: number | null;
  height_mm: number | null;
  trim_count: number | null;
  thread_length_m: number | null;
  estimated_minutes: number | null;
  thread_chart: ThreadColor[];
  issues: Issue[];
  error_message: string | null;
  files: DesignFile[];
  created_at: string | null;
  updated_at: string | null;
  settings: Record<string, unknown> | null;
};

export type StitchBlock = {
  color: string;
  name: string;
  code: string;
  technique: string;
  label: string;
  coords: number[];
  flags: number[];
};

export type StitchStream = {
  bounds: { minX: number; minY: number; maxX: number; maxY: number };
  decimation: number;
  blocks: StitchBlock[];
  summary: Record<string, unknown>;
};

export type Fabric = {
  key: string;
  name: string;
  category: string;
  fill_spacing_mm: number;
  satin_spacing_mm: number;
  pull_compensation_mm: number;
  underlay: string[];
  stabilizer: string;
  needle: string;
  topping: string;
  notes: string;
  min_letter_height_mm: number;
  speed_factor: number;
  tags: string[];
};

export type FormatInfo = {
  extension: string;
  name: string;
  vendor: string;
  available: boolean;
  backend: string;
  notes: string;
};

export type Machine = {
  id: string;
  name: string;
  brand: string;
  model: string | null;
  heads: number;
  needle_count: number;
  hoop_width_mm: number;
  hoop_height_mm: number;
  max_stitch_count: number;
  max_speed_spm: number;
  supported_formats: string[];
  hourly_rate: number | null;
  is_default: boolean;
  notes: string | null;
};

export type MachinePreset = {
  key: string;
  brand: string;
  model: string;
  category: string;
  heads: number;
  needle_count: number;
  hoop_width_mm: number;
  hoop_height_mm: number;
  max_speed_spm: number;
  max_stitch_count: number;
  formats: string[];
  notes: string;
};

export type StudioReference = {
  fabrics: Fabric[];
  formats: FormatInfo[];
  fonts: { key: string; name: string; notes: string; available: boolean }[];
  fonts_healthy: boolean;
  thread_catalogs: { name: string; count: number; colors: { code: string; name: string; hex: string }[] }[];
  machine_presets: MachinePreset[];
  machine_brands: string[];
  mockup_templates: {
    key: string;
    name: string;
    fabric_profile: string;
    placement: string;
    placement_width_mm: number;
  }[];
  garment_colors: { key: string; hex: string; name: string }[];
  placements: { key: string; name: string; max_width_mm: number; max_height_mm: number }[];
};

export type Customer = {
  id: string;
  name: string;
  company: string | null;
  email: string | null;
  phone: string | null;
  address: Record<string, unknown>;
  tax_exempt: boolean;
  notes: string | null;
  tags: string[];
  order_count?: number;
  revenue?: number;
};

export type OrderItem = {
  id: string;
  design_id: string | null;
  design_name: string | null;
  stitch_count: number | null;
  product_id: string | null;
  product_sku: string | null;
  description: string;
  quantity: number;
  unit_price: number;
  unit_cost: number;
  line_total: number;
  placement: string | null;
};

export type OrderStatus =
  | "quote" | "approved" | "digitizing" | "sewing" | "completed" | "delivered" | "cancelled";

export type Order = {
  id: string;
  number: string;
  status: OrderStatus;
  customer_id: string | null;
  customer_name: string | null;
  due_date: string | null;
  rush: boolean;
  notes: string | null;
  placement: string | null;
  subtotal: number;
  discount: number;
  tax: number;
  total: number;
  cost_total: number;
  profit: number;
  margin_pct: number;
  quantity: number;
  items: OrderItem[];
  events: { id: string; from_status: string | null; to_status: string; note: string | null; created_at: string }[];
  created_at: string | null;
};

export type Product = {
  id: string;
  sku: string;
  name: string;
  category: string | null;
  fabric_profile: string;
  color: string | null;
  size: string | null;
  blank_cost: number;
  stock_quantity: number;
  reorder_level: number;
  supplier: string | null;
  active: boolean;
  needs_reorder?: boolean;
  stock_value?: number;
};

export type Invoice = {
  id: string;
  number: string;
  status: "draft" | "sent" | "paid" | "overdue" | "void";
  order_id: string | null;
  order_number: string | null;
  customer_name: string | null;
  issue_date: string | null;
  due_date: string | null;
  subtotal: number;
  tax: number;
  total: number;
  amount_paid: number;
  balance_due: number;
  currency: string;
  line_items: Record<string, unknown>[];
  overdue: boolean;
};

export type PricingResult = {
  unit_cost: number;
  unit_wholesale: number;
  unit_retail: number;
  total_cost: number;
  total_wholesale: number;
  total_retail: number;
  breakdown: Record<string, Record<string, number | boolean | string>>;
  margins: Record<string, number>;
  quantity_breaks: { quantity: number; unit_cost: number; unit_price: number; total: number; margin_pct: number }[];
  assumptions: string[];
  currency: string;
  inputs: Record<string, unknown>;
};

export type Dashboard = {
  designs: {
    total: number;
    ready: number;
    this_month: number;
    total_stitches: number;
    recent: {
      id: string; name: string; status: string; stitch_count: number | null;
      color_count: number | null; compatibility_score: number | null; created_at: string | null;
    }[];
  };
  orders: {
    by_status: Record<string, number>;
    open: number;
    overdue: number;
    due_soon: { id: string; number: string; status: string; due_date: string | null; total: number; customer: string | null }[];
  };
  finance: {
    revenue_month: number; cost_month: number; profit_month: number;
    margin_pct: number; outstanding_invoices: number; currency: string;
  };
  customers: { total: number };
  inventory: { low_stock: number };
  subscription: Subscription;
};

export type VoiceCommand = {
  intent: string;
  confidence: number;
  entities: Record<string, string | number>;
  transcript: string;
  action: { type: string; route?: string; command?: string; field?: string; value?: unknown } | null;
  reply: string;
  suggestions: string[];
};

// ---------------------------------------------------------------- endpoints
export const api = {
  // auth
  me: () => request<Me>("/auth/me"),
  updateProfile: (body: Record<string, unknown>) =>
    request<Me>("/auth/me", { method: "PATCH", body }),
  plans: () => request<{ plans: Plan[] }>("/auth/plans"),
  usage: () => request<Subscription>("/auth/usage"),

  // dashboard
  dashboard: () => request<Dashboard>("/dashboard"),

  // studio reference
  reference: () => request<StudioReference>("/studio/reference"),
  estimate: (params: Record<string, string | number>) =>
    request<{ minutes: number; formatted: string; effective_speed_spm: number; fabric: string }>(
      `/studio/estimate?${new URLSearchParams(
        Object.entries(params).map(([k, v]) => [k, String(v)]),
      )}`,
    ),
  textPreview: (body: Record<string, unknown>) =>
    request<Record<string, unknown> & { stitches: StitchStream; issues: Issue[] }>(
      "/studio/text/preview",
      { body },
    ),

  // designs
  designs: (params: Record<string, string | number> = {}) =>
    request<{ items: Design[]; total: number }>(
      `/designs?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`,
    ),
  design: (id: string) => request<Design>(`/designs/${id}`),
  stitches: (id: string) => request<StitchStream>(`/designs/${id}/stitches`),
  createDesign: (body: Record<string, unknown>) => request<Design>("/designs", { body }),
  uploadDesign: (file: File, fields: Record<string, string> = {}) => {
    const form = new FormData();
    form.append("file", file);
    Object.entries(fields).forEach(([key, value]) => form.append(key, value));
    return request<Design>("/designs/upload", { formData: form });
  },
  createTextDesign: (body: Record<string, unknown>) => request<Design>("/designs/text", { body }),
  updateDesign: (id: string, body: Record<string, unknown>) =>
    request<Design>(`/designs/${id}`, { method: "PATCH", body }),
  deleteDesign: (id: string) => request<{ detail: string }>(`/designs/${id}`, { method: "DELETE" }),
  duplicateDesign: (id: string) => request<Design>(`/designs/${id}/duplicate`, { method: "POST" }),
  analyze: (id: string, useAi = true) =>
    request<Analysis>(`/designs/${id}/analyze?use_ai=${useAi}`, { method: "POST" }),
  vectorize: (id: string, body: Record<string, unknown>) =>
    request<{ layers: number; palette: string[]; svg: string | null; width_mm: number; height_mm: number }>(
      `/designs/${id}/vectorize`,
      { body },
    ),
  digitize: (id: string, body: Record<string, unknown>) =>
    request<Record<string, unknown> & { stitch_count: number; colors: ThreadColor[]; issues: Issue[] }>(
      `/designs/${id}/digitize`,
      { body },
    ),
  exportDesign: (id: string, body: Record<string, unknown>) =>
    request<Blob>(`/designs/${id}/export`, { body, raw: true }),
  mockup: (id: string, body: Record<string, unknown>) =>
    request<Blob>(`/designs/${id}/mockup`, { body, raw: true }),

  // machines
  machines: () => request<Machine[]>("/machines"),
  machinePresets: () => request<{ presets: MachinePreset[] }>("/machines/presets"),
  createMachine: (body: Record<string, unknown>) => request<Machine>("/machines", { body }),
  machineFromPreset: (key: string) =>
    request<Machine>(`/machines/from-preset/${key}`, { method: "POST" }),
  updateMachine: (id: string, body: Record<string, unknown>) =>
    request<Machine>(`/machines/${id}`, { method: "PUT", body }),
  deleteMachine: (id: string) => request<{ detail: string }>(`/machines/${id}`, { method: "DELETE" }),

  // customers
  customers: (params: Record<string, string | number> = {}) =>
    request<{ items: Customer[]; total: number }>(
      `/customers?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`,
    ),
  customer: (id: string) =>
    request<Customer & { orders: Order[]; designs: Design[] }>(`/customers/${id}`),
  createCustomer: (body: Record<string, unknown>) => request<Customer>("/customers", { body }),
  updateCustomer: (id: string, body: Record<string, unknown>) =>
    request<Customer>(`/customers/${id}`, { method: "PUT", body }),
  deleteCustomer: (id: string) => request<{ detail: string }>(`/customers/${id}`, { method: "DELETE" }),

  // orders
  orders: (params: Record<string, string | number> = {}) =>
    request<{ items: Order[]; total: number }>(
      `/orders?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`,
    ),
  order: (id: string) => request<Order>(`/orders/${id}`),
  orderBoard: () => request<{ columns: Record<string, Order[]>; totals: Record<string, number> }>("/orders/board"),
  createOrder: (body: Record<string, unknown>) => request<Order>("/orders", { body }),
  updateOrder: (id: string, body: Record<string, unknown>) =>
    request<Order>(`/orders/${id}`, { method: "PUT", body }),
  setOrderStatus: (id: string, status: string, note?: string) =>
    request<Order>(`/orders/${id}/status`, { body: { status, note } }),

  // inventory
  products: (params: Record<string, string | number | boolean> = {}) =>
    request<{ items: Product[]; total: number; inventory_value: number }>(
      `/products?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`,
    ),
  createProduct: (body: Record<string, unknown>) => request<Product>("/products", { body }),
  updateProduct: (id: string, body: Record<string, unknown>) =>
    request<Product>(`/products/${id}`, { method: "PUT", body }),
  adjustStock: (id: string, delta: number, reason?: string) =>
    request<Product>(`/products/${id}/stock`, { body: { delta, reason } }),

  // invoices
  invoices: (params: Record<string, string | number> = {}) =>
    request<{ items: Invoice[]; total: number; outstanding: number }>(
      `/invoices?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`,
    ),
  createInvoice: (body: Record<string, unknown>) => request<Invoice>("/invoices", { body }),
  sendInvoice: (id: string) => request<Invoice>(`/invoices/${id}/send`, { method: "POST" }),
  payInvoice: (id: string, amount: number) =>
    request<Invoice>(`/invoices/${id}/payments`, { body: { amount } }),

  // pricing
  quote: (body: Record<string, unknown>) => request<PricingResult>("/pricing/quote", { body }),
  pricingDefaults: () => request<Record<string, unknown>>("/pricing/defaults"),

  // voice
  voice: (transcript: string) => request<VoiceCommand>("/voice/command", { body: { transcript } }),
  voiceExamples: () => request<{ examples: string[] }>("/voice/examples"),
};

/** Trigger a browser download for a blob returned by the API. */
export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
