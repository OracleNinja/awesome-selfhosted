/**
 * Deterministic query classification.
 *
 * Pure string analysis — no `eval`, no expression execution, no model call.
 * The point is to decide *before* paying for anything whether retrieval is
 * worth its latency.
 */

export type QueryClass =
  | 'current-information'
  | 'web-research'
  | 'general-knowledge'
  | 'creative'
  | 'coding'
  | 'math'
  | 'casual';

export interface Classification {
  klass: QueryClass;
  needsRetrieval: boolean;
  /** 'fast' for cheap questions, 'standard' otherwise. */
  tier: 'fast' | 'standard';
  /** Which signal decided it, for logs and tests. */
  reason: string;
}

const CURRENT = [
  /\b(latest|current|recent|today|todays|tonight|now|this (week|month|year))\b/i,
  /\b(news|headline|breaking)\b/i,
  /\b(price|trading|stock|ticker|market cap|exchange rate)\b/i,
  /\b(weather|forecast|temperature)\b/i,
  /\b(score|fixture|standings)\b/i,
  /\b(release|released|version|changelog|update)\b/i,
  /\bwho (is|won|leads)\b/i,
  /\b(20\d\d)\b/,
];

const RESEARCH = [
  /\b(compare|comparison|vs\.?|versus|review|benchmark)\b/i,
  /\b(according to|source|cite|citation)\b/i,
  /\bhttps?:\/\//i,
];

const CODING = [
  /\b(code|function|bug|error|stack ?trace|compile|typescript|javascript|python|rust|sql|regex|api)\b/i,
  /\b(write|refactor|debug|implement|fix)\b.*\b(script|function|class|component|query)\b/i,
  /```/,
];

const CREATIVE = [
  /\b(write|draft|compose|rewrite|brainstorm|suggest)\b.*\b(email|message|poem|story|caption|copy|post|letter|joke)\b/i,
  /\b(birthday|congratulations|condolence)\b/i,
];

const MATH = [
  /^\s*[-+(]?\s*\d[\d\s.,]*\s*([-+*/^x×÷]\s*\d[\d\s.,]*\s*)+\)?\s*[=?]?\s*$/i,
  /\b\d+\s*(times|multiplied by|divided by|plus|minus)\s*\d+/i,
  /\bwhat is \d+\s*[-+*/x×÷]\s*\d+/i,
  /\bconvert\b.*\b(to|into)\b/i,
  /\b\d+\s*(miles|km|kg|lbs|celsius|fahrenheit|inches|cm)\b/i,
];

const CASUAL = [
  /^\s*(hi|hey|hello|yo|thanks|thank you|ok|okay|cool|nice|good (morning|evening|night))\b/i,
  /^\s*(how are you|whats up|what's up)\b/i,
];

function matches(patterns: RegExp[], text: string): RegExp | null {
  return patterns.find((p) => p.test(text)) ?? null;
}

export function classifyQuery(query: string): Classification {
  const text = query.trim();

  if (!text) {
    return { klass: 'casual', needsRetrieval: false, tier: 'fast', reason: 'empty' };
  }

  // Casual and math first: they are the cheapest and most specific.
  const casual = matches(CASUAL, text);
  if (casual && text.length < 40) {
    return { klass: 'casual', needsRetrieval: false, tier: 'fast', reason: 'greeting' };
  }

  const math = matches(MATH, text);
  if (math) {
    return { klass: 'math', needsRetrieval: false, tier: 'fast', reason: 'arithmetic-or-conversion' };
  }

  const current = matches(CURRENT, text);
  if (current) {
    return {
      klass: 'current-information',
      needsRetrieval: true,
      tier: 'standard',
      reason: `current-signal:${current.source.slice(0, 40)}`,
    };
  }

  const research = matches(RESEARCH, text);
  if (research) {
    return {
      klass: 'web-research',
      needsRetrieval: true,
      tier: 'standard',
      reason: `research-signal:${research.source.slice(0, 40)}`,
    };
  }

  const coding = matches(CODING, text);
  if (coding) {
    return { klass: 'coding', needsRetrieval: false, tier: 'standard', reason: 'code-signal' };
  }

  const creative = matches(CREATIVE, text);
  if (creative) {
    return { klass: 'creative', needsRetrieval: false, tier: 'standard', reason: 'creative-signal' };
  }

  return {
    klass: 'general-knowledge',
    needsRetrieval: false,
    tier: 'standard',
    reason: 'default',
  };
}
