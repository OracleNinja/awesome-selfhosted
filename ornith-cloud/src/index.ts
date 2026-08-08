import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { loadConfig, validateConfig } from './config.js';
import { log, setLogLevel } from './log.js';
import { createAnthropicProvider } from './ai/anthropic.js';
import { createStubProvider } from './ai/stub.js';
import { createRetriever } from './retrieval/pipeline.js';
import { createHttpFetchProvider } from './retrieval/fetch.js';
import { createNullSearchProvider, createSearxngProvider } from './retrieval/search.js';
import { createHostedSearchProvider } from './retrieval/hosted-search.js';

const config = loadConfig();
setLogLevel(config.logLevel);

const problems = validateConfig(config);
if (problems.length > 0) {
  for (const problem of problems) log.error('config.invalid', { problem });
  process.exit(1);
}

const provider =
  config.aiProvider === 'anthropic'
    ? createAnthropicProvider(config.anthropicApiKey, config.timeouts.providerMs)
    : createStubProvider();

const search =
  config.searchProvider === 'hosted'
    ? createHostedSearchProvider(config.searchApiKey, config.timeouts.searchMs)
    : config.searchProvider === 'searxng'
      ? createSearxngProvider(config.searxngUrl, config.timeouts.searchMs)
      : createNullSearchProvider();

const retriever = createRetriever({
  search,
  // 2 MB read ceiling per page, independent of the character limit applied
  // after extraction.
  fetcher: createHttpFetchProvider(config.timeouts.fetchMs, 2 * 1024 * 1024),
  config,
});

const app = createApp({ config, provider, retriever });

serve({ fetch: app.fetch, port: config.port }, (info) => {
  log.info('gateway.listening', {
    port: info.port,
    aiProvider: provider.name,
    searchProvider: search.name,
    model: config.onlineModel,
    fastModel: config.fastModel,
  });
});
