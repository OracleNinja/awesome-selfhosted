/**
 * Filesystem tools.
 *
 * Every path is resolved through the workspace sandbox, so the model can only
 * ever reach files under JARVIS_WORKSPACE_DIR. There is deliberately no shell
 * tool in v0.1: arbitrary command execution would make every other permission
 * in this system decorative.
 */
import { readFile, writeFile, mkdir, readdir, stat, unlink } from 'node:fs/promises';
import { dirname, relative, join } from 'node:path';
import type { ToolDefinition } from '@jarvis/shared';
import { truncate } from '@jarvis/shared';
import { PathEscapeError, resolveWorkspacePath } from '@jarvis/security';

const MAX_READ_BYTES = 256 * 1024;
const MAX_WRITE_BYTES = 1024 * 1024;

function failure(error: unknown): { ok: false; summary: string; error: string } {
  if (error instanceof PathEscapeError) {
    return { ok: false, summary: 'Refused: path is outside the workspace.', error: error.message };
  }
  const message = (error as NodeJS.ErrnoException).code === 'ENOENT'
    ? 'file not found'
    : (error as Error).message;
  return { ok: false, summary: `File operation failed: ${message}`, error: message };
}

export function fileReadTool(workspaceRoot: string): ToolDefinition {
  return {
    name: 'file_read',
    description:
      'Read a UTF-8 text file from the JARVIS workspace directory. Paths are relative to the workspace root; paths outside it are refused.',
    risk: 'READ',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Workspace-relative file path.', minLength: 1 },
        max_bytes: {
          type: 'integer',
          description: `Maximum bytes to read (default ${MAX_READ_BYTES}).`,
          minimum: 1,
          maximum: MAX_READ_BYTES,
        },
      },
      required: ['path'],
      additionalProperties: false,
    },
    async execute(args) {
      try {
        const target = resolveWorkspacePath(workspaceRoot, String(args.path ?? ''));
        const info = await stat(target);
        if (info.isDirectory()) {
          const entries = await readdir(target, { withFileTypes: true });
          return {
            ok: true,
            summary: `${relative(workspaceRoot, target) || '.'} is a directory with ${entries.length} entr${entries.length === 1 ? 'y' : 'ies'}.`,
            data: {
              directory: relative(workspaceRoot, target) || '.',
              entries: entries.map((entry) => ({
                name: entry.name,
                type: entry.isDirectory() ? 'directory' : 'file',
              })),
            },
          };
        }

        const limit = typeof args.max_bytes === 'number' ? args.max_bytes : MAX_READ_BYTES;
        const buffer = await readFile(target);
        const truncated = buffer.byteLength > limit;
        const content = buffer.subarray(0, limit).toString('utf8');

        return {
          ok: true,
          summary: `Read ${relative(workspaceRoot, target)} (${buffer.byteLength} bytes${truncated ? ', truncated' : ''}).`,
          data: {
            path: relative(workspaceRoot, target),
            bytes: buffer.byteLength,
            truncated,
            content,
          },
        };
      } catch (error) {
        return failure(error);
      }
    },
  };
}

export function fileWriteTool(workspaceRoot: string): ToolDefinition {
  return {
    name: 'file_write',
    description:
      'Write a UTF-8 text file inside the JARVIS workspace directory, creating parent directories as needed. Overwrites existing content unless append is true.',
    risk: 'WRITE',
    // Writing a file is not destructive under the policy, but it is the first
    // action that leaves a trace outside the database, so it is worth a look.
    requiresApproval: true,
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Workspace-relative file path.', minLength: 1 },
        content: { type: 'string', description: 'Full file contents to write.' },
        append: { type: 'boolean', description: 'Append instead of overwriting.', default: false },
      },
      required: ['path', 'content'],
      additionalProperties: false,
    },
    async execute(args) {
      try {
        const content = String(args.content ?? '');
        const bytes = Buffer.byteLength(content, 'utf8');
        if (bytes > MAX_WRITE_BYTES) {
          return {
            ok: false,
            summary: 'Refused: content too large.',
            error: `content is ${bytes} bytes; the limit is ${MAX_WRITE_BYTES}`,
          };
        }

        const target = resolveWorkspacePath(workspaceRoot, String(args.path ?? ''));
        await mkdir(dirname(target), { recursive: true });
        await writeFile(target, content, { encoding: 'utf8', flag: args.append ? 'a' : 'w' });

        return {
          ok: true,
          summary: `${args.append ? 'Appended to' : 'Wrote'} ${relative(workspaceRoot, target)} (${bytes} bytes).`,
          data: { path: relative(workspaceRoot, target), bytes, appended: Boolean(args.append) },
        };
      } catch (error) {
        return failure(error);
      }
    },
  };
}

export function fileListTool(workspaceRoot: string): ToolDefinition {
  return {
    name: 'file_list',
    description: 'List files and directories inside the JARVIS workspace directory.',
    risk: 'READ',
    requiresApproval: false,
    inputSchema: {
      type: 'object',
      properties: {
        path: {
          type: 'string',
          description: 'Workspace-relative directory. Defaults to the workspace root.',
          default: '.',
        },
      },
      required: [],
      additionalProperties: false,
    },
    async execute(args) {
      try {
        const requested = String(args.path ?? '.');
        const target =
          requested === '.' || requested === ''
            ? workspaceRoot
            : resolveWorkspacePath(workspaceRoot, requested);
        await mkdir(workspaceRoot, { recursive: true });
        const entries = await readdir(target, { withFileTypes: true });
        const detailed = await Promise.all(
          entries.map(async (entry) => {
            const info = await stat(join(target, entry.name)).catch(() => null);
            return {
              name: entry.name,
              type: entry.isDirectory() ? 'directory' : 'file',
              bytes: info?.isFile() ? info.size : undefined,
            };
          }),
        );
        return {
          ok: true,
          summary: `${detailed.length} entr${detailed.length === 1 ? 'y' : 'ies'} in ${relative(workspaceRoot, target) || 'workspace root'}.`,
          data: { directory: relative(workspaceRoot, target) || '.', entries: detailed },
        };
      } catch (error) {
        return failure(error);
      }
    },
  };
}

export function fileDeleteTool(workspaceRoot: string): ToolDefinition {
  return {
    name: 'file_delete',
    description:
      'Delete a file from the JARVIS workspace directory. Irreversible — there is no trash. Requires human approval.',
    risk: 'DESTRUCTIVE',
    requiresApproval: true,
    inputSchema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Workspace-relative file path.', minLength: 1 },
      },
      required: ['path'],
      additionalProperties: false,
    },
    async execute(args) {
      try {
        const target = resolveWorkspacePath(workspaceRoot, String(args.path ?? ''));
        const info = await stat(target);
        if (info.isDirectory()) {
          return {
            ok: false,
            summary: 'Refused: target is a directory.',
            error: 'file_delete removes single files only',
          };
        }
        await unlink(target);
        return {
          ok: true,
          summary: `Deleted ${relative(workspaceRoot, target)} (${info.size} bytes).`,
          data: { path: relative(workspaceRoot, target), bytes: info.size },
        };
      } catch (error) {
        return failure(error);
      }
    },
  };
}

export { MAX_READ_BYTES, MAX_WRITE_BYTES, truncate };
