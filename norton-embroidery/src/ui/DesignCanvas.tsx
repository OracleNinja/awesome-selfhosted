import { useCallback, useEffect, useRef, useState } from 'react';
import type { Stitch } from '../domain/stitch';
import type { Thread } from '../domain/thread';
import type { Hoop } from '../domain/machine';
import { unitsToInches, unitsToMm } from '../domain/units';
import {
  defaultViewport,
  fitViewport,
  render,
  screenToDesign,
  type Viewport,
} from './render/stitch-renderer';

export interface DesignCanvasProps {
  stitches: readonly Stitch[];
  threads: readonly Thread[];
  hoop: Hoop | null;
  /** -1 renders everything; otherwise renders the first N stitches. */
  upTo: number;
  highlightRange: { start: number; end: number } | null;
  /** Artwork preview mode shows the uploaded image instead of the stitches. */
  mode: 'stitch' | 'artwork';
  artworkUrl: string | null;
  stats: { width: number; height: number; stitchCount: number; colorCount: number };
}

export function DesignCanvas(props: DesignCanvasProps): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [viewport, setViewport] = useState<Viewport>(defaultViewport);
  const [panning, setPanning] = useState(false);
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [showJumps, setShowJumps] = useState(true);
  const [showGrid, setShowGrid] = useState(true);
  const [dark, setDark] = useState(false);
  const panStart = useRef<{ x: number; y: number; offsetX: number; offsetY: number } | null>(null);

  const sizeCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return { width: 0, height: 0 };
    const dpr = window.devicePixelRatio || 1;
    const width = container.clientWidth;
    const height = container.clientHeight;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(height * dpr));
    const ctx = canvas.getContext('2d');
    if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { width, height };
  }, []);

  const fitToHoop = useCallback(() => {
    const { width, height } = sizeCanvas();
    if (!width || !height) return;
    const box = props.hoop
      ? { minX: 0, minY: 0, maxX: props.hoop.width, maxY: props.hoop.height }
      : { minX: 0, minY: 0, maxX: 1300, maxY: 1800 };
    setViewport(fitViewport(box, width, height));
  }, [props.hoop, sizeCanvas]);

  const actualSize = useCallback(() => {
    // 1 design unit = 0.1 mm. A CSS pixel is 1/96 inch, so 1 mm ~= 3.7795 px.
    setViewport((v) => ({ ...v, scale: (96 / 25.4) / 10 }));
  }, []);

  // Initial fit, and refit on container resize.
  useEffect(() => {
    fitToHoop();
    const observer = new ResizeObserver(() => {
      sizeCanvas();
      redraw();
    });
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
    // Intentionally runs once: this sets up the initial fit and the resize
    // observer. Redraws on data changes are handled by the effect below.
  }, []);

  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    render(ctx, props.stitches, {
      viewport,
      threads: props.threads,
      hoop: props.hoop,
      upTo: props.upTo,
      showJumps,
      showPenetrations: true,
      showHoop: true,
      showSafeArea: true,
      showGrid,
      highlightRange: props.highlightRange,
      darkBackground: dark,
    });
  }, [props.stitches, props.threads, props.hoop, props.upTo, props.highlightRange, viewport, showJumps, showGrid, dark]);

  useEffect(() => {
    sizeCanvas();
    redraw();
  }, [redraw, sizeCanvas]);

  const onWheel = (e: React.WheelEvent<HTMLCanvasElement>): void => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    setViewport((v) => {
      const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
      const nextScale = Math.min(20, Math.max(0.05, v.scale * factor));
      // Keep the point under the cursor fixed while zooming.
      const before = screenToDesign({ x: px, y: py }, v);
      const after = screenToDesign({ x: px, y: py }, { ...v, scale: nextScale });
      return {
        scale: nextScale,
        offsetX: v.offsetX + (before.x - after.x),
        offsetY: v.offsetY + (before.y - after.y),
      };
    });
  };

  const onPointerDown = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    (e.target as HTMLCanvasElement).setPointerCapture(e.pointerId);
    panStart.current = { x: e.clientX, y: e.clientY, offsetX: viewport.offsetX, offsetY: viewport.offsetY };
    setPanning(true);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLCanvasElement>): void => {
    const canvas = canvasRef.current;
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      setCursor(screenToDesign({ x: e.clientX - rect.left, y: e.clientY - rect.top }, viewport));
    }
    if (!panning || !panStart.current) return;
    const start = panStart.current;
    setViewport((v) => ({
      ...v,
      offsetX: start.offsetX - (e.clientX - start.x) / v.scale,
      offsetY: start.offsetY - (e.clientY - start.y) / v.scale,
    }));
  };

  const onPointerUp = (): void => {
    setPanning(false);
    panStart.current = null;
  };

  const zoom = (factor: number): void =>
    setViewport((v) => ({ ...v, scale: Math.min(20, Math.max(0.05, v.scale * factor)) }));

  return (
    <div className="canvas-area" ref={containerRef}>
      <div className="canvas-overlay">
        <button onClick={fitToHoop} title="Fit the whole hoop in view">Fit to hoop</button>
        <button onClick={actualSize} title="Show the design at its physical size">Actual size</button>
        <button onClick={() => zoom(1.25)}>Zoom +</button>
        <button onClick={() => zoom(1 / 1.25)}>Zoom −</button>
        <button onClick={() => setShowJumps((s) => !s)}>{showJumps ? 'Hide jumps' : 'Show jumps'}</button>
        <button onClick={() => setShowGrid((s) => !s)}>{showGrid ? 'Hide grid' : 'Show grid'}</button>
        <button onClick={() => setDark((s) => !s)}>{dark ? 'Light' : 'Dark'}</button>
      </div>

      {props.mode === 'artwork' && props.artworkUrl ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: '#d9d6d1',
            zIndex: 1,
          }}
        >
          <img
            src={props.artworkUrl}
            alt="Original artwork"
            style={{ maxWidth: '85%', maxHeight: '85%', boxShadow: '0 2px 14px rgba(0,0,0,0.25)', background: '#fff' }}
          />
        </div>
      ) : null}

      <canvas
        ref={canvasRef}
        className={panning ? 'panning' : ''}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => {
          onPointerUp();
          setCursor(null);
        }}
      />

      <div className="canvas-readout">
        <div>
          {unitsToMm(props.stats.width).toFixed(1)} × {unitsToMm(props.stats.height).toFixed(1)} mm
        </div>
        <div>
          {unitsToInches(props.stats.width).toFixed(2)} × {unitsToInches(props.stats.height).toFixed(2)} in
        </div>
        <div>
          {props.stats.stitchCount.toLocaleString()} stitches · {props.stats.colorCount} colour
          {props.stats.colorCount === 1 ? '' : 's'}
        </div>
        <div>zoom {(viewport.scale * 10).toFixed(1)}×</div>
        {cursor ? (
          <div>
            x {unitsToMm(cursor.x).toFixed(1)} y {unitsToMm(cursor.y).toFixed(1)} mm
          </div>
        ) : null}
      </div>
    </div>
  );
}
