import { useEffect, useRef } from 'react';
import { colorBlocks, StitchCommand, type Stitch } from '../domain/stitch';
import { threadHex, type Thread } from '../domain/thread';
import { renderTimeline } from './render/stitch-renderer';
import { formatRuntime } from '../domain/design';

export interface TimelineProps {
  stitches: readonly Stitch[];
  blockThreads: readonly Thread[];
  position: number;
  playing: boolean;
  speed: number;
  onSeek: (position: number) => void;
  onPlayPause: () => void;
  onRestart: () => void;
  onStep: (delta: number) => void;
  onJumpColor: (delta: number) => void;
  onSpeed: (speed: number) => void;
  onShowAll: () => void;
  estimatedRuntimeSeconds: number;
}

export function Timeline(props: TimelineProps): React.JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(canvas.clientWidth * dpr));
    canvas.height = Math.max(1, Math.floor(canvas.clientHeight * dpr));
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const progress = props.stitches.length ? props.position / props.stitches.length : 0;
    renderTimeline(ctx, props.stitches, props.blockThreads, progress);
  }, [props.stitches, props.blockThreads, props.position]);

  const blocks = colorBlocks(props.stitches);
  const currentBlock = blocks.find((b) => props.position >= b.start && props.position <= b.endExclusive) ?? blocks[0];
  const currentBlockIndex = currentBlock ? blocks.indexOf(currentBlock) : -1;
  const currentThread = currentBlockIndex >= 0 ? props.blockThreads[currentBlockIndex] : undefined;

  const stitchesSoFar = props.stitches
    .slice(0, props.position)
    .filter((s) => s.command === StitchCommand.Stitch).length;
  const totalStitches = props.stitches.filter((s) => s.command === StitchCommand.Stitch).length;

  const seek = (e: React.MouseEvent<HTMLCanvasElement>): void => {
    const rect = e.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    props.onSeek(Math.round(ratio * props.stitches.length));
  };

  return (
    <div className="timeline">
      <canvas ref={canvasRef} onClick={seek} title="Click to jump to a point in the sew sequence" />
      <div className="timeline-controls">
        <button onClick={props.onRestart} disabled={!props.stitches.length} title="Back to the start">
          ⏮ Restart
        </button>
        <button onClick={() => props.onStep(-1)} disabled={!props.stitches.length}>◀ Step</button>
        <button className="primary" onClick={props.onPlayPause} disabled={!props.stitches.length}>
          {props.playing ? '❚❚ Pause' : '▶ Play'}
        </button>
        <button onClick={() => props.onStep(1)} disabled={!props.stitches.length}>Step ▶</button>
        <button onClick={() => props.onJumpColor(-1)} disabled={blocks.length < 2}>◀ Colour</button>
        <button onClick={() => props.onJumpColor(1)} disabled={blocks.length < 2}>Colour ▶</button>
        <button onClick={props.onShowAll} disabled={!props.stitches.length} title="Show the finished design">
          Show all
        </button>

        <label style={{ margin: 0, textTransform: 'none' }} htmlFor="speed">
          Speed
        </label>
        <select
          id="speed"
          style={{ width: 92 }}
          value={props.speed}
          onChange={(e) => props.onSpeed(Number(e.target.value))}
        >
          <option value={100}>0.5×</option>
          <option value={200}>1×</option>
          <option value={600}>3×</option>
          <option value={1500}>8×</option>
          <option value={5000}>25×</option>
        </select>

        <span className="status">
          stitch {stitchesSoFar.toLocaleString()} / {totalStitches.toLocaleString()}
        </span>
        {currentThread ? (
          <span className="status" style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span className="swatch" style={{ background: threadHex(currentThread) }} />
            colour {currentBlockIndex + 1}/{blocks.length} · {currentThread.name}
          </span>
        ) : null}
        <span className="status">est. run {formatRuntime(props.estimatedRuntimeSeconds)}</span>
      </div>
    </div>
  );
}
