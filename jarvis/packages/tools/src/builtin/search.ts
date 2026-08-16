import type { ToolDefinition } from '@jarvis/shared';
import { truncate } from '@jarvis/shared';
import { ProviderUnavailableError, type SearchProvider } from '@jarvis/providers';

/**
 * Web search.
 *
 * When no search provider is configured this tool returns `ok: false` with an
 * explicit "not configured" message. That is deliberate: the model is told the
 * capability is missing so it says so, rather than answering from memory and
 * presenting it as a search result.
 */
export function webSearchTool(provider: SearchProvider): ToolDefinition {
  return {
    name: 'web_search',
    description:
      'Search the public web and return titles, URLs and snippets. Read-only. Returns an explicit failure if no search provider is configured — in that case tell the user web search is unavailable instead of answering from memory.',
    risk: 'READ',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: 'Search query.', minLength: 2, maxLength: 500 },
        limit: { type: 'integer', description: 'Maximum results (1-10).', minimum: 1, maximum: 10, default: 5 },
      },
      required: ['query'],
      additionalProperties: false,
    },
    async execute(args, ctx) {
      const query = String(args.query ?? '');
      const limit = typeof args.limit === 'number' ? args.limit : 5;

      if (!provider.isAvailable()) {
        const reason = provider.status().reason ?? 'no search provider is configured';
        return {
          ok: false,
          summary: 'Web search is not available.',
          error: reason,
          data: { configured: false },
        };
      }

      try {
        const options: { limit: number; signal?: AbortSignal } = { limit };
        if (ctx.signal) options.signal = ctx.signal;
        const results = await provider.search(query, options);

        if (results.length === 0) {
          return { ok: true, summary: `No results for "${truncate(query, 60)}".`, data: { query, results: [] } };
        }
        return {
          ok: true,
          summary: `${results.length} result${results.length === 1 ? '' : 's'} for "${truncate(query, 60)}" via ${provider.id}.`,
          data: { query, provider: provider.id, results },
        };
      } catch (error) {
        if (error instanceof ProviderUnavailableError) {
          return { ok: false, summary: 'Web search is not available.', error: error.message };
        }
        return {
          ok: false,
          summary: 'Web search failed.',
          error: (error as Error).message,
        };
      }
    },
  };
}
