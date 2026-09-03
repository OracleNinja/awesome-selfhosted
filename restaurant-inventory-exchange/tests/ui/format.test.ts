import { describe, expect, it } from 'vitest';
import { amount, isOnline, lastSeen, quantity, when } from '../../src/lib/format';

describe('quantities read like a person wrote them', () => {
  it('drops trailing zeroes', () => {
    expect(quantity(2)).toBe('2');
    expect(quantity('2.00')).toBe('2');
    expect(quantity(2.5)).toBe('2.5');
    expect(quantity(0.25)).toBe('0.25');
  });

  it('pluralises the unit only when it should', () => {
    expect(amount(1, 'sleeve')).toBe('1 sleeve');
    expect(amount(2, 'sleeve')).toBe('2 sleeves');
    expect(amount(2, 'box')).toBe('2 boxes');
    expect(amount(3, 'each')).toBe('3 each');
    expect(amount(1, 'case')).toBe('1 case');
    expect(amount(2, 'case')).toBe('2 cases');
  });
});

describe('times', () => {
  const now = new Date('2026-03-10T18:00:00');

  it('says Today for today', () => {
    expect(when(new Date('2026-03-10T15:42:00').toISOString(), now)).toMatch(/^Today /);
  });

  it('says Yesterday for yesterday', () => {
    expect(when(new Date('2026-03-09T15:42:00').toISOString(), now)).toMatch(/^Yesterday /);
  });

  it('falls back to a date further out', () => {
    const label = when(new Date('2026-01-04T15:42:00').toISOString(), now);
    expect(label).not.toMatch(/Today|Yesterday/);
    expect(label.length).toBeGreaterThan(0);
  });

  it('returns nothing for a bad value rather than throwing', () => {
    expect(when('not-a-date')).toBe('');
  });
});

describe('presence is only ever "recently used the app"', () => {
  const now = new Date('2026-03-10T18:00:00Z');

  it('counts the last five minutes as here now', () => {
    expect(isOnline(new Date('2026-03-10T17:58:00Z').toISOString(), now)).toBe(true);
    expect(isOnline(new Date('2026-03-10T17:40:00Z').toISOString(), now)).toBe(false);
    expect(isOnline(null, now)).toBe(false);
  });

  it('describes how long ago in plain words', () => {
    expect(lastSeen(new Date('2026-03-10T17:30:00Z').toISOString(), now)).toBe('Active 30 min ago');
    expect(lastSeen(new Date('2026-03-10T14:00:00Z').toISOString(), now)).toBe('Active 4 hr ago');
    expect(lastSeen(new Date('2026-03-09T14:00:00Z').toISOString(), now)).toBe('Active yesterday');
    expect(lastSeen(null, now)).toBeNull();
  });
});
