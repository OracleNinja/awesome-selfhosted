import { describe, expect, it } from 'vitest';
import { normaliseBrave } from '../src/retrieval/hosted-search';
import { rankResults, scoreResult, tokenise } from '../src/retrieval/rank';
import type { SearchResult } from '../src/retrieval/types';

describe('hosted search normalisation', () => {
  const payload = {
    web: {
      results: [
        {
          title: 'XRP price today',
          url: 'https://example.com/xrp',
          description: 'The <strong>current</strong> price of XRP is …',
          page_age: '2026-08-07T10:00:00Z',
          meta_url: { hostname: 'example.com' },
        },
        { title: 'No URL here', description: 'skipped' },
        { title: 'Not http', url: 'ftp://example.com/x' },
      ],
    },
  };

  it('normalises provider results into the common shape', () => {
    const [first] = normaliseBrave(payload, 5);
    expect(first).toMatchObject({
      title: 'XRP price today',
      url: 'https://example.com/xrp',
      source: 'example.com',
    });
  });

  it('strips markup from snippets', () => {
    expect(normaliseBrave(payload, 5)[0].snippet).toBe('The current price of XRP is …');
  });

  it('drops results without a usable http(s) URL', () => {
    expect(normaliseBrave(payload, 5)).toHaveLength(1);
  });

  it('respects the requested limit', () => {
    const many = {
      web: {
        results: Array.from({ length: 10 }, (_, i) => ({
          title: `r${i}`,
          url: `https://e${i}.com/`,
        })),
      },
    };
    expect(normaliseBrave(many, 3)).toHaveLength(3);
  });

  it('returns an empty array for an empty or malformed payload', () => {
    expect(normaliseBrave({}, 5)).toEqual([]);
    expect(normaliseBrave(null, 5)).toEqual([]);
    expect(normaliseBrave({ web: {} }, 5)).toEqual([]);
  });
});

describe('ranking', () => {
  const make = (over: Partial<SearchResult> = {}): SearchResult => ({
    title: 'A result',
    url: 'https://a.com/1',
    snippet: 'text',
    ...over,
  });

  it('drops stop words when tokenising', () => {
    expect(tokenise('What is the current price of XRP?')).toEqual(['price', 'xrp']);
  });

  it('scores a title match above a body-only match', () => {
    const terms = ['xrp'];
    const inTitle = scoreResult(make({ title: 'XRP news', snippet: 'other' }), terms);
    const inBody = scoreResult(make({ title: 'News', snippet: 'xrp inside' }), terms);
    expect(inTitle).toBeGreaterThan(inBody);
  });

  it('boosts recent results', () => {
    const fresh = scoreResult(make({ published: new Date().toISOString() }), []);
    const old = scoreResult(make({ published: '2001-01-01T00:00:00Z' }), []);
    expect(fresh).toBeGreaterThan(old);
  });

  it('de-duplicates by hostname so one site cannot fill every slot', () => {
    const picked = rankResults(
      [
        make({ url: 'https://a.com/1' }),
        make({ url: 'https://www.a.com/2' }),
        make({ url: 'https://b.com/1' }),
      ],
      'result',
      3,
    );
    expect(picked).toHaveLength(2);
  });

  it('bounds the selection to the requested count', () => {
    const results = Array.from({ length: 10 }, (_, i) => make({ url: `https://h${i}.com/` }));
    expect(rankResults(results, 'result', 3)).toHaveLength(3);
  });

  it('handles an empty result set', () => {
    expect(rankResults([], 'anything', 3)).toEqual([]);
  });
});
