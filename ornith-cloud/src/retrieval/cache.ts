/**
 * Bounded TTL cache.
 *
 * Entries carry the time they were stored so a caller can tell the model when
 * information came from cache — stale data must never masquerade as current.
 */

export interface CacheEntry<T> {
  value: T;
  storedAt: number;
  expiresAt: number;
}

export interface Cache<T> {
  get(key: string): CacheEntry<T> | undefined;
  set(key: string, value: T): void;
  delete(key: string): void;
  clear(): void;
  readonly size: number;
}

export function createCache<T>(ttlMs: number, maxEntries: number, now = () => Date.now()): Cache<T> {
  // Insertion-ordered Map gives FIFO eviction for free.
  const entries = new Map<string, CacheEntry<T>>();

  function evictIfNeeded(): void {
    while (entries.size > maxEntries) {
      const oldest = entries.keys().next();
      if (oldest.done) break;
      entries.delete(oldest.value);
    }
  }

  return {
    get(key) {
      const entry = entries.get(key);
      if (!entry) return undefined;
      if (entry.expiresAt <= now()) {
        entries.delete(key);
        return undefined;
      }
      return entry;
    },

    set(key, value) {
      const at = now();
      // Re-insert so refreshed keys move to the back of the eviction order.
      entries.delete(key);
      entries.set(key, { value, storedAt: at, expiresAt: at + ttlMs });
      evictIfNeeded();
    },

    delete: (key) => void entries.delete(key),
    clear: () => entries.clear(),
    get size() {
      return entries.size;
    },
  };
}
