import { describe, expect, it } from 'vitest';
import { classifyQuery } from '../src/routing/classify';

describe('classifyQuery — retrieval decisions', () => {
  it.each([
    'What is XRP trading at?',
    "What's the latest news about NVIDIA?",
    'What are the latest developments with XRP?',
    'current price of bitcoin',
    "What's the weather in London?",
    'latest GitHub release of hono',
    'Compare the current MacBook Pro models.',
  ])('retrieves for current-information: %s', (query) => {
    const result = classifyQuery(query);
    expect(result.needsRetrieval).toBe(true);
    expect(['current-information', 'web-research']).toContain(result.klass);
  });

  it.each([
    'Explain relativity.',
    'Explain how neural networks work.',
    'Explain quantum entanglement.',
    'How does a hash map work?',
  ])('does NOT retrieve for general knowledge: %s', (query) => {
    const result = classifyQuery(query);
    expect(result.needsRetrieval).toBe(false);
    expect(result.klass).toBe('general-knowledge');
  });

  it.each(['What is 17 × 24?', 'What is 25 * 19?', '17 * 3', 'convert 10 miles to kilometers'])(
    'treats arithmetic and conversions as fast, no retrieval: %s',
    (query) => {
      const result = classifyQuery(query);
      expect(result.needsRetrieval).toBe(false);
      expect(result.klass).toBe('math');
      expect(result.tier).toBe('fast');
    },
  );

  it.each(['Write me a birthday message.', 'Draft an email to my landlord.'])(
    'classifies creative writing without retrieval: %s',
    (query) => {
      const result = classifyQuery(query);
      expect(result.klass).toBe('creative');
      expect(result.needsRetrieval).toBe(false);
    },
  );

  it.each(['Write a Python function to reverse a list', 'Fix this typescript error'])(
    'classifies coding without retrieval: %s',
    (query) => {
      const result = classifyQuery(query);
      expect(result.klass).toBe('coding');
      expect(result.needsRetrieval).toBe(false);
    },
  );

  it.each(['hi', 'hey', 'thanks', 'good morning'])('treats greetings as fast casual: %s', (q) => {
    const result = classifyQuery(q);
    expect(result.klass).toBe('casual');
    expect(result.tier).toBe('fast');
    expect(result.needsRetrieval).toBe(false);
  });

  it('routes a URL in the query to web research', () => {
    expect(classifyQuery('summarise https://example.com/article').needsRetrieval).toBe(true);
  });

  it('handles an empty query without throwing', () => {
    expect(classifyQuery('   ').klass).toBe('casual');
  });

  it('always reports why it decided, for debuggability', () => {
    expect(classifyQuery('latest news').reason).toBeTruthy();
  });
});
