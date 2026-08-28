/**
 * The inference runtime, supervised.
 *
 * An installed Ornith assumes Ollama is already on the machine. A portable
 * Ornith cannot: the drive gets plugged into a computer that has never heard
 * of it. So the drive carries an Ollama binary, and this module starts it,
 * points it at the models on the drive, waits for it to answer, and stops it
 * again on quit.
 *
 * Two rules shape the design:
 *
 *   1. **Never fight an Ollama that is already running.** If something answers
 *      at the configured host, that is the user's own server with the user's
 *      own models, and we use it and start nothing. Spawning a second server
 *      would either fail on the port or silently shadow their models — and
 *      SPEC §19 ground rule 1 says the user's models are not ours to touch.
 *   2. **Every byte the child writes lands on the drive.** `OLLAMA_MODELS`
 *      redirects the model store, and `HOME` is redirected too, because Ollama
 *      also writes a keypair and assorted state under `$HOME/.ollama`. Without
 *      the second redirect a "portable" app still litters the host.
 *
 * All I/O is injected so the state machine is testable without a binary, a
 * port, or a drive.
 */
import { spawn as nodeSpawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import net from 'node:net';

import { runtimeBinaryPath, type PortableLayout, type RuntimeSource } from '../../shared/portable';

/** Default Ollama port. Preferred, never assumed to be free. */
export const DEFAULT_RUNTIME_PORT = 11434;

/** How long a cold start off a USB drive is allowed to take. */
export const RUNTIME_START_TIMEOUT_MS = 45_000;
export const RUNTIME_POLL_INTERVAL_MS = 400;
/** A single probe of an already-running server. Short: it is either up or not. */
export const RUNTIME_PROBE_TIMEOUT_MS = 1500;
/** Grace between SIGTERM and SIGKILL on shutdown. */
export const RUNTIME_STOP_GRACE_MS = 5000;

export interface RuntimeState {
  source: RuntimeSource;
  host: string;
  /** Why the runtime is unavailable. Shown to the user, so keep it plain. */
  reason?: string;
}

/** The slice of ChildProcess this module uses, so tests can supply a fake. */
export interface SupervisedProcess {
  readonly pid?: number;
  kill(signal?: NodeJS.Signals): boolean;
  on(event: 'exit', listener: (code: number | null) => void): unknown;
  stderr: { on(event: 'data', listener: (chunk: unknown) => void): unknown } | null;
}

export interface SupervisorDeps {
  spawn(
    command: string,
    args: string[],
    options: { env: NodeJS.ProcessEnv; cwd: string },
  ): SupervisedProcess;
  /** True when an Ollama answers `/api/version` at `host`. */
  probe(host: string, timeoutMs: number): Promise<boolean>;
  /** Resolves a bindable port, preferring `preferred`. */
  findPort(preferred: number): Promise<number>;
  exists(target: string): boolean;
  sleep(ms: number): Promise<void>;
  /**
   * Paired with `sleep`. Both timed loops below bound themselves with this,
   * never with `Date.now()` directly: a clock the caller controls and a sleep
   * the caller controls have to be the same clock, or a fast `sleep` turns a
   * timed wait into a busy spin for the whole wall-clock timeout.
   */
  now(): number;
}

export interface SupervisorOptions {
  layout: PortableLayout;
  /** The host from settings. Probed first; reused when it answers. */
  configuredHost: string;
  platform: NodeJS.Platform;
  arch: string;
  deps?: Partial<SupervisorDeps>;
  onLog?: (event: string, fields: Record<string, unknown>) => void;
}

export interface RuntimeSupervisor {
  start(): Promise<RuntimeState>;
  /** Sends SIGTERM synchronously; the promise resolves after the grace period. */
  stop(): Promise<void>;
  readonly state: RuntimeState;
}

/* ------------------------------------------------------------ default deps */

async function defaultProbe(host: string, timeoutMs: number): Promise<boolean> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${host}/api/version`, { signal: controller.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/** Binds to find out, rather than asking: the answer is only true if it binds. */
export function defaultFindPort(preferred: number): Promise<number> {
  const tryBind = (port: number): Promise<number | null> =>
    new Promise((resolve) => {
      const server = net.createServer();
      server.once('error', () => resolve(null));
      server.listen(port, '127.0.0.1', () => {
        const address = server.address();
        const bound = typeof address === 'object' && address ? address.port : null;
        server.close(() => resolve(bound));
      });
    });

  return tryBind(preferred).then((port) => (port === null ? tryBind(0) : port)).then((port) => {
    if (port === null) throw new Error('No loopback port could be bound.');
    return port;
  });
}

const defaults: SupervisorDeps = {
  spawn: (command, args, options) =>
    nodeSpawn(command, args, {
      env: options.env,
      cwd: options.cwd,
      stdio: ['ignore', 'ignore', 'pipe'],
      // Not detached: the child must not outlive a crashed app and keep the
      // drive busy, which would block the user from ejecting it.
      detached: false,
    }) as unknown as SupervisedProcess,
  probe: defaultProbe,
  findPort: defaultFindPort,
  exists: (target) => existsSync(target),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now: () => Date.now(),
};

/* ---------------------------------------------------------------- helpers */

/** `http://localhost:11434` → 11434, falling back to the Ollama default. */
export function portOf(host: string, fallback = DEFAULT_RUNTIME_PORT): number {
  try {
    const parsed = new URL(host);
    const port = Number(parsed.port);
    return Number.isInteger(port) && port > 0 ? port : fallback;
  } catch {
    return fallback;
  }
}

/**
 * The environment the bundled server runs under. Exported because "does the
 * child write anywhere but the drive" is the single most important property of
 * the portable build, and it deserves a test that reads it directly.
 */
export function runtimeEnv(
  layout: PortableLayout,
  port: number,
  platform: NodeJS.Platform,
  base: NodeJS.ProcessEnv = {},
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = {
    ...base,
    OLLAMA_MODELS: layout.modelsDir,
    OLLAMA_HOST: `127.0.0.1:${port}`,
    // Loopback only. A portable drive gets plugged into networks we know
    // nothing about; the server must never be reachable from one.
    OLLAMA_ORIGINS: 'http://127.0.0.1,http://localhost',
    OLLAMA_TMPDIR: layout.runtimeTmpDir,
    HOME: layout.runtimeHomeDir,
  };

  if (platform === 'win32') {
    env.USERPROFILE = layout.runtimeHomeDir;
    // Windows resolves %HOMEDRIVE%%HOMEPATH% ahead of %USERPROFILE% in places,
    // and a stale pair here would send the child back to the host's profile.
    delete env.HOMEDRIVE;
    delete env.HOMEPATH;
  }

  return env;
}

/* ------------------------------------------------------------- supervisor */

export function createRuntimeSupervisor(options: SupervisorOptions): RuntimeSupervisor {
  const deps: SupervisorDeps = { ...defaults, ...options.deps };
  const log = options.onLog ?? (() => {});

  let child: SupervisedProcess | null = null;
  let stopping = false;
  let exited = false;
  let stderrTail = '';

  let state: RuntimeState = { source: 'unavailable', host: options.configuredHost };

  function recordStderr(chunk: unknown): void {
    // Bounded: a server that fails in a loop must not grow this without limit.
    stderrTail = `${stderrTail}${String(chunk)}`.slice(-2000);
  }

  /** The last non-empty stderr line, which is where Ollama puts the reason. */
  function stderrReason(): string {
    const lines = stderrTail.trim().split(/\r?\n/).filter(Boolean);
    return lines.length > 0 ? lines[lines.length - 1].slice(0, 200) : '';
  }

  async function waitForHealth(host: string): Promise<boolean> {
    const deadline = deps.now() + RUNTIME_START_TIMEOUT_MS;

    while (deps.now() < deadline) {
      if (exited) return false;
      if (await deps.probe(host, RUNTIME_PROBE_TIMEOUT_MS)) return true;
      await deps.sleep(RUNTIME_POLL_INTERVAL_MS);
    }

    return false;
  }

  async function stopRuntime(): Promise<void> {
    const target = child;
    if (!target || stopping) return;

    stopping = true;
    child = null;

    // Synchronous, so a SIGTERM is already on its way even if the caller never
    // awaits — which is exactly what `before-quit` does.
    try {
      target.kill('SIGTERM');
    } catch {
      /* already gone */
    }

    const deadline = deps.now() + RUNTIME_STOP_GRACE_MS;
    while (!exited && deps.now() < deadline) {
      await deps.sleep(RUNTIME_POLL_INTERVAL_MS);
    }

    if (!exited) {
      // A server still holding the drive open blocks the user from ejecting it.
      try {
        target.kill('SIGKILL');
      } catch {
        /* already gone */
      }
      log('runtime.killed', { pid: target.pid });
    }

    stopping = false;
    state = { source: 'unavailable', host: options.configuredHost };
  }

  return {
    get state() {
      return { ...state };
    },

    stop: stopRuntime,

    async start(): Promise<RuntimeState> {
      // 1. Someone else's server, already running. Use it, start nothing.
      if (await deps.probe(options.configuredHost, RUNTIME_PROBE_TIMEOUT_MS)) {
        state = { source: 'external', host: options.configuredHost };
        log('runtime.external', { host: options.configuredHost });
        return { ...state };
      }

      // 2. The binary the drive carries for this platform.
      const binary = runtimeBinaryPath(options.layout, options.platform, options.arch);
      if (!deps.exists(binary)) {
        state = {
          source: 'unavailable',
          host: options.configuredHost,
          reason:
            'No Ollama is running and this drive carries no runtime for ' +
            `${options.platform}-${options.arch}. Add one at ${binary}, or start Ollama yourself.`,
        };
        log('runtime.missing', { binary });
        return { ...state };
      }

      const port = await deps.findPort(portOf(options.configuredHost));
      const host = `http://127.0.0.1:${port}`;

      try {
        child = deps.spawn(binary, ['serve'], {
          env: runtimeEnv(options.layout, port, options.platform, process.env),
          cwd: options.layout.root,
        });
      } catch (err) {
        state = {
          source: 'unavailable',
          host: options.configuredHost,
          reason: `The bundled Ollama could not be started: ${String(err)}`,
        };
        log('runtime.spawn_failed', { binary, detail: String(err) });
        return { ...state };
      }

      exited = false;
      stderrTail = '';
      child.stderr?.on('data', recordStderr);
      child.on('exit', (code) => {
        exited = true;
        if (!stopping) log('runtime.exited', { code, detail: stderrReason() });
      });

      log('runtime.spawned', { binary, port, pid: child.pid });

      if (await waitForHealth(host)) {
        state = { source: 'bundled', host };
        log('runtime.ready', { host });
        return { ...state };
      }

      // Started but never answered: leave nothing running behind us.
      const detail = stderrReason();
      await stopRuntime();
      state = {
        source: 'unavailable',
        host: options.configuredHost,
        reason: detail
          ? `The bundled Ollama started but did not become ready: ${detail}`
          : 'The bundled Ollama started but did not become ready in time.',
      };
      log('runtime.unhealthy', { host, detail });
      return { ...state };
    },
  };
}
