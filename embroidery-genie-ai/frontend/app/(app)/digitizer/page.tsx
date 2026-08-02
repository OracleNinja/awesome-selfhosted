"use client";

/**
 * AI Digitizer — the "Create New Embroidery Design" entry point.
 *
 * Three routes into the pipeline: upload artwork, paste an SVG, or set
 * lettering. All three land on the same design record, which the studio then
 * digitizes.
 */

import { motion } from "framer-motion";
import {
  FileImage,
  FileUp,
  Loader2,
  Sparkles,
  Type as TypeIcon,
  Upload,
} from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { useSession } from "@/app/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, Badge, IssueList, Spinner } from "@/components/ui/feedback";
import { Field, Input, Select, Slider, Switch, Textarea } from "@/components/ui/form";
import { useToast } from "@/components/ui/overlay";
import { Tabs } from "@/components/ui/tabs";
import { api, ApiError } from "@/lib/api";
import { cn, formatNumber } from "@/lib/utils";

const ACCEPT = ".png,.jpg,.jpeg,.webp,.bmp,.heic,.heif,.svg,image/*";

export default function DigitizerPage() {
  const router = useRouter();
  const toast = useToast();
  const { reference, refresh } = useSession();
  const [mode, setMode] = React.useState("upload");

  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Create new embroidery design</h1>
        <p className="text-sm text-muted-foreground">
          Upload artwork or set lettering. The analyzer reports how well it will embroider before
          anything is digitized.
        </p>
      </div>

      <Tabs
        value={mode}
        onValueChange={setMode}
        tabs={[
          { value: "upload", label: "Upload artwork" },
          { value: "svg", label: "Paste SVG" },
          { value: "text", label: "Create text design" },
        ]}
      />

      {mode === "upload" ? (
        <UploadPanel
          onCreated={(id) => {
            void refresh();
            router.push(`/studio/${id}`);
          }}
          toast={toast}
        />
      ) : null}
      {mode === "svg" ? (
        <SvgPanel
          onCreated={(id) => {
            void refresh();
            router.push(`/studio/${id}`);
          }}
          toast={toast}
        />
      ) : null}
      {mode === "text" ? (
        <TextPanel
          fonts={reference?.fonts ?? []}
          healthy={reference?.fonts_healthy ?? false}
          onCreated={(id) => {
            void refresh();
            router.push(`/studio/${id}`);
          }}
          toast={toast}
        />
      ) : null}
    </div>
  );
}

type Toast = ReturnType<typeof useToast>;

