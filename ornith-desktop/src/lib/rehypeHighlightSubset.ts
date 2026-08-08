/**
 * Syntax highlighting with an explicit language subset (SPEC §14.3).
 *
 * `rehype-highlight` does `import {common, createLowlight}` at module level, so
 * its ~37 default grammars are bundled whether or not you pass `languages` —
 * the option only adds to them. Building lowlight directly from our own subset
 * is the only way to actually drop the dead grammars from the bundle.
 */
import { createLowlight } from 'lowlight';
import { HIGHLIGHT_LANGUAGES } from './languages';

const lowlight = createLowlight(HIGHLIGHT_LANGUAGES);

interface HastNode {
  type: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
  value?: string;
}

function textOf(node: HastNode): string {
  if (node.type === 'text') return node.value ?? '';
  return (node.children ?? []).map(textOf).join('');
}

function classNames(node: HastNode): string[] {
  const raw = node.properties?.className;
  if (Array.isArray(raw)) return raw.map(String);
  if (typeof raw === 'string') return raw.split(/\s+/);
  return [];
}

function walk(node: HastNode, parent: HastNode | null): void {
  if (node.type === 'element' && node.tagName === 'code' && parent?.tagName === 'pre') {
    const classes = classNames(node);
    const language = classes.find((c) => c.startsWith('language-'))?.slice('language-'.length);
    const source = textOf(node);

    if (source.trim()) {
      try {
        const result =
          language && lowlight.registered(language)
            ? lowlight.highlight(language, source)
            : lowlight.highlightAuto(source);

        node.properties = { ...node.properties, className: [...classes, 'hljs'] };
        node.children = result.children as unknown as HastNode[];
      } catch {
        // An unregistered or failing grammar leaves the block as plain text,
        // which is a perfectly good outcome.
      }
    }
    return; // Never descend into a block we just replaced.
  }

  for (const child of node.children ?? []) walk(child, node);
}

export function rehypeHighlightSubset() {
  return (tree: HastNode): void => {
    walk(tree, null);
  };
}
