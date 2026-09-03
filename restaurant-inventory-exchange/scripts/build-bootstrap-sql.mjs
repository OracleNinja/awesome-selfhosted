#!/usr/bin/env node
/**
 * Concatenates every migration into supabase/bootstrap.sql, so a new project
 * can be set up with one paste into the Supabase SQL editor instead of seven.
 *
 * The bundle is generated, never hand-edited. `npm run db:bundle` rebuilds it
 * and a test fails if the checked-in copy has drifted from the migrations.
 */
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const MIGRATIONS = join(ROOT, 'supabase', 'migrations');
export const BUNDLE_PATH = join(ROOT, 'supabase', 'bootstrap.sql');

export function buildBundle() {
  const files = readdirSync(MIGRATIONS)
    .filter((file) => file.endsWith('.sql'))
    .sort();

  const parts = [
    `-- Restaurant Inventory Exchange: complete database setup.
--
-- GENERATED FILE. Rebuild with \`npm run db:bundle\`; edit the files in
-- supabase/migrations/ instead.
--
-- Paste the whole thing into the Supabase SQL editor of a NEW project and run
-- it once. It creates the schema, the row level security policies, the read
-- models, the business functions, and the starting locations and catalog.
--
-- Bundled migrations:
${files.map((file) => `--   ${file}`).join('\n')}
`,
  ];

  for (const file of files) {
    const sql = readFileSync(join(MIGRATIONS, file), 'utf8').trimEnd();
    parts.push(`\n-- ${'='.repeat(74)}\n-- ${file}\n-- ${'='.repeat(74)}\n\n${sql}\n`);
  }

  return parts.join('');
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const bundle = buildBundle();
  writeFileSync(BUNDLE_PATH, bundle);
  console.log(`wrote supabase/bootstrap.sql (${bundle.split('\n').length} lines)`);
}