// -------------------------------------------------------------------- upload
function UploadPanel({ onCreated, toast }: { onCreated: (id: string) => void; toast: Toast }) {
  const [file, setFile] = React.useState<File | null>(null);
  const [name, setName] = React.useState("");
  const [useAi, setUseAi] = React.useState(true);
  const [dragging, setDragging] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [preview, setPreview] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!file) {
      setPreview(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const accept = (incoming: File | null) => {
    if (!incoming) return;
    setFile(incoming);
    if (!name) setName(incoming.name.replace(/\.[^.]+$/, ""));
  };

  const submit = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const design = await api.uploadDesign(file, {
        name: name || file.name,
        analyze: "true",
        use_ai: String(useAi),
      });
      toast.success("Artwork analyzed", `Compatibility ${design.compatibility_score ?? "—"}%`);
      onCreated(design.id);
    } catch (exception) {
      const message = exception instanceof ApiError ? exception.message : "Upload failed.";
      toast.error("Could not create the design", message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardContent className="space-y-4 p-5">
        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragging(false);
            accept(event.dataTransfer.files?.[0] ?? null);
          }}
          className={cn(
            "relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed p-10 text-center transition-colors",
            dragging ? "border-primary bg-primary/5" : "border-border/70",
          )}
        >
          {preview ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview}
              alt="Artwork preview"
              className="mb-4 max-h-48 rounded-lg border border-border/70 bg-white object-contain p-2"
            />
          ) : (
            <motion.span
              animate={{ y: dragging ? -4 : 0 }}
              className="mb-3 grid size-14 place-items-center rounded-2xl bg-secondary/70"
            >
              <Upload className="size-6 text-muted-foreground" />
            </motion.span>
          )}
          <p className="text-sm font-medium">
            {file ? file.name : "Drop artwork here, or choose a file"}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            PNG · JPG · HEIC · BMP · WEBP · SVG — up to 25 MB
          </p>
          <input
            type="file"
            accept={ACCEPT}
            onChange={(event) => accept(event.target.files?.[0] ?? null)}
            className="absolute inset-0 cursor-pointer opacity-0"
            aria-label="Choose artwork"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Design name">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Northside Athletics — left chest"
            />
          </Field>
          <Switch
            checked={useAi}
            onCheckedChange={setUseAi}
            label="Use AI vision analysis"
            description="Describes the artwork and flags small lettering. Measurements are always computed locally."
          />
        </div>

        <Button className="w-full" onClick={submit} disabled={!file} loading={busy} size="lg">
          <Sparkles />
          Analyze and continue
        </Button>
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------------- svg
function SvgPanel({ onCreated, toast }: { onCreated: (id: string) => void; toast: Toast }) {
  const [svg, setSvg] = React.useState("");
  const [name, setName] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const submit = async () => {
    if (!svg.trim().startsWith("<svg")) {
      toast.error("That does not look like SVG", "Paste the full <svg>…</svg> markup.");
      return;
    }
    setBusy(true);
    try {
      const file = new File([svg], `${(name || "design").replace(/\s+/g, "_")}.svg`, {
        type: "image/svg+xml",
      });
      const design = await api.uploadDesign(file, { name: name || "Vector design", analyze: "true" });
      toast.success("Vector imported");
      onCreated(design.id);
    } catch (exception) {
      toast.error(
        "Could not import the SVG",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileUp className="size-4" />
          Paste SVG markup
        </CardTitle>
        <CardDescription>
          Vector artwork traces exactly — no raster loss. Convert live text to outlines first, since
          font data is not embedded in SVG.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Field label="Design name">
          <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Club crest" />
        </Field>
        <Field label="SVG">
          <Textarea
            value={svg}
            onChange={(event) => setSvg(event.target.value)}
            rows={10}
            spellCheck={false}
            placeholder={'<svg xmlns="http://www.w3.org/2000/svg" width="60mm" height="40mm" …>'}
            className="font-mono text-xs"
          />
        </Field>
        <Button className="w-full" onClick={submit} loading={busy} disabled={!svg.trim()}>
          <FileImage />
          Import vector
        </Button>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------- text
function TextPanel({
  fonts,
  healthy,
  onCreated,
  toast,
}: {
  fonts: { key: string; name: string; notes: string; available: boolean }[];
  healthy: boolean;
  onCreated: (id: string) => void;
  toast: Toast;
}) {
  const [text, setText] = React.useState("Northside");
  const [fontKey, setFontKey] = React.useState("sans_bold");
  const [height, setHeight] = React.useState(25);
  const [arc, setArc] = React.useState(0);
  const [color, setColor] = React.useState("#F5C518");
  const [align, setAlign] = React.useState("center");
  const [busy, setBusy] = React.useState(false);
  const [previewing, setPreviewing] = React.useState(false);
  const [preview, setPreview] = React.useState<{
    stitch_count: number;
    color_count: number;
    width_mm: number;
    height_mm: number;
    issues: { level: string; code: string; message: string }[];
    minimum_height_mm: number;
  } | null>(null);

  // Debounced live preview: the engine call is real work, so wait for a pause
  // in typing rather than firing on every keystroke.
  React.useEffect(() => {
    if (!text.trim()) {
      setPreview(null);
      return;
    }
    const timer = setTimeout(async () => {
      setPreviewing(true);
      try {
        const result = await api.textPreview({
          text,
          font_key: fontKey,
          height_mm: height,
          arc_degrees: arc,
          color,
          align,
        });
        setPreview(result as never);
      } catch {
        setPreview(null);
      } finally {
        setPreviewing(false);
      }
    }, 700);
    return () => clearTimeout(timer);
  }, [text, fontKey, height, arc, color, align]);

  const submit = async () => {
    setBusy(true);
    try {
      const design = await api.createTextDesign({
        text,
        font_key: fontKey,
        height_mm: height,
        arc_degrees: arc,
        color,
        align,
      });
      toast.success("Lettering created");
      onCreated(design.id);
    } catch (exception) {
      toast.error(
        "Could not create the lettering",
        exception instanceof ApiError ? exception.message : "Unknown error.",
      );
    } finally {
      setBusy(false);
    }
  };

  if (!healthy) {
    return (
      <Alert level="error" title="No fonts installed on the server">
        Text designs render type to outlines using a TrueType font. Install one on the API host
        (Debian/Ubuntu: <code className="font-mono">apt-get install fonts-dejavu-core</code>) and
        reload.
      </Alert>
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_320px]">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <TypeIcon className="size-4" />
            Lettering
          </CardTitle>
          <CardDescription>
            Type is rendered to outlines and stitched as satin columns, the same route a digitizer
            takes by hand.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field label="Text" help="One line per row. Keep it short — embroidery is not a poster.">
            <Textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={3}
              maxLength={200}
            />
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Font">
              <Select value={fontKey} onChange={(event) => setFontKey(event.target.value)}>
                {fonts.map((font) => (
                  <option key={font.key} value={font.key} disabled={!font.available}>
                    {font.name}
                    {font.available ? "" : " (unavailable)"}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Alignment">
              <Select value={align} onChange={(event) => setAlign(event.target.value)}>
                <option value="left">Left</option>
                <option value="center">Center</option>
                <option value="right">Right</option>
              </Select>
            </Field>
          </div>

          <Field
            label="Cap height"
            hint={`${height} mm`}
            help={
              preview && height < preview.minimum_height_mm
                ? `Below the ${preview.minimum_height_mm} mm minimum for this face — letters will close up.`
                : "Letters under about 5 mm close up on most fabrics."
            }
          >
            <Slider value={height} min={4} max={120} step={1} onValueChange={setHeight} />
          </Field>

          <Field label="Baseline arc" hint={`${arc}°`} help="Bends the text for caps and left chest.">
            <Slider value={arc} min={-90} max={90} step={5} onValueChange={setArc} />
          </Field>

          <Field label="Thread colour">
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={color}
                onChange={(event) => setColor(event.target.value)}
                className="h-10 w-14 cursor-pointer rounded-lg border border-border bg-transparent"
                aria-label="Thread colour"
              />
              <Input value={color} onChange={(event) => setColor(event.target.value)} className="font-mono" />
            </div>
          </Field>

          <Button className="w-full" onClick={submit} loading={busy} disabled={!text.trim()}>
            <Sparkles />
            Create lettering design
          </Button>
        </CardContent>
      </Card>

      <Card className="h-fit">
        <CardHeader className="flex-row items-center justify-between">
          <CardTitle>Live estimate</CardTitle>
          {previewing ? <Spinner /> : null}
        </CardHeader>
        <CardContent className="space-y-3">
          {preview ? (
            <>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <Metric label="Stitches" value={formatNumber(preview.stitch_count)} />
                <Metric label="Colours" value={String(preview.color_count)} />
                <Metric label="Width" value={`${preview.width_mm} mm`} />
                <Metric label="Height" value={`${preview.height_mm} mm`} />
              </div>
              {preview.issues?.length ? (
                <IssueList issues={preview.issues as never} />
              ) : (
                <Badge variant="success">No pre-flight warnings</Badge>
              )}
            </>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              Type something to see the stitch estimate.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-secondary/40 p-2">
      <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="text-sm font-semibold tabular-nums">{value}</p>
    </div>
  );
}
