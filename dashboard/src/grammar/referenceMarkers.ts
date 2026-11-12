// A `[n]` marker in the prose's text. It is linked only in text nodes of the parsed Markdown, so a
// `reports[0]` inside a code span or a fenced block is never rewritten (review F4); a real link or a
// link definition that happens to be numbered is left as authored.
const MARKER = /\[(\d+)\]/g;
const HAS_MARKER = /\[\d+\]/;
// Code spans and fences hold their text in `value`, never in text children, so only an existing
// link's text needs skipping (a marker is never linked inside a link).
const IN_LINK = new Set(['link', 'linkReference']);

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  children?: MdNode[];
}

function splitMarkers(value: string): MdNode[] {
  const nodes: MdNode[] = [];
  let last = 0;
  for (const match of value.matchAll(MARKER)) {
    const at = match.index ?? 0;
    if (at > last) nodes.push({ type: 'text', value: value.slice(last, at) });
    nodes.push({
      type: 'link',
      url: `#reference-${match[1]}`,
      children: [{ type: 'text', value: match[0] }],
    });
    last = at + match[0].length;
  }
  if (last < value.length) nodes.push({ type: 'text', value: value.slice(last) });
  return nodes;
}

function linkMarkers(node: MdNode): void {
  if (!node.children) return;
  node.children = node.children.flatMap((child) => {
    if (child.type === 'text' && child.value && HAS_MARKER.test(child.value)) {
      return splitMarkers(child.value);
    }
    if (!IN_LINK.has(child.type)) linkMarkers(child);
    return [child];
  });
}

/** The remark plugin that turns the prose's `[n]` markers into reference links. */
export function remarkReferenceMarkers() {
  return (tree: unknown) => linkMarkers(tree as MdNode);
}

// IDs are assigned on the parsed headings, including repeated titles, so outline links
// and fragment links have one stable destination per chapter.
export function remarkHeadingIds() {
  return (tree: unknown) => {
    const counts = new Map<string, number>();
    const visit = (node: MdNode & { data?: { hProperties?: Record<string, string> } }) => {
      if (node.type === 'heading') {
        const label = (node.children ?? []).map(textOf).join('');
        const slug =
          label
            .toLowerCase()
            .replace(/[^\p{L}\p{N}]+/gu, '-')
            .replace(/^-|-$/g, '') || 'chapter';
        const number = counts.get(slug) ?? 0;
        counts.set(slug, number + 1);
        node.data = {
          ...node.data,
          hProperties: { ...node.data?.hProperties, id: number ? `${slug}-${number}` : slug },
        };
      }
      node.children?.forEach(visit);
    };
    visit(tree as MdNode);
  };
}
function textOf(node: MdNode): string {
  return node.value ?? node.children?.map(textOf).join('') ?? '';
}
