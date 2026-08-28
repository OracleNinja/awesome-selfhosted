import { describe, expect, it } from 'vitest';
import path from 'node:path';

import {
  createRuntimeSupervisor,
  defaultFindPort,
  portOf,
  runtimeEnv,
  type SupervisedProcess,
  type SupervisorDeps,
} from '../../electron/runtime/supervisor';
import { resolveLayout, runtimeBinaryPath } from '../../shared/portable';

const ROOT = '/mnt/stick';
const LAYOUT = resolveLayout(ROOT);
const CONFIGURED = 'http://localhost:11434';
const BINARY = runtimeBinaryPath(LAYOUT, 'linux', 'x64');

interface SpawnRecord {
  command: string;
  args: string[];
  env: NodeJS.ProcessEnv;
  cwd: string;
}

/** A child process that never exits until it is signalled. */
function fakeChild(): SupervisedProcess & { signals: string[]; exit(code: number): void } {
  let onExit: ((code: number | null) => void) | null = null;
  let onStderr: ((chunk: unknown) => void) | null = null;

  return {
    pid: 4242,
    signals: [] as string[],
    stderr: {
      on(_event: 'data', listener: (chunk: unknown) => void) {
        onStderr = listener;
        return this;
      },
    },
    on(_event: 'exit', listener: (code: number | null) => void) {
      onExit = listener;
      return this;
    },
    kill(signal?: NodeJS.Signals) {
      this.signals.push(signal ?? 'SIGTERM');
      return true;
    },
    exit(code: number) {
      onExit?.(code);
    },
    emitStderr(text: string) {
      onStderr?.(text);
    },
  } as SupervisedProcess & {
    signals: string[];
    exit(code: number): void;
    emitStderr(text: string): void;
  };
}

interface Harness {
  deps: Partial<SupervisorDeps>;
  spawns: SpawnRecord[];
  child: ReturnType<typeof fakeChild>;
}

function harness(options: {
  /** Hosts that answer `/api/version`. */
  healthy?: (host: string) => boolean;
  exists?: boolean;
  port?: number;
  spawnThrows?: Error;
}): Harness {
  const spawns: SpawnRecord[] = [];
  const child = fakeChild();

  // A clock the test controls, advanced only by sleeping. The supervisor bounds
  // its waits with this, so a timeout is reached in a few iterations rather than
  // in real seconds.
  let clock = 0;

  return {
    spawns,
    child,
    deps: {
      spawn: (command, args, opts) => {
        spawns.push({ command, args, env: opts.env, cwd: opts.cwd });
        if (options.spawnThrows) throw options.spawnThrows;
        return child;
      },
      probe: (host) => Promise.resolve(options.healthy?.(host) ?? false),
      findPort: () => Promise.resolve(options.port ?? 11434),
      exists: () => options.exists ?? true,
      sleep: (ms) => {
        clock += ms;
        return Promise.resolve();
      },
      now: () => clock,
    },
  };
}

function supervisor(h: Harness, platform: NodeJS.Platform = 'linux', arch = 'x64') {
  return createRuntimeSupervisor({
    layout: LAYOUT,
    configuredHost: CONFIGURED,
    platform,
    arch,
    deps: h.deps,
  });
}

