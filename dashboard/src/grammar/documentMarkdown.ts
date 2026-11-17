// The rendered-document mode's HTML policy and heading ids.
//
// A repository document may carry HTML that GitHub renders: a centered head, a picture with a
// width, folded blocks, tables. The tree is parsed with rehype-raw and then cleaned by the same
// element-and-attribute list GitHub's renderer applies (rehype-sanitize's schema): anything not
// on the list is removed, so nothing runs a script, loads a frame/object/embed, carries a style,
// class or event handler, or names a `javascript:` address. Raw ids get GitHub's `user-content-`
// prefix; heading ids are added after cleaning, as GitHub adds them, and stay unprefixed.
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

import type { Options } from "react-markdown";

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

// GitHub's list, plus the video element a document may use for a clip and the source element a
// video may carry. `video` is the only added tag; `style`, `class` and event handlers remain
// absent, which is what keeps a document from covering or restyling the dashboard. `source` is
// already GitHub-listed for audio/video children; its address and type are added here so a
// nested `<source src>` survives cleaning (a `video src` alone loses the clip's type).
const documentSchema = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), "video", "source"],
  attributes: {
    ...defaultSchema.attributes,
    video: [
      "src",
      "poster",
      "controls",
      "width",
      "height",
      "loop",
      "muted",
      "preload",
      "playsInline",
    ],
    source: ["src", "type", "srcSet", "media", "sizes"],
    img: [...(defaultSchema.attributes?.img ?? []), "srcSet", "sizes"],
  },
};

export const documentRehypePlugins: NonNullable<Options["rehypePlugins"]> = [
  rehypeRaw,
  [rehypeSanitize, documentSchema],
  rehypeStripDocumentClasses,
  rehypeDocumentHeadingIds,
];

// The notes reader keeps its ordinary Markdown renderer: it needs document links and media
// addresses, but a note is not a repository document, so raw HTML is not parsed there.
export const noteRehypePlugins: NonNullable<Options["rehypePlugins"]> = [];

// Even the schema's permitted class names (a code fence's language, a task list's marker) can
// restyle the dashboard; the document mode removes every class after cleaning.
function rehypeStripDocumentClasses() {
  return (tree: HastNode) => {
    const visit = (node: HastNode) => {
      if (node.type === "element" && node.properties) {
        delete node.properties.className;
        delete node.properties.class;
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}

function textOf(node: HastNode): string {
  if (node.value) return node.value;
  return (node.children ?? []).map(textOf).join("");
}

function slugify(label: string): string {
  return (
    label
      .toLowerCase()
      .replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-|-$/g, "") || "section"
  );
}

// IDs land on cleaned headings only. Adding them before cleaning would let the sanitizer's
// clobber rule prefix them, which is exactly the prefix a raw HTML id gets and a heading does not.
function rehypeDocumentHeadingIds() {
  return (tree: HastNode) => {
    const counts = new Map<string, number>();
    const visit = (node: HastNode) => {
      if (node.type === "element" && /^h[1-6]$/.test(node.tagName ?? "")) {
        // A raw heading's own id is kept (the sanitizer already prefixed it); only a heading
        // without one gets the slug GitHub gives it.
        if (node.properties?.id) return;
        const slug = slugify(textOf(node));
        const seen = counts.get(slug) ?? 0;
        counts.set(slug, seen + 1);
        node.properties = { ...node.properties, id: seen ? `${slug}-${seen}` : slug };
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
}

// The element an anchor href names, trying the raw-HTML id GitHub prefixes and the heading id.
// Ids are compared as values, never pasted into a selector, so an odd id cannot change the query.
export function documentAnchor(container: ParentNode, id: string): HTMLElement | null {
  const wanted = new Set([id, `user-content-${id}`]);
  for (const node of container.querySelectorAll<HTMLElement>("[id]")) {
    if (wanted.has(node.id)) return node;
  }
  return null;
}
