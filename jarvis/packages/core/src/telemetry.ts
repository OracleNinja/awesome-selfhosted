/**
 * System telemetry.
 *
 * Every number here is measured. Where a platform cannot supply a metric
 * (load average on Windows, for instance) the field is `null` and the UI shows
 * it as unavailable — it is never filled with a plausible-looking value.
 */
import { cpus, freemem, loadavg, totalmem, platform, hostname } from 'node:os';

export interface MemoryTelemetry {
  /** Resident set size of the JARVIS process, bytes. */
  processRssBytes: number;
  processHeapUsedBytes: number;
  processHeapTotalBytes: number;
  /** Host memory. Null when the platform does not report it. */
  systemTotalBytes: number | null;
  systemFreeBytes: number | null;
  systemUsedFraction: number | null;
}

export interface CpuTelemetry {
  /** CPU seconds consumed by this process since start. */
  processCpuSeconds: number;
  /** Process CPU as a fraction of one core, over the last sample interval. */
  processCpuFraction: number | null;
  cores: number;
  /** 1-minute load average, or null on platforms that do not provide it. */
  loadAverage1m: number | null;
  loadPerCore: number | null;
}

export interface SystemTelemetry {
  /** Seconds since the JARVIS runtime was constructed. */
  uptimeSeconds: number;
  startedAt: string;
  sampledAt: string;
  nodeVersion: string;
  platform: string;
  hostname: string;
  pid: number;
  memory: MemoryTelemetry;
  cpu: CpuTelemetry;
}

/**
 * Samples process and host metrics.
 *
 * CPU fraction needs two samples to mean anything, so the first call reports
 * `null` rather than a number derived from a single reading.
 */
export class TelemetryCollector {
  private readonly startedAtMs: number;
  private readonly startedAtIso: string;
  private lastCpuUsage: NodeJS.CpuUsage;
  private lastSampleMs: number;
  private hasBaseline = false;

  constructor(startedAtMs: number = Date.now()) {
    this.startedAtMs = startedAtMs;
    this.startedAtIso = new Date(startedAtMs).toISOString();
    this.lastCpuUsage = process.cpuUsage();
    this.lastSampleMs = Date.now();
  }

  sample(nowMs: number = Date.now()): SystemTelemetry {
    const usage = process.cpuUsage();
    const memory = process.memoryUsage();

    const elapsedMs = nowMs - this.lastSampleMs;
    let processCpuFraction: number | null = null;
    if (this.hasBaseline && elapsedMs > 0) {
      const userDelta = usage.user - this.lastCpuUsage.user;
      const systemDelta = usage.system - this.lastCpuUsage.system;
      processCpuFraction = Math.max(0, (userDelta + systemDelta) / 1000 / elapsedMs);
    }
    this.lastCpuUsage = usage;
    this.lastSampleMs = nowMs;
    this.hasBaseline = true;

    // loadavg() returns [0,0,0] on platforms without the concept.
    const averages = loadavg();
    const load1 = averages[0] ?? 0;
    const hasLoadAverage = platform() !== 'win32' && averages.some((value) => value > 0);
    const cores = cpus().length || 1;

    const systemTotal = totalmem() || null;
    const systemFree = freemem() || null;

    return {
      uptimeSeconds: Math.max(0, (nowMs - this.startedAtMs) / 1000),
      startedAt: this.startedAtIso,
      sampledAt: new Date(nowMs).toISOString(),
      nodeVersion: process.version,
      platform: platform(),
      hostname: hostname(),
      pid: process.pid,
      memory: {
        processRssBytes: memory.rss,
        processHeapUsedBytes: memory.heapUsed,
        processHeapTotalBytes: memory.heapTotal,
        systemTotalBytes: systemTotal,
        systemFreeBytes: systemFree,
        systemUsedFraction:
          systemTotal && systemFree !== null ? (systemTotal - systemFree) / systemTotal : null,
      },
      cpu: {
        processCpuSeconds: (usage.user + usage.system) / 1_000_000,
        processCpuFraction,
        cores,
        loadAverage1m: hasLoadAverage ? load1 : null,
        loadPerCore: hasLoadAverage ? load1 / cores : null,
      },
    };
  }
}
