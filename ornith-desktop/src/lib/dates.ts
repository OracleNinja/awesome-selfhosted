import type { ConversationSummary } from '../../shared/types';

export type GroupLabel = 'Today' | 'Yesterday' | 'Previous 7 days' | 'Earlier';

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfDay(ts: number): number {
  const d = new Date(ts);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

export function groupLabel(updatedAt: number, now = Date.now()): GroupLabel {
  const today = startOfDay(now);
  if (updatedAt >= today) return 'Today';
  if (updatedAt >= today - DAY_MS) return 'Yesterday';
  if (updatedAt >= today - 7 * DAY_MS) return 'Previous 7 days';
  return 'Earlier';
}

const ORDER: GroupLabel[] = ['Today', 'Yesterday', 'Previous 7 days', 'Earlier'];

export function groupConversations(
  conversations: readonly ConversationSummary[],
  now = Date.now(),
): Array<{ label: GroupLabel; items: ConversationSummary[] }> {
  const buckets = new Map<GroupLabel, ConversationSummary[]>();

  for (const c of [...conversations].sort((a, b) => b.updatedAt - a.updatedAt)) {
    const label = groupLabel(c.updatedAt, now);
    const list = buckets.get(label);
    if (list) list.push(c);
    else buckets.set(label, [c]);
  }

  return ORDER.filter((label) => buckets.has(label)).map((label) => ({
    label,
    items: buckets.get(label)!,
  }));
}
