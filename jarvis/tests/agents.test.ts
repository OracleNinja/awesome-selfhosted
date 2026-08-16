/**
 * Agents: existence, capability boundaries, delegation and charter overrides.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { AGENT_DEFINITIONS, loadAgentDefinitions } from '@jarvis/agents';
import { createTestJarvis, testTurn, type TestHarness } from './helpers.ts';

describe('agent roster', () => {
  it('defines scout, operator, advisor and developer', () => {
    expect(Object.keys(AGENT_DEFINITIONS).sort()).toEqual(['advisor', 'developer', 'operator', 'scout']);
    for (const agent of Object.values(AGENT_DEFINITIONS)) {
      expect(agent.systemPrompt.length).toBeGreaterThan(100);
      expect(agent.purpose).toBeTruthy();
      expect(agent.maxIterations).toBeGreaterThan(0);
    }
  });

  it('gives each agent the authority its role requires and no more', () => {
    expect(AGENT_DEFINITIONS.scout.readOnly).toBe(true);
    expect(AGENT_DEFINITIONS.scout.maxRisk).toBe('READ');

    expect(AGENT_DEFINITIONS.operator.maxRisk).toBe('DESTRUCTIVE');
    expect(AGENT_DEFINITIONS.operator.readOnly).toBe(false);

    // Advisor recommends; developer writes code. Neither may destroy.
    expect(AGENT_DEFINITIONS.advisor.maxRisk).toBe('WRITE');
    expect(AGENT_DEFINITIONS.developer.maxRisk).toBe('WRITE');
    expect(AGENT_DEFINITIONS.developer.allowedTools).not.toContain('file_delete');
  });

  it('gives no agent a shell', () => {
    for (const agent of Object.values(AGENT_DEFINITIONS)) {
      expect(agent.allowedTools.join(' ')).not.toMatch(/shell|exec|bash|command/);
    }
  });
});

describe('agent charters', () => {
  it('loads built-in definitions when no charter files exist', () => {
    const empty = mkdtempSync(join(tmpdir(), 'jarvis-charters-'));
    const result = loadAgentDefinitions(empty);
    expect(Object.keys(result.definitions).sort()).toEqual([
      'advisor',
      'developer',
      'operator',
      'scout',
    ]);
    expect(result.overridden).toHaveLength(0);
    expect(result.errors).toHaveLength(0);
    rmSync(empty, { recursive: true, force: true });
  });

  it('applies an operator-authored override', () => {
    const dir = mkdtempSync(join(tmpdir(), 'jarvis-charters-'));
    mkdirSync(join(dir, 'scout'), { recursive: true });
    writeFileSync(
      join(dir, 'scout', 'agent.json'),
      JSON.stringify({ title: 'Recon', maxIterations: 3, allowedTools: ['current_time'] }),
    );

    const result = loadAgentDefinitions(dir);
    expect(result.overridden).toEqual(['scout']);
    expect(result.definitions.scout!.title).toBe('Recon');
    expect(result.definitions.scout!.maxIterations).toBe(3);
    expect(result.definitions.scout!.allowedTools).toEqual(['current_time']);
    // Untouched fields survive.
    expect(result.definitions.scout!.readOnly).toBe(true);
    rmSync(dir, { recursive: true, force: true });
  });

  it('reports a malformed charter without crashing startup', () => {
    const dir = mkdtempSync(join(tmpdir(), 'jarvis-charters-'));
    mkdirSync(join(dir, 'advisor'), { recursive: true });
    writeFileSync(join(dir, 'advisor', 'agent.json'), '{ not json');

    const result = loadAgentDefinitions(dir);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0]).toMatch(/advisor/);
    expect(result.definitions.advisor!.title).toBe('Advisor');
    rmSync(dir, { recursive: true, force: true });
  });
});

describe('delegation', () => {
  let harness: TestHarness;
  afterEach(() => harness?.cleanup());

  it('runs a sub-agent and returns its report to the orchestrator', async () => {
    harness = createTestJarvis({
      turns: [
        // JARVIS delegates.
        {
          toolCalls: [
            {
              name: 'delegate_agent',
              arguments: { agent: 'scout', task: 'Summarise what tools you have.' },
            },
          ],
        },
        // Scout's turn: answers directly.
        { content: 'FINDINGS: I have read-only tools. CONFIDENCE: high. GAPS: none.' },
        // JARVIS relays.
        { content: 'Scout reports read-only tooling.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'ask scout what it can do',
    });

    expect(turn.toolCalls).toEqual([{ name: 'delegate_agent', status: 'executed' }]);
    expect(turn.reply).toBe('Scout reports read-only tooling.');

    const delegationEvents = turn.events.filter((event) => event.type === 'AGENT_DELEGATION');
    expect(delegationEvents).toHaveLength(1);
    expect(delegationEvents[0]!.agent).toBe('scout');
    expect(turn.events.some((event) => event.type === 'AGENT_RESULT')).toBe(true);

    // The sub-agent got its own system prompt, not JARVIS's.
    const scoutRequest = harness.provider.requests[1]!;
    expect(harness.provider.systemPromptOf(scoutRequest)).toContain('You are SCOUT');

    expect(harness.jarvis.store.agents.get('scout')?.runCount).toBe(1);
  });

  it('lets a sub-agent use its own tools', async () => {
    harness = createTestJarvis({
      turns: [
        {
          toolCalls: [
            { name: 'delegate_agent', arguments: { agent: 'scout', task: 'What time is it?' } },
          ],
        },
        { toolCalls: [{ name: 'current_time', arguments: { timezone: 'UTC' } }] },
        { content: 'FINDINGS: the current time was retrieved.' },
        { content: 'Scout retrieved the time.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'have scout check the time',
    });

    expect(turn.reply).toBe('Scout retrieved the time.');
    const audit = harness.jarvis.store.audit.list(harness.userId);
    const scoutCall = audit.find((entry) => entry.agent === 'scout');
    expect(scoutCall?.tool).toBe('current_time');
  });

  it('refuses a sub-agent tool call outside its authority, and audits the refusal', async () => {
    harness = createTestJarvis({
      turns: [
        {
          toolCalls: [
            { name: 'delegate_agent', arguments: { agent: 'scout', task: 'Delete every file.' } },
          ],
        },
        // Scout tries anyway.
        { toolCalls: [{ name: 'file_delete', arguments: { path: 'x.txt' } }] },
        { content: 'GAPS: I am read-only and cannot delete files.' },
        { content: 'Scout cannot delete files.' },
      ],
    });
    writeFileSync(join(harness.workspace, 'x.txt'), 'still here');

    await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'have scout delete things',
    });

    const refused = harness.jarvis.store.audit
      .list(harness.userId)
      .find((entry) => entry.tool === 'file_delete');
    expect(refused?.approvalState).toBe('denied');
    expect(refused?.error).toMatch(/read-only|not permitted/);

    // And crucially: no approval was ever created for it.
    expect(harness.jarvis.store.approvals.list(harness.userId)).toHaveLength(0);
  });

  it('rejects delegation to an unknown agent', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'delegate_agent', arguments: { agent: 'butler', task: 'Do a thing.' } }] },
        { content: 'There is no such agent.' },
      ],
    });

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'delegate to butler',
    });
    expect(turn.toolCalls[0]!.status).toBe('error');
  });

  it('reports an agent model failure honestly rather than inventing a report', async () => {
    harness = createTestJarvis({
      turns: [
        { toolCalls: [{ name: 'delegate_agent', arguments: { agent: 'advisor', task: 'Advise me.' } }] },
        { content: 'unused' },
        { content: 'The advisor run failed.' },
      ],
    });
    // Fail the second model call — the advisor's.
    const original = harness.provider.chat.bind(harness.provider);
    let calls = 0;
    harness.provider.chat = async (request) => {
      calls += 1;
      if (calls === 2) throw new Error('upstream 503');
      return original(request);
    };

    const turn = await harness.jarvis.orchestrator.handleMessage({
      userId: harness.userId,
      text: 'ask the advisor',
    });

    const toolMessage = harness.provider.requests[harness.provider.requests.length - 1]!.messages.find(
      (m) => m.role === 'tool',
    );
    expect(toolMessage?.content).toMatch(/upstream 503/);
    expect(turn.events.some((event) => event.type === 'ERROR')).toBe(true);
  });

  it('runs an agent directly through the runner', async () => {
    harness = createTestJarvis({
      turns: [{ content: 'SITUATION: all clear. PRIORITIES: none. RECOMMENDATION: rest.' }],
    });

    const result = await harness.jarvis.runner.run(
      harness.jarvis.agents.advisor!,
      'Assess the current situation.',
      { userId: harness.userId, conversationId: null, turn: testTurn() },
    );

    expect(result.agent).toBe('advisor');
    expect(result.stoppedBecause).toBe('complete');
    expect(result.output).toMatch(/SITUATION/);
    expect(result.iterations).toBe(1);
  });

  it('stops an agent at its iteration budget', async () => {
    const turns = Array.from({ length: 20 }, () => ({
      toolCalls: [{ name: 'current_time', arguments: {} }],
    }));
    harness = createTestJarvis({ turns });

    const result = await harness.jarvis.runner.run(harness.jarvis.agents.scout!, 'Loop forever.', {
      userId: harness.userId,
      conversationId: null,
      turn: testTurn(),
    });

    expect(result.stoppedBecause).toBe('max_iterations');
    expect(result.iterations).toBe(AGENT_DEFINITIONS.scout.maxIterations);
  });
});
