/**
 * The approval workflow and the audit log.
 *
 * These are the tests that matter most: they assert that an approval-gated
 * action genuinely does not run until a human says so, that the model is told
 * the truth about it, and that every attempt is recorded either way.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createTestJarvis, type TestHarness } from './helpers.ts';

describe('approval workflow', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('holds a DESTRUCTIVE action, then executes it only after approval', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'doomed.txt' } }] },
        { content: 'I need your approval to delete doomed.txt.' },
        { content: 'Deleted doomed.txt as approved.' },
      ],
    });
    const target = join(harness.workspace, 'doomed.txt');
    writeFileSync(target, 'delete me');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete doomed.txt',
    });

    // Nothing happened yet.
    expect(existsSync(target)).toBe(true);
    expect(turn.toolCalls[0]!.status).toBe('awaiting_approval');
    expect(turn.pendingApprovals).toHaveLength(1);

    const approval = turn.pendingApprovals[0]!;
    expect(approval.risk).toBe('DESTRUCTIVE');
    expect(approval.tool).toBe('file_delete');
    expect(approval.state).toBe('pending');
    expect(approval.description).toContain('doomed.txt');
    expect(approval.arguments).toEqual({ path: 'doomed.txt' });

    // The model was told, in no uncertain terms, that it did not happen.
    const toolMessage = harness.provider.requests[1]!.messages.find((m) => m.role === 'tool');
    expect(toolMessage?.content).toMatch(/APPROVAL REQUIRED/);
    expect(toolMessage?.content).toMatch(/has NOT been executed/);

    // Now approve.
    const outcome = await harness.jarvis.approve(approval.id, harness.userId);
    expect(outcome.ok).toBe(true);
    expect(outcome.state).toBe('approved');
    expect(existsSync(target)).toBe(false);
    expect(harness.jarvis.store.approvals.get(approval.id)?.state).toBe('approved');
  });

  it('never shows the internal approval note as a user message or a reply', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'note.txt' } }] },
        { content: 'Awaiting approval.' },
        // The model returns nothing usable on the follow-up turn.
        { content: '' },
      ],
    });
    writeFileSync(join(harness.workspace, 'note.txt'), 'x');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete note.txt',
    });
    const outcome = await harness.jarvis.approve(turn.pendingApprovals[0]!.id, harness.userId);

    // The user-facing reply is a clean sentence, not the internal instruction.
    expect(outcome.turn?.reply).not.toMatch(/Report this outcome to the user/);
    expect(outcome.turn?.reply).toMatch(/Approved and executed/);

    // The note is stored as `system`, so no user turn was fabricated.
    const stored = harness.jarvis.store.messages.list(turn.conversationId);
    const noteMessage = stored.find((message) => message.content.includes('The user APPROVED'));
    expect(noteMessage?.role).toBe('system');
    expect(stored.filter((m) => m.role === 'user').map((m) => m.content)).toEqual(['delete note.txt']);
  });

  it('offers no tools on the post-approval turn, so the action cannot be redone', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'once-only.txt' } }] },
        { content: 'Awaiting approval.' },
        { content: 'Deleted, as approved.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'once-only.txt'), 'x');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete once-only.txt',
    });
    await harness.jarvis.approve(turn.pendingApprovals[0]!.id, harness.userId);

    const followUp = harness.provider.requests[harness.provider.requests.length - 1]!;
    expect(followUp.tools).toBeUndefined();
  });

  it('denies an action and never executes it', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'keep.txt' } }] },
        { content: 'Awaiting your decision.' },
        { content: 'Understood — leaving it in place.' },
      ],
    });
    const target = join(harness.workspace, 'keep.txt');
    writeFileSync(target, 'keep me');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete keep.txt',
    });
    const approval = turn.pendingApprovals[0]!;

    const outcome = await harness.jarvis.deny(approval.id, harness.userId, 'still needed');
    expect(outcome.state).toBe('denied');
    expect(existsSync(target)).toBe(true);
    expect(harness.jarvis.store.approvals.get(approval.id)?.state).toBe('denied');
    expect(harness.jarvis.store.approvals.get(approval.id)?.note).toBe('still needed');
  });

  it('cannot be decided twice', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'once.txt' } }] },
        { content: 'Pending.' },
        { content: 'Done.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'once.txt'), 'x');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete once.txt',
    });
    const approvalId = turn.pendingApprovals[0]!.id;

    expect((await harness.jarvis.approve(approvalId, harness.userId)).ok).toBe(true);
    const replay = await harness.jarvis.approve(approvalId, harness.userId);
    expect(replay.ok).toBe(false);
    expect(replay.state).toBe('unavailable');
    expect(replay.message).toMatch(/approved/);
  });

  it('refuses to execute an expired approval', async () => {
    harness = createTestJarvis({
      config: { approvalTimeoutSeconds: 0 },
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'stale.txt' } }] },
        { content: 'Pending.' },
      ],
    });
    const target = join(harness.workspace, 'stale.txt');
    writeFileSync(target, 'x');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete stale.txt',
    });
    const approvalId = turn.pendingApprovals[0]?.id;

    // With a zero-second window the request is already expired on read.
    if (approvalId) {
      const outcome = await harness.jarvis.approve(approvalId, harness.userId);
      expect(outcome.ok).toBe(false);
    }
    expect(existsSync(target)).toBe(true);
    expect(harness.jarvis.store.approvals.listPending(harness.userId)).toHaveLength(0);
  });

  it('refuses an approval belonging to another user', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'theirs.txt' } }] },
        { content: 'Pending.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'theirs.txt'), 'x');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete theirs.txt',
    });
    const approvalId = turn.pendingApprovals[0]!.id;

    const outcome = await harness.jarvis.approve(approvalId, 'someone_else');
    expect(outcome.ok).toBe(false);
    expect(existsSync(join(harness.workspace, 'theirs.txt'))).toBe(true);
  });

  it('gates a tool that opts in to approval at WRITE risk', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_write', arguments: { path: 'new.txt', content: 'hello' } }] },
        { content: 'Waiting for approval to write the file.' },
        { content: 'Written.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'write new.txt',
    });
    expect(turn.pendingApprovals).toHaveLength(1);
    expect(existsSync(join(harness.workspace, 'new.txt'))).toBe(false);

    await harness.jarvis.approve(turn.pendingApprovals[0]!.id, harness.userId);
    expect(existsSync(join(harness.workspace, 'new.txt'))).toBe(true);
  });

  it('does not gate READ or ordinary WRITE actions', async () => {
    harness = createTestJarvis({
      turns: [
        {
          toolCalls: [
            { name: 'current_time', arguments: {} },
            { name: 'memory_write', arguments: { type: 'fact', content: 'Ungated write path works.' } },
          ],
        },
        { content: 'Both done.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'note the time and remember it',
    });

    expect(turn.pendingApprovals).toHaveLength(0);
    expect(turn.toolCalls.every((call) => call.status === 'executed')).toBe(true);
    expect(harness.jarvis.store.memories.count(harness.userId)).toBe(1);
  });
});

describe('audit log', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('records successful invocations with timing and risk', async () => {
    harness = createTestJarvis({
      turns: [{ toolCalls: [{ name: 'current_time', arguments: {} }] }, { content: 'done' }],
    });

    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'time?' });

    const audit = harness.jarvis.store.audit.list(harness.userId);
    expect(audit).toHaveLength(1);
    const entry = audit[0]!;
    expect(entry.tool).toBe('current_time');
    expect(entry.agent).toBe('jarvis');
    expect(entry.risk).toBe('READ');
    expect(entry.approvalState).toBe('not_required');
    expect(entry.error).toBeNull();
    expect(entry.result).toBeTruthy();
    expect(entry.durationMs).toBeGreaterThanOrEqual(0);
    expect(Date.parse(entry.timestamp)).toBeGreaterThan(0);
  });

  it('records failures, refusals and pending approvals — not just successes', async () => {
    harness = createTestJarvis({
      turns: [
        {
          toolCalls: [
            { name: 'file_read', arguments: { path: 'nope.txt' } },
            { name: 'does_not_exist', arguments: {} },
            { name: 'file_delete', arguments: { path: 'x.txt' } },
          ],
        },
        { content: 'Report.' },
      ],
    });

    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'try things' });

    const audit = harness.jarvis.store.audit.list(harness.userId);
    const byTool = Object.fromEntries(audit.map((entry) => [entry.tool, entry]));

    expect(byTool.file_read?.error).toMatch(/file not found/);
    expect(byTool.does_not_exist?.error).toMatch(/does not exist/);
    expect(byTool.file_delete?.approvalState).toBe('pending');
    expect(byTool.file_delete?.approvalId).toBeTruthy();
  });

  it('records a denial, with the action never executed', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'audit.txt' } }] },
        { content: 'Pending.' },
        { content: 'Acknowledged.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'audit.txt'), 'x');

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delete audit.txt',
    });
    await harness.jarvis.deny(turn.pendingApprovals[0]!.id, harness.userId, 'no');

    const denied = harness.jarvis.store.audit
      .list(harness.userId)
      .filter((entry) => entry.approvalState === 'denied');
    expect(denied).toHaveLength(1);
    expect(denied[0]!.error).toMatch(/denied by user/);
  });

  it('redacts credential-shaped values before they reach the log', async () => {
    harness = createTestJarvis({
      turns: [
        {
          toolCalls: [
            {
              name: 'memory_write',
              arguments: { type: 'fact', content: 'The key is nvapi-abcdef123456789', api_key: 'sk-secret-value-1234' },
            },
          ],
        },
        { content: 'Saved.' },
      ],
    });

    await harness.jarvis.orchestrator.handleMessage({ userId: harness.userId, text: 'remember the key' });

    const serialised = JSON.stringify(harness.jarvis.store.audit.list(harness.userId));
    expect(serialised).not.toContain('sk-secret-value-1234');
    expect(serialised).not.toContain('nvapi-abcdef123456789');
  });

  it('mirrors every entry into tool_calls for per-conversation history', async () => {
    harness = createTestJarvis({
      turns: [{ toolCalls: [{ name: 'current_time', arguments: {} }] }, { content: 'ok' }],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'time?',
    });

    const rows = harness.jarvis.store.db
      .prepare('SELECT * FROM tool_calls WHERE conversation_id = ?')
      .all(turn.conversationId) as { tool: string; state: string }[];
    expect(rows).toHaveLength(1);
    expect(rows[0]!.tool).toBe('current_time');
    expect(rows[0]!.state).toBe('ok');
  });
});
