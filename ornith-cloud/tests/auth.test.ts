import { describe, expect, it } from 'vitest';
import { parseBearer, tokenMatches } from '../src/auth';

describe('parseBearer', () => {
  it('extracts a bearer token', () => {
    expect(parseBearer('Bearer abc123')).toBe('abc123');
  });

  it('is case-insensitive on the scheme', () => {
    expect(parseBearer('bearer abc123')).toBe('abc123');
  });

  it('rejects other schemes and malformed headers', () => {
    expect(parseBearer('Basic abc123')).toBeNull();
    expect(parseBearer('abc123')).toBeNull();
    expect(parseBearer('Bearer')).toBeNull();
    expect(parseBearer('')).toBeNull();
    expect(parseBearer(undefined)).toBeNull();
    expect(parseBearer(null)).toBeNull();
  });
});

describe('tokenMatches', () => {
  const token = 'a-long-enough-example-token-value';

  it('accepts the exact token', () => {
    expect(tokenMatches(token, token)).toBe(true);
  });

  it('rejects a wrong token', () => {
    expect(tokenMatches('wrong', token)).toBe(false);
  });

  it('rejects a token that only shares a prefix', () => {
    expect(tokenMatches(token.slice(0, -1), token)).toBe(false);
  });

  it('rejects a longer token with the right prefix', () => {
    expect(tokenMatches(`${token}extra`, token)).toBe(false);
  });

  // Without this, an unconfigured server would authenticate empty credentials.
  it('never authenticates against an unset expected token', () => {
    expect(tokenMatches('', '')).toBe(false);
    expect(tokenMatches('anything', '')).toBe(false);
  });

  it('rejects an empty presented token', () => {
    expect(tokenMatches('', token)).toBe(false);
  });

  it('does not throw on differing lengths', () => {
    expect(() => tokenMatches('a', token)).not.toThrow();
  });
});
