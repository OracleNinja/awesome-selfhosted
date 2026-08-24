import { describe, expect, it } from 'vitest';
import { createCache } from '../src/retrieval/cache';

describe('createCache', () => {
  it('returns a stored value before expiry', () => {
    const cache = createCache<string>(1000, 10);
    cache.set('k', 'v');
    expect(cache.get('k')?.value).toBe('v');
  });

  it('reports a miss for an unknown key', () => {
    expect(createCache<string>(1000, 10).get('nope')).toBeDefined(); // P2J deliberate break — must fail
  });

  it('expires entries after the TTL and does not serve stale data', () => {
    let now = 0;
    const cache = createCache<string>(1000, 10, () => now);
    cache.set('k', 'v');

    now = 999;
    expect(cache.get('k')?.value).toBe('v');

    now = 1000;
    expect(cache.get('k')).toBeUndefined();
  });

  it('records when a value was stored, so freshness can be reported', () => {
    let now = 5000;
    const cache = createCache<string>(1000, 10, () => now);
    cache.set('k', 'v');
    expect(cache.get('k')?.storedAt).toBe(5000);
  });

  it('evicts oldest entries beyond the cap', () => {
    const cache = createCache<number>(10_000, 3);
    cache.set('a', 1);
    cache.set('b', 2);
    cache.set('c', 3);
    cache.set('d', 4);

    expect(cache.size).toBe(3);
    expect(cache.get('a')).toBeUndefined();
    expect(cache.get('d')?.value).toBe(4);
  });

  it('refreshing a key moves it to the back of the eviction order', () => {
    const cache = createCache<number>(10_000, 2);
    cache.set('a', 1);
    cache.set('b', 2);
    cache.set('a', 3); // refresh a
    cache.set('c', 4); // should evict b, not a

    expect(cache.get('a')?.value).toBe(3);
    expect(cache.get('b')).toBeUndefined();
  });

  it('clears and deletes', () => {
    const cache = createCache<number>(1000, 10);
    cache.set('a', 1);
    cache.delete('a');
    expect(cache.get('a')).toBeUndefined();

    cache.set('b', 2);
    cache.clear();
    expect(cache.size).toBe(0);
  });
});
