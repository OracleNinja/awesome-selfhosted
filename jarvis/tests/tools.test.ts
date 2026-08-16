/**
 * Tool registry and built-in tool behaviour.
 *
 * These run the real tools against the real store and a real temp workspace.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { writeFileSync, mkdirSync, existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { ToolRegistry, createBuiltinTools, currentTimeTool, webSearchTool } from '@jarvis/tools';
import { PermissionPolicy } from '@jarvis/security';
import { AGENT_DEFINITIONS, JARVIS_AGENT } from '@jarvis/agents';
import { UnavailableSearchProvider } from '@jarvis/providers';
import type { ToolContext, ToolDefinition } from '@jarvis/shared';
import { createTestJarvis, type TestHarness } from './helpers.ts';

const ctx = (overrides: Partial<ToolContext> = {}): ToolContext => ({
  userId: 'user_test',
  conversationId: null,
  agent: 'jarvis',
  ...overrides,
});

describe('tool registry', () => {
  let policy: PermissionPolicy;
  let registry: ToolRegistry;

  beforeEach(() => {
    policy = new PermissionPolicy();
    registry = new ToolRegistry(policy);
  });

  it('registers and retrieves tools', () => {
    registry.register(currentTimeTool);
    expect(registry.size).toBe(1);
    expect(registry.has('current_time')).toBe(true);
    expect(registry.get('current_time')?.risk).toBe('READ');
    expect(registry.get('nope')).toBeUndefined();
  });

  it('refuses duplicate registrations', () => {
    registry.register(currentTimeTool);
    expect(() => registry.register(currentTimeTool)).toThrow(/already registered/);
  });

  it('refuses malformed tool names and non-object schemas', () => {
    const bad: ToolDefinition = { ...currentTimeTool, name: 'Bad Name' };
    expect(() => registry.register(bad)).toThrow(/invalid tool name/);

    const badSchema = {
      ...currentTimeTool,
      name: 'weird',
      inputSchema: { type: 'string' as const },
    };
    expect(() => registry.register(badSchema)).toThrow(/object input schema/);
  });

  it('filters the tool list per agent', () => {
    const harness = createTestJarvis();
    const scoutTools = harness.jarvis.registry.listForAgent(AGENT_DEFINITIONS.scout).map((t) => t.name);
    expect(scoutTools).toContain('web_search');
    expect(scoutTools).toContain('memory_search');
    expect(scoutTools).not.toContain('memory_write');
    expect(scoutTools).not.toContain('file_delete');
    expect(scoutTools).not.toContain('delegate_agent');
    harness.cleanup();
  });

  it('tells the model, in the tool spec, which tools are approval-gated', () => {
    const harness = createTestJarvis();
    const specs = harness.jarvis.registry.specsForAgent(JARVIS_AGENT);
    const destructive = specs.find((spec) => spec.name === 'file_delete');
    expect(destructive?.description).toMatch(/requires human approval/i);
    const readOnly = specs.find((spec) => spec.name === 'current_time');
    expect(readOnly?.description).not.toMatch(/requires human approval/i);
    harness.cleanup();
  });
});

describe('built-in tools', () => {
  let harness: TestHarness;

  beforeEach(() => {
    harness = createTestJarvis();
  });
  afterEach(() => harness.cleanup());

  it('registers the full v0.1 tool set', () => {
    const names = harness.jarvis.registry.list().map((tool) => tool.name);
    expect(names).toEqual(
      [
        'current_time',
        'delegate_agent',
        'file_delete',
        'file_list',
        'file_read',
        'file_write',
        'memory_forget',
        'memory_search',
        'memory_write',
        'task_create',
        'task_list',
        'task_update',
        'web_search',
      ].sort(),
    );
  });

  it('current_time returns a real timestamp and rejects a bad timezone', async () => {
    const ok = await currentTimeTool.execute({ timezone: 'America/New_York' }, ctx());
    expect(ok.ok).toBe(true);
    const data = ok.data as { iso: string; timezone: string };
    expect(Date.parse(data.iso)).toBeGreaterThan(0);
    expect(data.timezone).toBe('America/New_York');

    const bad = await currentTimeTool.execute({ timezone: 'Mars/Olympus' }, ctx());
    expect(bad.ok).toBe(false);
    expect(bad.error).toMatch(/not a valid IANA timezone/);
  });

  it('memory_write then memory_search round-trips through the database', async () => {
    const write = harness.jarvis.registry.get('memory_write')!;
    const search = harness.jarvis.registry.get('memory_search')!;
    const context = ctx({ userId: harness.userId });

    const written = await write.execute(
      { type: 'preference', content: 'Prefers concise answers with no preamble.', importance: 0.8 },
      context,
    );
    expect(written.ok).toBe(true);

    const found = await search.execute({ query: 'how should answers be written' }, context);
    expect(found.ok).toBe(true);
    const results = (found.data as { results: { content: string }[] }).results;
    expect(results[0]?.content).toMatch(/concise answers/);
  });

  it('memory_forget refuses an id belonging to another user', async () => {
    const forget = harness.jarvis.registry.get('memory_forget')!;
    harness.jarvis.store.users.ensure('someone_else');
    const mine = harness.jarvis.store.memories.write({
      userId: 'someone_else',
      type: 'fact',
      content: 'Not yours.',
      source: 'user',
    });
    const result = await forget.execute({ id: mine.id }, ctx({ userId: harness.userId }));
    expect(result.ok).toBe(false);
    expect(harness.jarvis.store.memories.get(mine.id)).not.toBeNull();
  });

  it('task_create, task_list and task_update work together', async () => {
    const create = harness.jarvis.registry.get('task_create')!;
    const list = harness.jarvis.registry.get('task_list')!;
    const update = harness.jarvis.registry.get('task_update')!;
    const context = ctx({ userId: harness.userId });

    const created = await create.execute({ title: 'Restock the walk-in', priority: 'high' }, context);
    expect(created.ok).toBe(true);
    const taskId = (created.data as { id: string }).id;

    const listed = await list.execute({ status: 'open' }, context);
    expect((listed.data as { tasks: unknown[] }).tasks).toHaveLength(1);

    const updated = await update.execute({ id: taskId, status: 'done' }, context);
    expect(updated.ok).toBe(true);
    expect(harness.jarvis.store.tasks.get(taskId)?.status).toBe('done');
  });

  it('task_create rejects an invalid due date', async () => {
    const create = harness.jarvis.registry.get('task_create')!;
    const result = await create.execute(
      { title: 'Bad date', due_at: 'next tuesday-ish' },
      ctx({ userId: harness.userId }),
    );
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/not a valid date/);
  });

  it('file_write and file_read round-trip inside the workspace', async () => {
    const write = harness.jarvis.registry.get('file_write')!;
    const read = harness.jarvis.registry.get('file_read')!;

    const written = await write.execute({ path: 'notes/hello.txt', content: 'JARVIS online.' }, ctx());
    expect(written.ok).toBe(true);
    expect(existsSync(join(harness.workspace, 'notes/hello.txt'))).toBe(true);

    const readBack = await read.execute({ path: 'notes/hello.txt' }, ctx());
    expect((readBack.data as { content: string }).content).toBe('JARVIS online.');
  });

  it('file tools refuse to escape the workspace', async () => {
    const read = harness.jarvis.registry.get('file_read')!;
    const write = harness.jarvis.registry.get('file_write')!;

    const readEscape = await read.execute({ path: '../../etc/passwd' }, ctx());
    expect(readEscape.ok).toBe(false);
    expect(readEscape.error).toMatch(/escapes the workspace/);

    const writeEscape = await write.execute({ path: '/tmp/jarvis-escape.txt', content: 'x' }, ctx());
    expect(writeEscape.ok).toBe(false);
    expect(existsSync('/tmp/jarvis-escape.txt')).toBe(false);
  });

  it('file_read reports a missing file honestly', async () => {
    const read = harness.jarvis.registry.get('file_read')!;
    const result = await read.execute({ path: 'not-here.txt' }, ctx());
    expect(result.ok).toBe(false);
    expect(result.error).toMatch(/file not found/);
  });

  it('file_list lists the workspace', async () => {
    mkdirSync(join(harness.workspace, 'sub'), { recursive: true });
    writeFileSync(join(harness.workspace, 'a.txt'), 'a');
    const list = harness.jarvis.registry.get('file_list')!;
    const result = await list.execute({}, ctx());
    expect(result.ok).toBe(true);
    const names = (result.data as { entries: { name: string }[] }).entries.map((e) => e.name).sort();
    expect(names).toEqual(['a.txt', 'sub']);
  });

  it('file_delete removes a file', async () => {
    const target = join(harness.workspace, 'gone.txt');
    writeFileSync(target, 'temporary');
    const remove = harness.jarvis.registry.get('file_delete')!;
    const result = await remove.execute({ path: 'gone.txt' }, ctx());
    expect(result.ok).toBe(true);
    expect(existsSync(target)).toBe(false);
  });

  it('file_write appends when asked', async () => {
    const write = harness.jarvis.registry.get('file_write')!;
    await write.execute({ path: 'log.txt', content: 'one\n' }, ctx());
    await write.execute({ path: 'log.txt', content: 'two\n', append: true }, ctx());
    expect(readFileSync(join(harness.workspace, 'log.txt'), 'utf8')).toBe('one\ntwo\n');
  });

  it('web_search says it is unavailable instead of inventing results', async () => {
    const tool = webSearchTool(new UnavailableSearchProvider('SEARCH_PROVIDER is not set'));
    const result = await tool.execute({ query: 'current gold price' }, ctx());
    expect(result.ok).toBe(false);
    expect(result.summary).toMatch(/not available/i);
    expect(result.error).toMatch(/SEARCH_PROVIDER/);
    expect(result.data).toEqual({ configured: false });
  });
});
