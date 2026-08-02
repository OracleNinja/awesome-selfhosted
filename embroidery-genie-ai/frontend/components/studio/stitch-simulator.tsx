"use client";

/**
 * 3D stitch simulator.
 *
 * Renders the stitch stream on a canvas with thread shading, a fabric ground,
 * and stitch-by-stitch playback so an operator can watch the design sew before
 * it ever reaches a machine.
 *
 * Performance note: a design can carry 100k+ segments, so the completed
 * portion is drawn *incrementally* onto a persistent canvas rather than
 * redrawn every frame. A full repaint only happens when the view changes
 * (zoom, rotate, tilt, layer toggle) or when the playhead moves backwards.
 */

import { motion } from "framer-motion";
import {
  Boxes,
  Eye,
  EyeOff,
  Gauge,
  Maximize2,
  Pause,
  Play,
  RotateCcw,
  RotateCw,
  Shirt,
  SkipForward,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/feedback";
import { Select } from "@/components/ui/form";
import type { StitchStream } from "@/lib/api";
import { cn, contrastText, formatNumber } from "@/lib/utils";

const FABRICS: { key: string; label: string; color: string; weave: string }[] = [
  { key: "white", label: "White", color: "#F4F4F2", weave: "#E4E4E0" },
  { key: "natural", label: "Natural", color: "#E6DFCE", weave: "#D6CDB8" },
  { key: "grey", label: "Sport grey", color: "#A9ADB0", weave: "#989DA1" },
  { key: "navy", label: "Navy", color: "#1B2A47", weave: "#16233B" },
  { key: "black", label: "Black", color: "#141416", weave: "#1E1E21" },
  { key: "red", label: "Red", color: "#9E2126", weave: "#8A1C21" },
  { key: "forest", label: "Forest", color: "#1F4632", weave: "#193A2A" },
];

const SPEEDS = [
  { label: "0.5×", value: 0.5 },
  { label: "1×", value: 1 },
  { label: "4×", value: 4 },
  { label: "16×", value: 16 },
  { label: "64×", value: 64 },
];

// Flags mirror app/embroidery/pattern.py Command.
const CMD_STITCH = 0;
const CMD_JUMP = 1;
const CMD_TRIM = 2;

type Segment = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  block: number;
  jump: boolean;
};

type View = {
  zoom: number;
  rotation: number;
  tilt: number;
  panX: number;
  panY: number;
};

function shade(hex: string, factor: number) {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  const clamp = (n: number) => Math.max(0, Math.min(255, Math.round(n)));
  return `rgb(${clamp(r * factor)}, ${clamp(g * factor)}, ${clamp(b * factor)})`;
}

