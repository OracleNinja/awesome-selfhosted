import type { ToolDefinition } from '@jarvis/shared';

/**
 * Current time.
 *
 * Small, but load-bearing: a model with no clock will otherwise guess at dates,
 * and every scheduling or "how long ago" answer becomes fiction.
 */
export const currentTimeTool: ToolDefinition = {
  name: 'current_time',
  description:
    'Get the current date and time. Use this instead of guessing the date; the model has no clock of its own.',
  risk: 'READ',
  requiresApproval: false,
  inputSchema: {
    type: 'object',
    properties: {
      timezone: {
        type: 'string',
        description: 'IANA timezone name, e.g. "America/New_York". Defaults to UTC.',
      },
    },
    required: [],
    additionalProperties: false,
  },
  async execute(args) {
    const timezone = typeof args.timezone === 'string' && args.timezone ? args.timezone : 'UTC';
    const date = new Date();
    let formatted: string;
    try {
      formatted = new Intl.DateTimeFormat('en-GB', {
        timeZone: timezone,
        dateStyle: 'full',
        timeStyle: 'long',
      }).format(date);
    } catch {
      return {
        ok: false,
        summary: `Unknown timezone "${timezone}".`,
        error: `"${timezone}" is not a valid IANA timezone name.`,
      };
    }

    return {
      ok: true,
      summary: `${formatted} (${timezone})`,
      data: {
        iso: date.toISOString(),
        epochMs: date.getTime(),
        timezone,
        formatted,
      },
    };
  },
};