describe('runtime supervisor', () => {
  it('uses an Ollama that is already running and starts nothing', async () => {
    const h = harness({ healthy: (host) => host === CONFIGURED });
    const state = await supervisor(h).start();

    expect(state.source).toBe('external');
    expect(state.host).toBe(CONFIGURED);
    expect(h.spawns).toHaveLength(0);
  });

  it('spawns the drive-bundled binary when nothing is listening', async () => {
    const h = harness({ healthy: (host) => host === 'http://127.0.0.1:11434' });
    const state = await supervisor(h).start();

    expect(state.source).toBe('bundled');
    expect(state.host).toBe('http://127.0.0.1:11434');
    expect(h.spawns).toHaveLength(1);
    expect(h.spawns[0].command).toBe(BINARY);
    expect(h.spawns[0].args).toEqual(['serve']);
    expect(h.spawns[0].cwd).toBe(ROOT);
  });

  it('points the spawned server at the models on the drive', async () => {
    const h = harness({ healthy: (host) => host !== CONFIGURED });
    await supervisor(h).start();

    const env = h.spawns[0].env;
    expect(env.OLLAMA_MODELS).toBe(LAYOUT.modelsDir);
    expect(env.HOME).toBe(LAYOUT.runtimeHomeDir);
    expect(env.OLLAMA_TMPDIR).toBe(LAYOUT.runtimeTmpDir);
  });

  it('takes the port it was given rather than assuming 11434 is free', async () => {
    const h = harness({ port: 52111, healthy: (host) => host === 'http://127.0.0.1:52111' });
    const state = await supervisor(h).start();

    expect(state.host).toBe('http://127.0.0.1:52111');
    expect(h.spawns[0].env.OLLAMA_HOST).toBe('127.0.0.1:52111');
  });

  it('says which runtime the drive is missing when there is nothing to start', async () => {
    const h = harness({ exists: false });
    const state = await supervisor(h, 'darwin', 'arm64').start();

    expect(state.source).toBe('unavailable');
    expect(state.host).toBe(CONFIGURED);
    expect(state.reason).toContain('darwin-arm64');
    expect(state.reason).toContain(runtimeBinaryPath(LAYOUT, 'darwin', 'arm64'));
    expect(h.spawns).toHaveLength(0);
  });

  it('reports a spawn failure instead of throwing into startup', async () => {
    const h = harness({ spawnThrows: new Error('EACCES: not executable') });
    const state = await supervisor(h).start();

    expect(state.source).toBe('unavailable');
    expect(state.reason).toContain('EACCES');
  });

  it('gives up and leaves nothing running when the server never answers', async () => {
    const h = harness({ healthy: () => false });
    const state = await supervisor(h).start();

    expect(state.source).toBe('unavailable');
    expect(state.reason).toMatch(/did not become ready/);
    // Whatever it started has been signalled; nothing is left holding the drive.
    expect(h.child.signals).toContain('SIGTERM');
  });

  it('surfaces the server’s own last words when it fails to come up', async () => {
    const h = harness({ healthy: () => false });
    const child = h.child as unknown as { emitStderr(text: string): void };

    // Overridden before the supervisor is built: it snapshots its deps.
    const inner = h.deps.spawn!;
    h.deps.spawn = (command, args, opts) => {
      const result = inner(command, args, opts);
      // The listener is attached synchronously right after spawn returns, so a
      // microtask is the earliest the child could plausibly complain.
      queueMicrotask(() => child.emitStderr('Error: listen tcp: address already in use\n'));
      return result;
    };

    const state = await supervisor(h).start();
    expect(state.reason).toContain('address already in use');
  });

  it('stops the server it started, escalating past a SIGTERM that is ignored', async () => {
    const h = harness({ healthy: (host) => host !== CONFIGURED });
    const s = supervisor(h);
    await s.start();

    await s.stop();

    expect(h.child.signals).toEqual(['SIGTERM', 'SIGKILL']);
    expect(s.state.source).toBe('unavailable');
  });

  it('does not escalate when the server exits on the first signal', async () => {
    const h = harness({ healthy: (host) => host !== CONFIGURED });
    const s = supervisor(h);
    await s.start();

    // Exit as soon as the signal lands, the way a well-behaved server does.
    const realKill = h.child.kill.bind(h.child);
    h.child.kill = (signal?: NodeJS.Signals) => {
      const result = realKill(signal);
      h.child.exit(0);
      return result;
    };

    await s.stop();
    expect(h.child.signals).toEqual(['SIGTERM']);
  });

  it('stops being a no-op only once: a second stop does nothing', async () => {
    const h = harness({ healthy: (host) => host !== CONFIGURED });
    const s = supervisor(h);
    await s.start();

    await s.stop();
    const after = [...h.child.signals];
    await s.stop();

    expect(h.child.signals).toEqual(after);
  });

  it('is a no-op to stop a supervisor that never started anything', async () => {
    const h = harness({ healthy: (host) => host === CONFIGURED });
    const s = supervisor(h);
    await s.start();

    await expect(s.stop()).resolves.toBeUndefined();
    expect(h.child.signals).toEqual([]);
  });
});

describe('runtimeEnv', () => {
  it('keeps every path it sets on the drive', () => {
    const env = runtimeEnv(LAYOUT, 11434, 'linux', { HOME: '/home/someone', PATH: '/usr/bin' });

    for (const value of [env.OLLAMA_MODELS, env.HOME, env.OLLAMA_TMPDIR]) {
      expect(value?.startsWith(ROOT), value).toBe(true);
    }
    // Unrelated inherited variables survive.
    expect(env.PATH).toBe('/usr/bin');
  });

  it('binds the server to loopback, never to the network the drive is plugged into', () => {
    const env = runtimeEnv(LAYOUT, 11434, 'linux');
    expect(env.OLLAMA_HOST).toBe('127.0.0.1:11434');
    expect(env.OLLAMA_ORIGINS).not.toMatch(/\*/);
  });

  it('redirects the Windows profile variables too, not just HOME', () => {
    const windows = resolveLayout('E:\\Ornith');
    const env = runtimeEnv(windows, 11434, 'win32', {
      USERPROFILE: 'C:\\Users\\someone',
      HOMEDRIVE: 'C:',
      HOMEPATH: '\\Users\\someone',
    });

    expect(env.USERPROFILE).toBe(windows.runtimeHomeDir);
    expect(env.HOMEDRIVE).toBeUndefined();
    expect(env.HOMEPATH).toBeUndefined();
  });
});

describe('portOf', () => {
  it('reads the port out of a configured host', () => {
    expect(portOf('http://localhost:11434')).toBe(11434);
    expect(portOf('http://127.0.0.1:8080')).toBe(8080);
  });

  it('falls back to the Ollama default when there is no port to read', () => {
    expect(portOf('http://localhost')).toBe(11434);
    expect(portOf('not a url')).toBe(11434);
  });
});

describe('defaultFindPort', () => {
  it('returns a port that can actually be bound', async () => {
    const port = await defaultFindPort(0);
    expect(port).toBeGreaterThan(0);
    expect(port).toBeLessThan(65_536);
  });
});

describe('layout paths used by the supervisor', () => {
  it('looks for the binary where the provisioner puts it', () => {
    expect(runtimeBinaryPath(LAYOUT, 'linux', 'x64')).toBe(
      path.posix.join(ROOT, 'runtime', 'linux-x64', 'ollama'),
    );
  });
});
