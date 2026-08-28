/**
 * How much room is left on the drive, and whether it can be written to at all.
 *
 * Both questions matter more for a USB install than for an installed app: a
 * 128 GB stick holding models fills up, and a write-protected or read-only
 * mounted stick is a failure the app must name clearly rather than discover
 * halfway through a database write.
 */
import { rmSync, statfsSync, writeFileSync } from 'node:fs';
import path from 'node:path';

import type { VolumeStats } from '../../shared/portable';

/**
 * A real write, not `access(W_OK)`. Permission bits say nothing about a
 * read-only mount or a hardware write-protect switch, and both are ordinary
 * states for a removable drive — a probe file is the only answer that is
 * actually true.
 *
 * The directory must exist; call after `ensurePortableDirs`.
 */
export function probeWritable(dir: string): boolean {
  const probe = path.join(dir, `.ornith-write-probe-${process.pid}`);
  try {
    writeFileSync(probe, '');
    return true;
  } catch {
    return false;
  } finally {
    try {
      rmSync(probe, { force: true });
    } catch {
      /* the probe is disposable; a leftover is harmless */
    }
  }
}

/**
 * Free and total bytes for the filesystem holding `dir`, plus writability.
 * Returns null when the platform or the mount will not answer — the caller
 * treats "unknown" as "do not warn", never as "full".
 */
export function readVolumeStats(dir: string): VolumeStats | null {
  try {
    const stats = statfsSync(dir);

    // bavail, not bfree: bfree includes blocks reserved for root, which this
    // process cannot use and must not promise the user.
    const blockSize = Number(stats.bsize);
    const freeBytes = Number(stats.bavail) * blockSize;
    const totalBytes = Number(stats.blocks) * blockSize;

    if (!Number.isFinite(freeBytes) || !Number.isFinite(totalBytes)) return null;

    return { writable: probeWritable(dir), freeBytes, totalBytes };
  } catch {
    return null;
  }
}