export function StitchSimulator({
  stream,
  className,
  compact = false,
}: {
  stream: StitchStream;
  className?: string;
  compact?: boolean;
}) {
  const canvasRef = React.useRef<HTMLCanvasElement | null>(null);
  const containerRef = React.useRef<HTMLDivElement | null>(null);

  const [view, setView] = React.useState<View>({
    zoom: 1,
    rotation: 0,
    tilt: 0,
    panX: 0,
    panY: 0,
  });
  const [fabric, setFabric] = React.useState(FABRICS[4]);
  const [playing, setPlaying] = React.useState(false);
  const [speed, setSpeed] = React.useState(16);
  const [progress, setProgress] = React.useState(1); // 0..1 of total segments
  const [showJumps, setShowJumps] = React.useState(false);
  const [hidden, setHidden] = React.useState<Set<number>>(new Set());
  const [size, setSize] = React.useState({ width: 800, height: 600 });

  // ----------------------------------------------------------- flatten once
  const segments = React.useMemo<Segment[]>(() => {
    const out: Segment[] = [];
    stream.blocks.forEach((block, blockIndex) => {
      let previousX: number | null = null;
      let previousY: number | null = null;
      for (let i = 0; i < block.flags.length; i += 1) {
        const x = block.coords[i * 2];
        const y = block.coords[i * 2 + 1];
        const flag = block.flags[i];
        if (previousX !== null && previousY !== null) {
          if (flag === CMD_STITCH) {
            out.push({ x1: previousX, y1: previousY, x2: x, y2: y, block: blockIndex, jump: false });
          } else if (flag === CMD_JUMP || flag === CMD_TRIM) {
            out.push({ x1: previousX, y1: previousY, x2: x, y2: y, block: blockIndex, jump: true });
          }
        }
        previousX = x;
        previousY = y;
      }
    });
    return out;
  }, [stream]);

  const stitchTotal = React.useMemo(
    () => segments.filter((segment) => !segment.jump).length,
    [segments],
  );

  const visibleSegments = React.useMemo(
    () => segments.filter((segment) => !hidden.has(segment.block) && (showJumps || !segment.jump)),
    [segments, hidden, showJumps],
  );

  // ---------------------------------------------------------------- sizing
  React.useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0].contentRect;
      setSize({ width: Math.max(320, rect.width), height: Math.max(240, rect.height) });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // ------------------------------------------------------------- transform
  const transform = React.useMemo(() => {
    const { minX, minY, maxX, maxY } = stream.bounds;
    const designWidth = Math.max(1, maxX - minX);
    const designHeight = Math.max(1, maxY - minY);
    const padding = 48;
    const base = Math.min(
      (size.width - padding * 2) / designWidth,
      (size.height - padding * 2) / designHeight,
    );
    return {
      scale: base * view.zoom,
      centerX: (minX + maxX) / 2,
      centerY: (minY + maxY) / 2,
    };
  }, [stream.bounds, size, view.zoom]);

  const project = React.useCallback(
    (x: number, y: number): [number, number] => {
      const dx = (x - transform.centerX) * transform.scale;
      const dy = (y - transform.centerY) * transform.scale;
      const cos = Math.cos(view.rotation);
      const sin = Math.sin(view.rotation);
      const rx = dx * cos - dy * sin;
      const ry = dx * sin + dy * cos;
      // Tilt foreshortens the vertical axis, which reads as looking across
      // the garment rather than straight down at it.
      const tilted = ry * Math.cos(view.tilt);
      return [size.width / 2 + rx + view.panX, size.height / 2 + tilted + view.panY];
    },
    [transform, view, size],
  );

  // ------------------------------------------------------------- rendering
  const drawnCount = React.useRef(0);

  const paintBackground = React.useCallback(
    (ctx: CanvasRenderingContext2D) => {
      ctx.fillStyle = fabric.color;
      ctx.fillRect(0, 0, size.width, size.height);

      // Woven texture: two low-contrast line grids.
      ctx.save();
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = fabric.weave;
      ctx.lineWidth = 1;
      for (let x = 0; x < size.width; x += 4) {
        ctx.beginPath();
        ctx.moveTo(x + 0.5, 0);
        ctx.lineTo(x + 0.5, size.height);
        ctx.stroke();
      }
      for (let y = 0; y < size.height; y += 4) {
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(size.width, y + 0.5);
        ctx.stroke();
      }
      ctx.restore();

      // Vignette, so the hoop area reads as lit from the front.
      const gradient = ctx.createRadialGradient(
        size.width / 2, size.height / 2, Math.min(size.width, size.height) * 0.2,
        size.width / 2, size.height / 2, Math.max(size.width, size.height) * 0.75,
      );
      gradient.addColorStop(0, "rgba(255,255,255,0.06)");
      gradient.addColorStop(1, "rgba(0,0,0,0.35)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, size.width, size.height);
    },
    [fabric, size],
  );

  const drawSegments = React.useCallback(
    (ctx: CanvasRenderingContext2D, from: number, to: number) => {
      const threadWidth = Math.max(1.1, 0.42 * transform.scale * 10);

      for (let i = from; i < to; i += 1) {
        const segment = visibleSegments[i];
        if (!segment) continue;
        const block = stream.blocks[segment.block];
        const [x1, y1] = project(segment.x1, segment.y1);
        const [x2, y2] = project(segment.x2, segment.y2);

        if (segment.jump) {
          ctx.save();
          ctx.strokeStyle = "rgba(255,255,255,0.45)";
          ctx.setLineDash([3, 4]);
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(x1, y1);
          ctx.lineTo(x2, y2);
          ctx.stroke();
          ctx.restore();
          continue;
        }

        const color = block?.color ?? "#888888";

        // Shadow under the thread gives it height off the fabric.
        ctx.strokeStyle = "rgba(0,0,0,0.35)";
        ctx.lineWidth = threadWidth * 1.35;
        ctx.lineCap = "round";
        ctx.beginPath();
        ctx.moveTo(x1 + 1.2, y1 + 1.8);
        ctx.lineTo(x2 + 1.2, y2 + 1.8);
        ctx.stroke();

        // Thread body.
        ctx.strokeStyle = color;
        ctx.lineWidth = threadWidth;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();

        // Specular streak along the top edge of the floss.
        const dx = x2 - x1;
        const dy = y2 - y1;
        const length = Math.hypot(dx, dy);
        if (length > 0.6 && threadWidth > 2) {
          const nx = -dy / length;
          const ny = dx / length;
          const offset = threadWidth * 0.22;
          ctx.strokeStyle = shade(color, 1.5);
          ctx.globalAlpha = 0.6;
          ctx.lineWidth = Math.max(0.8, threadWidth * 0.3);
          ctx.beginPath();
          ctx.moveTo(x1 + nx * offset, y1 + ny * offset);
          ctx.lineTo(x2 + nx * offset, y2 + ny * offset);
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
      }
    },
    [visibleSegments, stream.blocks, project, transform.scale],
  );

  const repaint = React.useCallback(
    (count: number) => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (!canvas || !ctx) return;
      paintBackground(ctx);
      drawSegments(ctx, 0, count);
      drawnCount.current = count;
    },
    [paintBackground, drawSegments],
  );

  // Full repaint when the view, layers or fabric change.
  React.useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = size.width * dpr;
    canvas.height = size.height * dpr;
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    const ctx = canvas.getContext("2d");
    ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
    repaint(Math.round(progress * visibleSegments.length));
    // `progress` is deliberately excluded: playback is handled incrementally
    // below so we do not repaint the whole design on every frame.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size, view, fabric, hidden, showJumps, repaint, visibleSegments.length]);

  // Incremental draw as the playhead advances.
  React.useEffect(() => {
    const target = Math.round(progress * visibleSegments.length);
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!ctx) return;
    if (target < drawnCount.current) {
      repaint(target);
      return;
    }
    if (target > drawnCount.current) {
      drawSegments(ctx, drawnCount.current, target);
      drawnCount.current = target;
    }
  }, [progress, visibleSegments.length, drawSegments, repaint]);

  // --------------------------------------------------------------- playback
  React.useEffect(() => {
    if (!playing) return;
    let frame = 0;
    let last = performance.now();

    const tick = (now: number) => {
      const elapsed = (now - last) / 1000;
      last = now;
      // 800 stitches per minute is a realistic head speed; speed multiplies it.
      const perSecond = (800 / 60) * speed;
      setProgress((current) => {
        const next = current + (perSecond * elapsed) / Math.max(1, visibleSegments.length);
        if (next >= 1) {
          setPlaying(false);
          return 1;
        }
        return next;
      });
      frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, speed, visibleSegments.length]);

  // ------------------------------------------------------------ interaction
  const dragState = React.useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    (event.target as HTMLCanvasElement).setPointerCapture(event.pointerId);
    dragState.current = { x: event.clientX, y: event.clientY, panX: view.panX, panY: view.panY };
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const state = dragState.current;
    if (!state) return;
    setView((current) => ({
      ...current,
      panX: state.panX + (event.clientX - state.x),
      panY: state.panY + (event.clientY - state.y),
    }));
  };

  const onPointerUp = () => {
    dragState.current = null;
  };

  const onWheel = (event: React.WheelEvent<HTMLCanvasElement>) => {
    const delta = event.deltaY > 0 ? 0.9 : 1.1;
    setView((current) => ({
      ...current,
      zoom: Math.max(0.2, Math.min(24, current.zoom * delta)),
    }));
  };

  const reset = () =>
    setView({ zoom: 1, rotation: 0, tilt: 0, panX: 0, panY: 0 });

  const toggleBlock = (index: number) =>
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const currentStitch = Math.round(progress * stitchTotal);
  const activeBlock = React.useMemo(() => {
    const index = Math.round(progress * visibleSegments.length) - 1;
    return visibleSegments[Math.max(0, index)]?.block ?? 0;
  }, [progress, visibleSegments]);

  return (
    <div className={cn("flex h-full flex-col overflow-hidden rounded-xl border border-border/70 bg-card", className)}>
      {/* ------------------------------------------------------------- canvas */}
      <div ref={containerRef} className="relative min-h-[280px] flex-1 overflow-hidden">
        <canvas
          ref={canvasRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onWheel={onWheel}
          className="absolute inset-0 cursor-grab touch-none active:cursor-grabbing"
          role="img"
          aria-label={
            `Stitch preview: ${stitchTotal.toLocaleString()} stitches in ` +
            `${stream.blocks.length} colours. Drag to pan, scroll to zoom.`
          }
        />

        <div className="pointer-events-none absolute left-3 top-3 flex flex-wrap gap-1.5">
          <Badge variant="secondary" className="pointer-events-auto backdrop-blur">
            {formatNumber(currentStitch)} / {formatNumber(stitchTotal)} stitches
          </Badge>
          {stream.decimation > 1 ? (
            <Badge variant="outline" className="pointer-events-auto bg-card/80 backdrop-blur">
              preview at 1:{stream.decimation}
            </Badge>
          ) : null}
        </div>

        <div className="absolute right-3 top-3 flex flex-col gap-1.5">
          <Button size="icon-sm" variant="secondary" onClick={() => setView((v) => ({ ...v, zoom: Math.min(24, v.zoom * 1.25) }))} title="Zoom in">
            <ZoomIn />
          </Button>
          <Button size="icon-sm" variant="secondary" onClick={() => setView((v) => ({ ...v, zoom: Math.max(0.2, v.zoom / 1.25) }))} title="Zoom out">
            <ZoomOut />
          </Button>
          <Button size="icon-sm" variant="secondary" onClick={() => setView((v) => ({ ...v, rotation: v.rotation - Math.PI / 12 }))} title="Rotate left">
            <RotateCcw />
          </Button>
          <Button size="icon-sm" variant="secondary" onClick={() => setView((v) => ({ ...v, rotation: v.rotation + Math.PI / 12 }))} title="Rotate right">
            <RotateCw />
          </Button>
          <Button
            size="icon-sm"
            variant={view.tilt > 0 ? "default" : "secondary"}
            onClick={() => setView((v) => ({ ...v, tilt: v.tilt > 0 ? 0 : 0.9 }))}
            title="Toggle 3D tilt"
          >
            <Boxes />
          </Button>
          <Button size="icon-sm" variant="secondary" onClick={reset} title="Reset view">
            <Maximize2 />
          </Button>
        </div>
      </div>

      {/* ------------------------------------------------------------ controls */}
      <div className="space-y-3 border-t border-border/70 bg-card/80 p-3">
        <div className="flex items-center gap-2">
          <Button
            size="icon-sm"
            onClick={() => {
              if (progress >= 1) setProgress(0);
              setPlaying((current) => !current);
            }}
            title={playing ? "Pause" : "Play"}
          >
            {playing ? <Pause /> : <Play />}
          </Button>
          <Button size="icon-sm" variant="secondary" onClick={() => { setPlaying(false); setProgress(1); }} title="Jump to end">
            <SkipForward />
          </Button>

          <input
            type="range"
            min={0}
            max={1000}
            value={Math.round(progress * 1000)}
            onChange={(event) => {
              setPlaying(false);
              setProgress(Number(event.target.value) / 1000);
            }}
            className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-secondary [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary"
            aria-label="Stitch position"
          />

          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <Gauge className="size-3.5" />
            <Select
              value={String(speed)}
              onChange={(event) => setSpeed(Number(event.target.value))}
              className="h-8 w-20 py-0 text-xs"
              aria-label="Playback speed"
            >
              {SPEEDS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </Select>
          </div>
        </div>

        {!compact ? (
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
              <Shirt className="size-3.5 text-muted-foreground" />
              <Select
                value={fabric.key}
                onChange={(event) =>
                  setFabric(FABRICS.find((f) => f.key === event.target.value) ?? FABRICS[0])
                }
                className="h-8 w-32 py-0 text-xs"
                aria-label="Fabric colour"
              >
                {FABRICS.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.label}
                  </option>
                ))}
              </Select>
            </div>

            <Button
              size="sm"
              variant={showJumps ? "default" : "outline"}
              onClick={() => setShowJumps((current) => !current)}
            >
              {showJumps ? <Eye /> : <EyeOff />}
              Jumps
            </Button>

            <div className="flex flex-1 flex-wrap justify-end gap-1">
              {stream.blocks.map((block, index) => {
                const isHidden = hidden.has(index);
                const isActive = index === activeBlock && playing;
                return (
                  <motion.button
                    key={`${block.color}-${index}`}
                    onClick={() => toggleBlock(index)}
                    animate={isActive ? { scale: [1, 1.08, 1] } : { scale: 1 }}
                    transition={{ duration: 0.8, repeat: isActive ? Infinity : 0 }}
                    title={`${block.name}${block.code ? ` (${block.code})` : ""} — ${formatNumber(
                      block.coords.length / 2,
                    )} points · ${block.technique}`}
                    className={cn(
                      "flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-opacity",
                      isHidden ? "border-border opacity-40" : "border-transparent",
                    )}
                    style={
                      isHidden
                        ? undefined
                        : { backgroundColor: block.color, color: contrastText(block.color) }
                    }
                  >
                    <span
                      className="size-2 rounded-full ring-1 ring-black/20"
                      style={{ backgroundColor: block.color }}
                    />
                    {index + 1}
                  </motion.button>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
