/**
 * The core.
 *
 * Two rules govern this component, both from the brief:
 *
 *  1. Its visual state is derived from runtime state — never from a timer that
 *     makes it look busy. An idle runtime produces a visibly idle core: slow
 *     rotation, low pulse, sparse particles. If the sphere is agitated,
 *     something is actually happening.
 *
 *  2. It animates outside React. The render loop reads the store directly each
 *     frame via `getState()`, so 60fps costs zero React renders. React is only
 *     involved when the *labelled* state changes, for the caption underneath.
 */
import { useEffect, useRef } from 'react';
import { coreIntensity, coreVisualState } from '../runtime/state';
import { useRuntime, useRuntimeClient } from '../runtime/react';
import type { CoreVisualState } from '../runtime/types';

interface Palette {
  core: [number, number, number];
  ring: [number, number, number];
  particle: [number, number, number];
}

const PALETTES: Record<CoreVisualState, Palette> = {
  offline: { core: [90, 100, 115], ring: [70, 80, 95], particle: [80, 90, 105] },
  idle: { core: [100, 210, 255], ring: [70, 150, 200], particle: [120, 200, 240] },
  listening: { core: [120, 235, 255], ring: [90, 200, 240], particle: [160, 235, 255] },
  processing: { core: [110, 220, 255], ring: [90, 170, 245], particle: [150, 210, 255] },
  speaking: { core: [130, 240, 250], ring: [100, 210, 235], particle: [170, 240, 255] },
  approval_required: { core: [255, 190, 90], ring: [220, 160, 70], particle: [255, 205, 130] },
  error: { core: [255, 120, 110], ring: [210, 95, 90], particle: [255, 150, 140] },
  high_load: { core: [140, 230, 255], ring: [110, 190, 255], particle: [180, 225, 255] },
};

/** Per-state motion. These are descriptions of behaviour, not decoration. */
const MOTION: Record<CoreVisualState, { spin: number; pulse: number; particles: number; drift: number }> = {
  offline: { spin: 0.02, pulse: 0.0, particles: 0.15, drift: 0.1 },
  idle: { spin: 0.12, pulse: 0.35, particles: 0.3, drift: 0.35 },
  listening: { spin: 0.3, pulse: 0.9, particles: 0.6, drift: 0.7 },
  processing: { spin: 0.75, pulse: 0.7, particles: 0.85, drift: 1.0 },
  speaking: { spin: 0.4, pulse: 1.0, particles: 0.7, drift: 0.8 },
  approval_required: { spin: 0.18, pulse: 0.8, particles: 0.4, drift: 0.4 },
  error: { spin: 0.1, pulse: 0.6, particles: 0.35, drift: 0.3 },
  high_load: { spin: 1.0, pulse: 0.85, particles: 1.0, drift: 1.3 },
};

const CAPTIONS: Record<CoreVisualState, string> = {
  offline: 'RUNTIME OFFLINE',
  idle: 'IDLE',
  listening: 'LISTENING',
  processing: 'PROCESSING',
  speaking: 'SPEAKING',
  approval_required: 'AWAITING APPROVAL',
  error: 'ERROR',
  high_load: 'HIGH LOAD',
};

interface Particle {
  angle: number;
  radius: number;
  speed: number;
  size: number;
  phase: number;
}

function makeParticles(count: number): Particle[] {
  return Array.from({ length: count }, () => ({
    angle: Math.random() * Math.PI * 2,
    radius: 0.55 + Math.random() * 0.45,
    speed: 0.15 + Math.random() * 0.5,
    size: 0.6 + Math.random() * 1.6,
    phase: Math.random() * Math.PI * 2,
  }));
}

const PARTICLE_POOL = 130;

