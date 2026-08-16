import type { JarvisConfig, ToolDefinition } from '@jarvis/shared';
import type { Store } from '@jarvis/memory';
import type { SearchProvider } from '@jarvis/providers';
import { currentTimeTool } from './builtin/time.ts';
import { memoryForgetTool, memorySearchTool, memoryWriteTool } from './builtin/memory.ts';
import { taskCreateTool, taskListTool, taskUpdateTool } from './builtin/tasks.ts';
import { fileDeleteTool, fileListTool, fileReadTool, fileWriteTool } from './builtin/files.ts';
import { webSearchTool } from './builtin/search.ts';

export * from './registry.ts';
export { currentTimeTool } from './builtin/time.ts';
export { memorySearchTool, memoryWriteTool, memoryForgetTool } from './builtin/memory.ts';
export { taskCreateTool, taskListTool, taskUpdateTool } from './builtin/tasks.ts';
export { fileReadTool, fileWriteTool, fileListTool, fileDeleteTool } from './builtin/files.ts';
export { webSearchTool } from './builtin/search.ts';

export interface ToolDependencies {
  store: Store;
  config: JarvisConfig;
  search: SearchProvider;
}

/**
 * The v0.1 tool set.
 *
 * Adding a tool means adding one file and one line here — the registry, the
 * permission policy, the approval gate and the audit log pick it up
 * automatically from its declared risk level.
 */
export function createBuiltinTools(deps: ToolDependencies): ToolDefinition[] {
  const { store, config, search } = deps;
  return [
    currentTimeTool,
    webSearchTool(search),
    memorySearchTool(store),
    memoryWriteTool(store),
    memoryForgetTool(store),
    taskCreateTool(store),
    taskListTool(store),
    taskUpdateTool(store),
    fileReadTool(config.workspaceDir),
    fileWriteTool(config.workspaceDir),
    fileListTool(config.workspaceDir),
    fileDeleteTool(config.workspaceDir),
  ];
}
