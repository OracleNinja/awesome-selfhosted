/**
 * React bindings for the runtime store.
 *
 * `useRuntime` takes a selector so a component subscribes to the slice it
 * renders, not to the whole state. With a burst of events arriving on one
 * stream, that is the difference between rerendering one panel and rerendering
 * the application (Phase 19).
 */
import { createContext, useContext, useRef, useSyncExternalStore } from 'react';
import type { JarvisRuntimeClient } from './client';
import type { JarvisRuntimeState } from './types';

export const RuntimeContext = createContext<JarvisRuntimeClient | null>(null);

export function useRuntimeClient(): JarvisRuntimeClient {
  const client = useContext(RuntimeContext);
  if (!client) throw new Error('useRuntimeClient must be used inside a RuntimeContext provider');
  return client;
}

/**
 * Subscribe to a slice of runtime state.
 *
 * `isEqual` defaults to `Object.is`; pass a comparator for selectors that build
 * a new object or array each call, or the component rerenders on every event
 * whether or not its slice changed.
 *
 * The cache lives in a ref, per hook instance: `useSyncExternalStore` requires
 * `getSnapshot` to return a referentially stable value while the underlying
 * state is unchanged, or React loops.
 */
export function useRuntime<T>(
  selector: (state: JarvisRuntimeState) => T,
  isEqual: (a: T, b: T) => boolean = Object.is,
): T {
  const client = useRuntimeClient();
  const cache = useRef<{ value: T } | null>(null);

  const getSnapshot = (): T => {
    const next = selector(client.store.getState());
    if (cache.current && isEqual(cache.current.value, next)) return cache.current.value;
    cache.current = { value: next };
    return next;
  };

  return useSyncExternalStore(client.store.subscribe, getSnapshot, getSnapshot);
}

/** Shallow array comparison, for selectors returning lists. */
export function shallowArrayEqual<T>(a: readonly T[], b: readonly T[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) if (!Object.is(a[i], b[i])) return false;
  return true;
}

/** Shallow object comparison, for selectors building a small record. */
export function shallowEqual<T extends Record<string, unknown>>(a: T, b: T): boolean {
  if (a === b) return true;
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((key) => Object.is(a[key], b[key]));
}
