"use client";

/**
 * Design workspace — the screen the product lives or dies on.
 *
 * Left: stitch simulator / AI analysis / garment mockup.
 * Right: digitizing controls, thread chart, pre-flight, export, pricing.
 */

import {
  AlertTriangle,
  Download,
  Layers,
  Loader2,
  Ruler,
  ScanEye,
  Shirt,
  Sparkles,
  Trash2,
  Wand2,
} from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import * as React from "react";

import { useSession } from "@/app/providers";
import { StitchSimulator } from "@/components/studio/stitch-simulator";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Badge, EmptyState, IssueList, Progress, Skeleton } from "@/components/ui/feedback";
import { Field, Input, Select, Slider, Switch } from "@/components/ui/form";
import { Dialog, useToast } from "@/components/ui/overlay";
import { Tabs } from "@/components/ui/tabs";
import {
  api,
  ApiError,
  downloadBlob,
  type Design,
  type Machine,
  type PricingResult,
  type StitchStream,
} from "@/lib/api";
import { cn, contrastText, formatMinutes, formatMm, formatMoney, formatNumber, humanize } from "@/lib/utils";

export default function DesignWorkspace() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const { reference, me } = useSession();

  const [design, setDesign] = React.useState<Design | null>(null);
  const [stream, setStream] = React.useState<StitchStream | null>(null);
  const [machines, setMachines] = React.useState<Machine[]>([]);
  const [view, setView] = React.useState("simulator");
  const [busy, setBusy] = React.useState<string | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [exportOpen, setExportOpen] = React.useState(false);
  const [mockupUrl, setMockupUrl] = React.useState<string | null>(null);

  // Digitizing settings.
  const [fabric, setFabric] = React.useState("cotton_shirt");
  const [machineId, setMachineId] = React.useState("");
  const [width, setWidth] = React.useState(89);
  const [density, setDensity] = React.useState(1);
  const [angle, setAngle] = React.useState(45);
  const [underlay, setUnderlay] = React.useState(true);
  const [autoSatin, setAutoSatin] = React.useState(true);
  const [maxColors, setMaxColors] = React.useState(0);

  const load = React.useCallback(async () => {
    try {
      const record = await api.design(params.id);
      setDesign(record);
      const settings = record.settings as Record<string, unknown> | null;
      if (settings) {
        if (typeof settings.fabric === "string") setFabric(settings.fabric);
        if (typeof settings.target_width_mm === "number") setWidth(settings.target_width_mm);
        if (typeof settings.density_scale === "number") setDensity(settings.density_scale);
        if (typeof settings.fill_angle_deg === "number") setAngle(settings.fill_angle_deg);
        if (typeof settings.auto_underlay === "boolean") setUnderlay(settings.auto_underlay);
        if (typeof settings.auto_satin === "boolean") setAutoSatin(settings.auto_satin);
        if (typeof settings.machine_id === "string") setMachineId(settings.machine_id);
      } else if (record.analysis && "suggested_fabric" in record.analysis) {
        setFabric((record.analysis as { suggested_fabric: string }).suggested_fabric);
      }
      if (record.status === "ready") {
        api.stitches(record.id).then(setStream).catch(() => setStream(null));
      }
    } catch (exception) {
      setError(exception instanceof ApiError ? exception.message : "Could not load the design.");
    }
  }, [params.id]);

  React.useEffect(() => {
    void load();
    api.machines().then(setMachines).catch(() => setMachines([]));
  }, [load]);

  const digitize = async () => {
    setBusy("digitize");
    try {
      const result = await api.digitize(params.id, {
        fabric,
        machine_id: machineId || null,
        target_width_mm: width,
        density_scale: density,
        fill_angle_deg: angle,
        auto_underlay: underlay,
        auto_satin: autoSatin,
        max_colors: maxColors || null,
      });
      toast.success(
        "Digitized",
        `${formatNumber(result.stitch_count)} stitches across ${result.colors.length} colours`,
      );
      await load();
      setView("simulator");
    } catch (exception) {
      toast.error(
        "Digitizing failed",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    } finally {
      setBusy(null);
    }
  };

  const analyze = async () => {
    setBusy("analyze");
    try {
      await api.analyze(params.id, true);
      await load();
      toast.success("Analysis refreshed");
      setView("analysis");
    } catch (exception) {
      toast.error(
        "Analysis failed",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!confirm("Delete this design and all its files? This cannot be undone.")) return;
    try {
      await api.deleteDesign(params.id);
      toast.success("Design deleted");
      router.push("/studio");
    } catch {
      toast.error("Could not delete the design");
    }
  };

  if (error) {
    return <EmptyState icon={<AlertTriangle className="size-6" />} title="Design unavailable" description={error} />;
  }
  if (!design) {
    return (
      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <Skeleton className="h-[560px]" />
        <Skeleton className="h-[560px]" />
      </div>
    );
  }

  const analysis = design.analysis && "compatibility_score" in design.analysis ? design.analysis : null;
  const ready = design.status === "ready";
  const fabricProfile = reference?.fabrics.find((item) => item.key === fabric);

  return (
    <div className="space-y-4">
      {/* -------------------------------------------------------------- header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-2xl font-semibold tracking-tight">{design.name}</h1>
            <Badge variant={ready ? "success" : design.status === "failed" ? "destructive" : "secondary"}>
              {humanize(design.status)}
            </Badge>
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
                {design.compatibility_score}% embroidery ready
              </Badge>
            ) : null}
          </div>
          {ready ? (
            <p className="mt-1 text-sm text-muted-foreground">
              {formatNumber(design.stitch_count)} stitches · {design.color_count} colours ·{" "}
              {formatMm(design.width_mm)} × {formatMm(design.height_mm)} ·{" "}
              {formatMinutes(design.estimated_minutes)} per piece
            </p>
          ) : (
            <p className="mt-1 text-sm text-muted-foreground">
              Set the fabric and size, then digitize to generate stitches.
            </p>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={analyze} loading={busy === "analyze"}>
            <ScanEye />
            Re-analyze
          </Button>
          <Button onClick={() => setExportOpen(true)} disabled={!ready}>
            <Download />
            Export
          </Button>
          <Button variant="ghost" size="icon" onClick={remove} title="Delete design">
            <Trash2 />
          </Button>
        </div>
      </div>

      {design.error_message ? <Alert level="error">{design.error_message}</Alert> : null}

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        {/* ------------------------------------------------------------ canvas */}
        <div className="space-y-4">
          <Tabs
            value={view}
            onValueChange={setView}
            tabs={[
              { value: "simulator", label: "Stitch simulator" },
              { value: "analysis", label: "AI analysis" },
              { value: "mockup", label: "Mockup" },
            ]}
          />

          {view === "simulator" ? (
            ready && stream ? (
              <StitchSimulator stream={stream} className="h-[560px]" />
            ) : ready ? (
              <div className="grid h-[560px] place-items-center rounded-xl border border-border/70">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <EmptyState
                className="h-[560px]"
                icon={<Layers className="size-6" />}
                title="No stitches yet"
                description="Choose a fabric and finished width on the right, then run the digitizer."
                action={
                  <Button onClick={digitize} loading={busy === "digitize"}>
                    <Wand2 />
                    Digitize now
                  </Button>
                }
              />
            )
          ) : null}

          {view === "analysis" ? <AnalysisPanel design={design} /> : null}

          {view === "mockup" ? (
            <MockupPanel
              designId={design.id}
              ready={ready}
              templates={reference?.mockup_templates ?? []}
              colors={reference?.garment_colors ?? []}
              url={mockupUrl}
              onRendered={setMockupUrl}
            />
          ) : null}
        </div>

        {/* ------------------------------------------------------------ panel */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Wand2 className="size-4" />
                Digitizing
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Field label="Fabric" help={fabricProfile?.notes}>
                <Select value={fabric} onChange={(event) => setFabric(event.target.value)}>
                  {(reference?.fabrics ?? []).map((item) => (
                    <option key={item.key} value={item.key}>
                      {item.name}
                    </option>
                  ))}
                </Select>
              </Field>

              {fabricProfile ? (
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <Detail label="Stabiliser" value={fabricProfile.stabilizer} />
                  <Detail label="Needle" value={fabricProfile.needle} />
                  <Detail label="Underlay" value={fabricProfile.underlay.map(humanize).join(", ") || "None"} />
                  <Detail label="Topping" value={fabricProfile.topping} />
                </div>
              ) : null}

              <Field label="Machine" help="Used for hoop and stitch-count pre-flight checks.">
                <Select value={machineId} onChange={(event) => setMachineId(event.target.value)}>
                  <option value="">No machine selected</option>
                  {machines.map((machine) => (
                    <option key={machine.id} value={machine.id}>
                      {machine.name} — {machine.hoop_width_mm}×{machine.hoop_height_mm} mm
                    </option>
                  ))}
                </Select>
              </Field>

              <Field label="Finished width" hint={`${width} mm`}>
                <Slider value={width} min={20} max={400} step={1} onValueChange={setWidth} />
              </Field>

              <Field
                label="Density"
                hint={density === 1 ? "Standard" : density < 1 ? "Tighter" : "Opener"}
                help="Below 1 packs stitches closer; above 1 opens the fill up."
              >
                <Slider value={density} min={0.6} max={1.8} step={0.05} onValueChange={setDensity} />
              </Field>

              <Field label="Fill angle" hint={`${angle}°`}>
                <Slider value={angle} min={0} max={180} step={5} onValueChange={setAngle} />
              </Field>

              <Field label="Colour limit" help="0 keeps every colour in the artwork.">
                <Input
                  type="number"
                  min={0}
                  max={24}
                  value={maxColors}
                  onChange={(event) => setMaxColors(Number(event.target.value))}
                />
              </Field>

              <Switch
                checked={underlay}
                onCheckedChange={setUnderlay}
                label="Automatic underlay"
                description="Foundation stitching. Turning this off will pucker most fabrics."
              />
              <Switch
                checked={autoSatin}
                onCheckedChange={setAutoSatin}
                label="Automatic satin columns"
                description="Narrow shapes sew as satin instead of fill."
              />

              <Button className="w-full" onClick={digitize} loading={busy === "digitize"} size="lg">
                <Sparkles />
                {ready ? "Re-digitize" : "Digitize design"}
              </Button>
            </CardContent>
          </Card>

          {design.issues?.length ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <AlertTriangle className="size-4" />
                  Pre-flight
                </CardTitle>
              </CardHeader>
              <CardContent>
                <IssueList issues={design.issues} />
              </CardContent>
            </Card>
          ) : null}

          {design.thread_chart?.length ? (
            <Card>
              <CardHeader>
                <CardTitle>Thread chart</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5">
                {design.thread_chart.map((color) => (
                  <div
                    key={color.index}
                    className="flex items-center gap-2 rounded-lg border border-border/60 p-2"
                  >
                    <span
                      className="grid size-7 shrink-0 place-items-center rounded-md text-[10px] font-bold"
                      style={{ backgroundColor: color.hex, color: contrastText(color.hex) }}
                    >
                      {color.index + 1}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">{color.name}</span>
                      <span className="block truncate text-[11px] text-muted-foreground">
                        {color.code} · {color.technique}
                      </span>
                    </span>
                    <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                      {formatNumber(color.stitches)}
                    </span>
                  </div>
                ))}
              </CardContent>
            </Card>
          ) : null}

          {ready ? <PricingPanel designId={design.id} currency={me?.organization.currency ?? "USD"} /> : null}
        </div>
      </div>

      <ExportDialog
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        design={design}
        machines={machines}
        formats={reference?.formats ?? []}
        allowed={me?.subscription.plan.export_formats ?? ["dst", "pes"]}
      />
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-secondary/40 px-2 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="truncate text-xs font-medium" title={value}>
        {value}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ analysis
function AnalysisPanel({ design }: { design: Design }) {
  const analysis = design.analysis && "compatibility_score" in design.analysis ? design.analysis : null;
  if (!analysis) {
    return <EmptyState icon={<ScanEye className="size-6" />} title="Not analyzed yet" />;
  }

  const score = analysis.compatibility_score;
  const detected = analysis.detected as Record<string, unknown>;

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="p-5">
          <div className="flex flex-wrap items-center gap-4">
            <div className="relative grid size-24 shrink-0 place-items-center">
              <svg viewBox="0 0 100 100" className="absolute inset-0 -rotate-90">
                <circle cx="50" cy="50" r="42" fill="none" stroke="hsl(var(--secondary))" strokeWidth="10" />
                <circle
                  cx="50" cy="50" r="42" fill="none" strokeWidth="10" strokeLinecap="round"
                  stroke={score >= 85 ? "hsl(var(--success))" : score >= 60 ? "hsl(var(--warning))" : "hsl(var(--destructive))"}
                  strokeDasharray={`${(score / 100) * 264} 264`}
                />
              </svg>
              <span className="text-xl font-semibold tabular-nums">{score}%</span>
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium">Embroidery compatibility</p>
              <p className="mt-1 text-sm text-muted-foreground">{analysis.verdict}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Detected</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5 text-sm">
            {Object.entries(detected)
              .filter(([, value]) => value !== null && value !== undefined && value !== "")
              .map(([key, value]) => (
                <div key={key} className="flex items-start justify-between gap-3 border-b border-border/40 pb-1.5">
                  <span className="text-muted-foreground">{humanize(key)}</span>
                  <span className="max-w-[60%] truncate text-right font-medium">
                    {typeof value === "boolean" ? (value ? "Yes" : "No") : String(value)}
                  </span>
                </div>
              ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Colours ({analysis.colors.length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {analysis.colors.slice(0, 10).map((color) => (
              <div key={color.hex} className="flex items-center gap-2">
                <span className="size-6 shrink-0 rounded-md border border-border/60" style={{ backgroundColor: color.hex }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium">
                    {color.thread?.name ?? color.hex}
                  </span>
                  <Progress value={color.share * 100} className="mt-1 h-1" />
                </span>
                <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground">
                  {(color.share * 100).toFixed(0)}%
                </span>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      {analysis.recommended_placements?.length ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Ruler className="size-4" />
              Recommended placements
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-2 sm:grid-cols-2">
            {analysis.recommended_placements.map((placement) => (
              <div key={placement.key} className="rounded-lg border border-border/60 p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium">{placement.name}</p>
                  <Badge variant={placement.score >= 80 ? "success" : placement.score >= 55 ? "warning" : "destructive"}>
                    {placement.score}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {placement.width_mm} × {placement.height_mm} mm
                </p>
                {placement.notes.map((note) => (
                  <p key={note} className="mt-1 text-xs text-muted-foreground">
                    {note}
                  </p>
                ))}
              </div>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {analysis.recommendations?.length ? (
        <Card>
          <CardHeader>
            <CardTitle>Recommendations</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5 text-sm text-muted-foreground">
              {analysis.recommendations.map((item) => (
                <li key={item} className="flex gap-2">
                  <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary" />
                  {item}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {analysis.issues?.length ? <IssueList issues={analysis.issues} /> : null}
    </div>
  );
}

// -------------------------------------------------------------------- mockup
function MockupPanel({
  designId,
  ready,
  templates,
  colors,
  url,
  onRendered,
}: {
  designId: string;
  ready: boolean;
  templates: { key: string; name: string; placement: string }[];
  colors: { key: string; hex: string; name: string }[];
  url: string | null;
  onRendered: (url: string) => void;
}) {
  const toast = useToast();
  const [template, setTemplate] = React.useState(templates[0]?.key ?? "tshirt_left_chest");
  const [color, setColor] = React.useState("black");
  const [busy, setBusy] = React.useState(false);

  const render = async () => {
    setBusy(true);
    try {
      const blob = await api.mockup(designId, { template, garment_color: color });
      onRendered(URL.createObjectURL(blob));
    } catch (exception) {
      toast.error(
        "Could not render the mockup",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    } finally {
      setBusy(false);
    }
  };

  if (!ready) {
    return (
      <EmptyState
        className="h-[560px]"
        icon={<Shirt className="size-6" />}
        title="Digitize first"
        description="Mockups render the actual stitches, so the design has to be digitized."
      />
    );
  }

  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap gap-2">
          <Select value={template} onChange={(event) => setTemplate(event.target.value)} className="w-52">
            {templates.map((item) => (
              <option key={item.key} value={item.key}>
                {item.name}
              </option>
            ))}
          </Select>
          <Select value={color} onChange={(event) => setColor(event.target.value)} className="w-40">
            {colors.map((item) => (
              <option key={item.key} value={item.key}>
                {item.name}
              </option>
            ))}
          </Select>
          <Button onClick={render} loading={busy}>
            <Shirt />
            Render mockup
          </Button>
        </div>

        <div className="grid min-h-[420px] place-items-center rounded-xl border border-border/70 bg-secondary/20">
          {url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={url} alt="Garment mockup" className="max-h-[460px] rounded-lg object-contain" />
          ) : (
            <p className="text-sm text-muted-foreground">
              Choose a garment and colour, then render.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------- pricing
function PricingPanel({ designId, currency }: { designId: string; currency: string }) {
  const [quantity, setQuantity] = React.useState(24);
  const [blankCost, setBlankCost] = React.useState(4.25);
  const [result, setResult] = React.useState<PricingResult | null>(null);
  const [busy, setBusy] = React.useState(false);

  const quote = React.useCallback(async () => {
    setBusy(true);
    try {
      setResult(await api.quote({ design_id: designId, quantity, blank_cost: blankCost }));
    } catch {
      setResult(null);
    } finally {
      setBusy(false);
    }
  }, [designId, quantity, blankCost]);

  React.useEffect(() => {
    const timer = setTimeout(() => void quote(), 400);
    return () => clearTimeout(timer);
  }, [quote]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Pricing assistant</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <Field label="Quantity">
            <Input
              type="number"
              min={1}
              value={quantity}
              onChange={(event) => setQuantity(Math.max(1, Number(event.target.value)))}
            />
          </Field>
          <Field label="Blank cost">
            <Input
              type="number"
              min={0}
              step="0.01"
              value={blankCost}
              onChange={(event) => setBlankCost(Number(event.target.value))}
            />
          </Field>
        </div>

        {busy ? (
          <Skeleton className="h-24" />
        ) : result ? (
          <>
            <div className="grid grid-cols-3 gap-2 text-center">
              <PriceTile label="Cost" value={formatMoney(result.unit_cost, currency)} />
              <PriceTile label="Wholesale" value={formatMoney(result.unit_wholesale, currency)} accent />
              <PriceTile label="Retail" value={formatMoney(result.unit_retail, currency)} />
            </div>
            <p className="text-xs text-muted-foreground">
              {formatMoney(result.total_wholesale, currency)} for {quantity} ·{" "}
              {result.margins.wholesale_margin_pct}% margin ·{" "}
              {formatMoney(result.margins.wholesale_profit_total, currency)} profit
            </p>
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer select-none hover:text-foreground">
                How this was calculated
              </summary>
              <ul className="mt-2 space-y-1">
                {result.assumptions.map((line) => (
                  <li key={line}>· {line}</li>
                ))}
              </ul>
            </details>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">Digitize the design to price it.</p>
        )}
      </CardContent>
    </Card>
  );
}

function PriceTile({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={cn("rounded-lg border p-2", accent ? "border-primary/40 bg-primary/10" : "border-border/60")}>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold tabular-nums">{value}</p>
    </div>
  );
}

// -------------------------------------------------------------------- export
function ExportDialog({
  open,
  onClose,
  design,
  machines,
  formats,
  allowed,
}: {
  open: boolean;
  onClose: () => void;
  design: Design;
  machines: Machine[];
  formats: { extension: string; name: string; vendor: string; available: boolean; notes: string }[];
  allowed: string[];
}) {
  const toast = useToast();
  const [selected, setSelected] = React.useState<string[]>(["dst", "pes"]);
  const [machineId, setMachineId] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const toggle = (extension: string) =>
    setSelected((current) =>
      current.includes(extension)
        ? current.filter((value) => value !== extension)
        : [...current, extension],
    );

  const run = async () => {
    setBusy(true);
    try {
      const blob = await api.exportDesign(design.id, {
        formats: selected,
        machine_id: machineId || null,
      });
      downloadBlob(blob, `${design.name.replace(/\s+/g, "_")}_package.zip`);
      toast.success("Export package downloaded");
      onClose();
    } catch (exception) {
      const message = exception instanceof ApiError ? exception.message : "Export failed.";
      toast.error(
        exception instanceof ApiError && exception.isUpgradeRequired ? "Upgrade required" : "Export failed",
        message,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Export production package"
      description="A ZIP containing the stitch files, thread chart, run sheet and preview."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={run} loading={busy} disabled={!selected.length}>
            <Download />
            Download
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-2 sm:grid-cols-2">
          {formats.map((format) => {
            const locked = !allowed.includes(format.extension);
            const active = selected.includes(format.extension);
            return (
              <button
                key={format.extension}
                disabled={!format.available || locked}
                onClick={() => toggle(format.extension)}
                className={cn(
                  "rounded-lg border p-3 text-left transition-colors",
                  active ? "border-primary bg-primary/10" : "border-border/70 hover:bg-secondary/40",
                  (!format.available || locked) && "cursor-not-allowed opacity-50",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-sm font-semibold uppercase">.{format.extension}</span>
                  {locked ? (
                    <Badge variant="outline">Pro</Badge>
                  ) : !format.available ? (
                    <Badge variant="outline">N/A</Badge>
                  ) : active ? (
                    <Badge variant="success">Selected</Badge>
                  ) : null}
                </div>
                <p className="mt-0.5 text-xs text-muted-foreground">{format.vendor}</p>
                <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{format.notes}</p>
              </button>
            );
          })}
        </div>

        <Field label="Machine" help="Adds the setup details to the production notes.">
          <Select value={machineId} onChange={(event) => setMachineId(event.target.value)}>
            <option value="">No machine</option>
            {machines.map((machine) => (
              <option key={machine.id} value={machine.id}>
                {machine.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Dialog>
  );
}