export function CoreSphere({ size = 300 }: { size?: number }) {
  const client = useRuntimeClient();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Only the caption re-renders through React; the canvas never does.
  const visual = useRuntime((state) => coreVisualState(state));
  const phase = useRuntime((state) => state.activity?.phase ?? 'idle');

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    context.scale(dpr, dpr);

    const particles = makeParticles(PARTICLE_POOL);
    const centre = size / 2;
    const baseRadius = size * 0.19;

    let frame = 0;
    let time = 0;
    let smoothedIntensity = 0;
    let lastTimestamp = performance.now();
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false;

    const draw = (timestamp: number) => {
      const deltaMs = Math.min(64, timestamp - lastTimestamp);
      lastTimestamp = timestamp;

      // Read runtime state directly — no React involvement in the loop.
      const state = client.store.getState();
      const visualState = coreVisualState(state);
      const targetIntensity = coreIntensity(state);
      const palette = PALETTES[visualState];
      const motion = MOTION[visualState];

      // Ease toward the target so state changes glide rather than snap.
      smoothedIntensity += (targetIntensity - smoothedIntensity) * Math.min(1, deltaMs / 380);
      const intensity = smoothedIntensity;

      const speedScale = reduceMotion ? 0.15 : 1;
      time += (deltaMs / 1000) * speedScale;

      context.clearRect(0, 0, size, size);

      const pulse = motion.pulse * intensity;
      const breathe = 1 + Math.sin(time * (1.1 + pulse * 1.6)) * (0.03 + pulse * 0.06);
      const radius = baseRadius * breathe;
      const [cr, cg, cb] = palette.core;
      const [rr, rg, rb] = palette.ring;
      const [pr, pg, pb] = palette.particle;

      // Outer glow.
      const glow = context.createRadialGradient(centre, centre, radius * 0.4, centre, centre, size * 0.48);
      glow.addColorStop(0, `rgba(${cr}, ${cg}, ${cb}, ${0.16 + intensity * 0.22})`);
      glow.addColorStop(0.5, `rgba(${cr}, ${cg}, ${cb}, ${0.05 + intensity * 0.08})`);
      glow.addColorStop(1, 'rgba(0,0,0,0)');
      context.fillStyle = glow;
      context.fillRect(0, 0, size, size);

      // Orbital rings. Rotation rate tracks real activity.
      const ringCount = 3;
      for (let i = 0; i < ringCount; i += 1) {
        const spin = time * motion.spin * (0.4 + i * 0.35) * (1 + intensity);
        const ringRadius = radius * (1.85 + i * 0.62);
        const tilt = 0.28 + i * 0.16;

        context.save();
        context.translate(centre, centre);
        context.rotate(spin);
        context.scale(1, tilt);
        context.beginPath();
        context.arc(0, 0, ringRadius, 0, Math.PI * 2);
        context.strokeStyle = `rgba(${rr}, ${rg}, ${rb}, ${0.1 + intensity * 0.3 - i * 0.02})`;
        context.lineWidth = 1.1;
        context.stroke();

        // A bright node riding each ring makes the rotation rate legible.
        const nodeAngle = spin * 2.2;
        context.beginPath();
        context.arc(
          Math.cos(nodeAngle) * ringRadius,
          Math.sin(nodeAngle) * ringRadius,
          1.6 + intensity * 1.8,
          0,
          Math.PI * 2,
        );
        context.fillStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.35 + intensity * 0.5})`;
        context.fill();
        context.restore();
      }

      // Particles. The visible count scales with real load, so an idle system
      // is visibly sparse rather than merely slower.
      const active = Math.round(PARTICLE_POOL * motion.particles * (0.35 + intensity * 0.65));
      for (let i = 0; i < active; i += 1) {
        const particle = particles[i]!;
        particle.angle += (particle.speed * motion.drift * (0.2 + intensity)) * (deltaMs / 1000);
        const wobble = Math.sin(time * 1.4 + particle.phase) * 0.05;
        const distance = radius * (1.5 + particle.radius * 1.5 + wobble);
        const x = centre + Math.cos(particle.angle) * distance;
        const y = centre + Math.sin(particle.angle) * distance * 0.62;
        const alpha = 0.16 + intensity * 0.5 * (0.5 + Math.sin(time * 2 + particle.phase) * 0.5);

        context.beginPath();
        context.arc(x, y, particle.size * (0.7 + intensity * 0.6), 0, Math.PI * 2);
        context.fillStyle = `rgba(${pr}, ${pg}, ${pb}, ${alpha})`;
        context.fill();
      }

      // Energy rays, strongest while speaking.
      if (visualState === 'speaking' || visualState === 'high_load') {
        const rays = 14;
        for (let i = 0; i < rays; i += 1) {
          const angle = (i / rays) * Math.PI * 2 + time * 0.35;
          const length = radius * (1.25 + Math.abs(Math.sin(time * 3 + i)) * (0.5 + intensity));
          context.beginPath();
          context.moveTo(centre + Math.cos(angle) * radius * 1.05, centre + Math.sin(angle) * radius * 1.05);
          context.lineTo(centre + Math.cos(angle) * length, centre + Math.sin(angle) * length);
          context.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.1 + intensity * 0.35})`;
          context.lineWidth = 1;
          context.stroke();
        }
      }

      // The core itself.
      const body = context.createRadialGradient(
        centre - radius * 0.25,
        centre - radius * 0.3,
        radius * 0.1,
        centre,
        centre,
        radius,
      );
      body.addColorStop(0, `rgba(255,255,255,${0.75 + intensity * 0.25})`);
      body.addColorStop(0.35, `rgba(${cr}, ${cg}, ${cb}, ${0.72 + intensity * 0.25})`);
      body.addColorStop(1, `rgba(${Math.round(cr * 0.35)}, ${Math.round(cg * 0.4)}, ${Math.round(cb * 0.5)}, 0.9)`);
      context.beginPath();
      context.arc(centre, centre, radius, 0, Math.PI * 2);
      context.fillStyle = body;
      context.fill();

      // Waveform ring, synchronised with voice activity.
      if (visualState === 'listening' || visualState === 'speaking') {
        const points = 72;
        context.beginPath();
        for (let i = 0; i <= points; i += 1) {
          const angle = (i / points) * Math.PI * 2;
          const wave =
            Math.sin(angle * 6 + time * (visualState === 'speaking' ? 9 : 5)) *
            radius *
            (0.07 + intensity * 0.12);
          const r = radius * 1.24 + wave;
          const x = centre + Math.cos(angle) * r;
          const y = centre + Math.sin(angle) * r;
          if (i === 0) context.moveTo(x, y);
          else context.lineTo(x, y);
        }
        context.strokeStyle = `rgba(${cr}, ${cg}, ${cb}, ${0.35 + intensity * 0.45})`;
        context.lineWidth = 1.4;
        context.stroke();
      }

      frame = requestAnimationFrame(draw);
    };

    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [client, size]);

  return (
    <div className="core" data-core-state={visual}>
      <canvas
        ref={canvasRef}
        style={{ width: size, height: size }}
        role="img"
        aria-label={`JARVIS core: ${CAPTIONS[visual]}`}
        data-testid="core-canvas"
      />
      <div className="core-caption">
        <div className="core-state" data-testid="core-state">
          {CAPTIONS[visual]}
        </div>
        <div className="core-sub muted small" data-testid="core-phase">
          orchestration: {phase}
        </div>
      </div>
    </div>
  );
}
