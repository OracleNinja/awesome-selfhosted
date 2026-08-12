#!/usr/bin/env node
/**
 * Pre-flight gate: verify the configured Anthropic model IDs actually exist
 * before deploying. Run it locally (or in CI) ahead of `fly deploy`.
 *
 * Reads ANTHROPIC_API_KEY, ORNITH_ONLINE_MODEL and ORNITH_FAST_MODEL from
 * .env if present, then falls back to the process environment. It validates
 * against the live Models API and reports — it never changes configuration,
 * and it will not "fix" a missing or deprecated model ID by substituting
 * another one.
 *
 * Usage:
 *   node scripts/preflight-models.mjs
 *
 * Exit codes:
 *   0  both configured models were confirmed by the API
 *   1  a configured model was not found (or the key is missing/unusable)
 */

import { readFileSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import process from 'node:process';

function loadDotEnv(file) {
  const out = {};
  if (!existsSync(file)) return out;
  for (const raw of readFileSync(file, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1).trim();
    if (value.startsWith('"') && value.endsWith('"')) {
      out[key] = JSON.parse(value);
    } else {
      out[key] = value;
    }
  }
  return out;
}

const env = { ...loadDotEnv(resolve('.env')), ...process.env };

const apiKey = env.ANTHROPIC_API_KEY ?? '';
const models = [env.ORNITH_ONLINE_MODEL, env.ORNITH_FAST_MODEL].filter(Boolean);
const configured = [...new Set(models)];

if (!apiKey) {
  console.error(
    'FAIL: ANTHROPIC_API_KEY is not set. Pre-flight requires a real key to ' +
      'list models. Nothing was deployed.',
  );
  process.exit(1);
}

if (configured.length === 0) {
  console.error(
    'FAIL: neither ORNITH_ONLINE_MODEL nor ORNITH_FAST_MODEL is set. ' +
      'Nothing was deployed.',
  );
  process.exit(1);
}

console.log(
  `Checking ${configured.length} configured model ID(s) against the Models API:`,
);
for (const id of configured) console.log(`  - ${id}`);

const res = await fetch('https://api.anthropic.com/v1/models', {
  headers: {
    'x-api-key': apiKey,
    'anthropic-version': '2023-06-01',
  },
});

if (!res.ok) {
  console.error(`FAIL: Models API returned HTTP ${res.status}.`);
  console.error(
    '  Check ANTHROPIC_API_KEY. Note: a scoped/limited key may legitimately ' +
      'be rejected here even when chat calls would work.',
  );
  process.exit(1);
}

const body = (await res.json()) ?? { data: [] };
const available = new Set((body.data ?? []).map((m) => m.id));

const missing = configured.filter((id) => !available.has(id));
if (missing.length > 0) {
  console.error('FAIL: the following configured model ID(s) were not found:');
  for (const id of missing) console.error(`  - ${id}`);
  console.error(
    '  No model ID was substituted. Correct the IDs in your configuration ' +
      'and re-run this gate before deploying.',
  );
  process.exit(1);
}

console.log('PASS: all configured model IDs are available.');
