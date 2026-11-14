import { memo, type ComponentProps } from 'react';
import { useDashboard } from '../data/store';
import type { TaskDocumentResources } from '../data/taskDocuments';
import { remarkCachedParse } from './markdownParseCache';
import ReactMarkdown, { type Components, type ExtraProps, type Options } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkReferenceMarkers, remarkHeadingIds } from './referenceMarkers';
import { documentComponents, type DocumentContext } from './DocumentMarkdown';
import { documentRehypePlugins, noteRehypePlugins } from './documentMarkdown';

import { css } from '../../styled-system/css';
import { isRequirementAddress, resolveRequirementAddress } from '../data/requirements';
import { useTaskRequirementLinks } from './TaskRequirementLinks';

// Markdown rendering for task/master prose (slice 6g). The task-doc section bodies are GFM markdown
// (tables, blockquotes, **bold**, `code`, lists, links) folded from the original task.md; without a
// renderer they showed raw. Styled via Panda descendant selectors so we don't hand-wrap every node;
// a wide table gets a horizontal scroll box so it can't blow out the detail panel. react-markdown
// renders no raw HTML by default, so this is safe for arbitrary doc content.
const box = css({
  fontSize: '0.86rem',
  lineHeight: '1.55',
  color: 'ink',
  '& > :first-child': { marginTop: '0' },
  '& > :last-child': { marginBottom: '0' },
  '& p': { margin: '0 0 0.5rem', maxWidth: '78ch' },
  '& strong': { fontWeight: '600', color: 'ink' },
  '& em': { fontStyle: 'italic' },
  '& a': { color: 'cyan', textDecoration: 'underline' },
  '& del': { textDecoration: 'line-through', color: 'muted' },
  '& code': {
    fontSize: '0.86em',
    background: 'bg',
    paddingInline: '0.25rem',
    paddingBlock: '0.05rem',
    borderRadius: '2px',
  },
  '& pre': {
    background: 'bg',
    padding: '0.5rem 0.6rem',
    borderRadius: '3px',
    overflow: 'auto',
    margin: '0.3rem 0',
  },
  '& pre code': { background: 'transparent', paddingInline: '0', fontSize: '0.78rem' },
  '& blockquote': {
    margin: '0.4rem 0',
    paddingLeft: '0.6rem',
    borderLeftWidth: '2px',
    borderLeftStyle: 'solid',
    borderLeftColor: 'grid',
    color: 'muted',
  },
  '& ul, & ol': { margin: '0 0 0.5rem', paddingLeft: '1.2rem', display: 'grid', gap: '0.15rem' },
  '& li': { maxWidth: '78ch' },
  '& h1, & h2, & h3, & h4': {
    fontSize: '0.78rem',
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    color: 'amber',
    margin: '0.6rem 0 0.3rem',
  },
  '&[data-document-headings=true] h1': {
    fontSize: '1.35rem',
    textTransform: 'none',
    letterSpacing: '0',
    margin: '0 0 0.6rem',
  },
  '&[data-document-headings=true] h2': {
    fontSize: '1.05rem',
    textTransform: 'none',
    marginTop: '1.1rem',
  },
  '&[data-document-headings=true] h3': { fontSize: '0.9rem', marginTop: '0.8rem' },
  '&[data-document-headings=true] h4, &[data-document-headings=true] h5, &[data-document-headings=true] h6':
    { fontSize: '0.8rem' },
  '& table': { borderCollapse: 'collapse', fontSize: '0.8rem' },
  '& th, & td': {
    borderWidth: '1px',
    borderStyle: 'solid',
    borderColor: 'grid',
    padding: '0.2rem 0.45rem',
    textAlign: 'left',
    verticalAlign: 'top',
  },
  '& th': { color: 'amber', fontWeight: '600', whiteSpace: 'nowrap' },
});
const tableScroll = css({ overflowX: 'auto', maxWidth: '100%', margin: '0.3rem 0' });
const internalLink = css({
  font: 'inherit',
  color: 'cyan',
  background: 'transparent',
  border: '0',
  padding: '0',
  cursor: 'pointer',
  textDecoration: 'underline',
});

// Inline variant for list items / decision cells: the markdown is a single phrase, so unwrap the
// paragraph (no block margins, no <p>-in-<span>) and keep bold/code/links/em.
const inlineBox = css({
  '& code': { background: 'bg', paddingInline: '0.2rem', borderRadius: '2px', fontSize: '0.86em' },
  '& strong': { fontWeight: '600', color: 'ink' },
  '& em': { fontStyle: 'italic' },
  '& del': { textDecoration: 'line-through', color: 'muted' },
  '& a': { color: 'cyan', textDecoration: 'underline' },
});

const blockComponents: Components = {
  // Keep the table a real <table> but inside a scroll box so wide tables never overflow the panel.
  table({ node, ...props }) {
    void node; // strip the AST node react-markdown passes; only DOM props go to <table>
    return (
      <div className={tableScroll}>
        <table {...props} />
      </div>
    );
  },
};

const inlineComponents: Components = {
  // Unwrap the single paragraph markdown produces for a one-line phrase.
  p: ({ children }) => <>{children}</>,
};

function RequirementAnchor({
  node,
  href = '',
  children: linkChildren,
  ...props
}: ComponentProps<'a'> & ExtraProps) {
  const requirementLinks = useTaskRequirementLinks();
  void node;
  const target = requirementLinks
    ? resolveRequirementAddress(href, requirementLinks.requirements)
    : undefined;
  if (target) {
    return (
      <button
        type="button"
        className={internalLink}
        data-testid="requirement-link"
        title={`open requirements/${target}`}
        onClick={() => requirementLinks?.open(target)}
      >
        {linkChildren}
      </button>
    );
  }
  if (isRequirementAddress(href)) {
    return <span data-testid="requirement-link-refused">{linkChildren}</span>;
  }
  // Rule 9: no rendered Markdown may move the dashboard's own tab. An outside address opens a
  // new tab; an anchor stays in the page; a relative address is text that says it is not followed.
  if (/^https?:/i.test(href))
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" {...props}>
        {linkChildren}
      </a>
    );
  if (href.startsWith("#"))
    return (
      <a href={href} {...props}>
        {linkChildren}
      </a>
    );
  return (
    <a
      href={href}
      {...props}
      data-testid="link-not-followed"
      title="not a file this page opens"
      onClick={(event) => event.preventDefault()}
    >
      {linkChildren}
    </a>
  );
}

function documentHtmlAllowed(documentContext: DocumentContext | undefined): boolean {
  return documentContext?.allowHtml !== false;
}

// memo: the projection ticks (SSE) re-render DetailPanel every ~second, but section bodies are
// stable strings — without memo, react-markdown re-parses every body on every tick (scroll jank).
// children is a primitive string, so the default shallow prop compare is exactly right here.
export const Markdown = memo(function Markdown({
  children,
  inline = false,
  referenceMarkers = false,
  headingIds = false,
  components: overrides,
  document: documentContext,
}: {
  children: string;
  inline?: boolean;
  referenceMarkers?: boolean;
  headingIds?: boolean;
  components?: Components;
  // A repository document: HTML is cleaned to GitHub's list, headings get ids, and links and
  // media resolve against the document's repository. Absent is the dashboard's own renderer for
  // cards, task documents and notes, which never renders raw HTML.
  document?: DocumentContext;
}) {
  const resources = useDashboard((state) => state.taskDocuments);
  const setup = markdownSetup({
    inline,
    overrides,
    referenceMarkers,
    headingIds,
    resources: !overrides && children.length >= 1000 ? resources : undefined,
    documentLinks: documentContext ? documentComponents(documentContext) : null,
    documentHtml: documentHtmlAllowed(documentContext),
  });
  const content = (
    <ReactMarkdown
      remarkPlugins={setup.plugins}
      rehypePlugins={setup.rehypePlugins}
      components={setup.components}
    >
      {children}
    </ReactMarkdown>
  );
  if (inline) return <span className={inlineBox}>{content}</span>;
  return (
    <div
      className={box}
      data-document-headings={headingIds ? true : undefined}
      data-document-root={documentContext ? true : undefined}
    >
      {content}
    </div>
  );
});

function markdownSetup({
  inline,
  overrides,
  referenceMarkers,
  headingIds,
  resources,
  documentLinks,
  documentHtml,
}: {
  inline: boolean;
  overrides: Components | undefined;
  referenceMarkers: boolean;
  headingIds: boolean;
  resources: TaskDocumentResources | undefined;
  documentLinks: Components | null;
  documentHtml: boolean;
}): {
  components: Components;
  plugins: NonNullable<Options['remarkPlugins']>;
  rehypePlugins: NonNullable<Options['rehypePlugins']> | undefined;
} {
  if (documentLinks)
    return {
      components: { ...blockComponents, ...documentLinks, ...overrides },
      plugins: [remarkGfm],
      rehypePlugins: documentHtml ? documentRehypePlugins : noteRehypePlugins,
    };
  return {
    components: inline
      ? { ...inlineComponents, a: RequirementAnchor, ...overrides }
      : { ...blockComponents, a: RequirementAnchor, ...overrides },
    plugins: markdownPlugins(referenceMarkers, headingIds, resources),
    rehypePlugins: undefined,
  };
}

function markdownPlugins(
  referenceMarkers: boolean,
  headingIds: boolean,
  resources: TaskDocumentResources | undefined,
): NonNullable<Options['remarkPlugins']> {
  const plugins: NonNullable<Options['remarkPlugins']> = [remarkGfm];
  if (resources) plugins.push([remarkCachedParse, resources]);
  if (referenceMarkers) plugins.push(remarkReferenceMarkers);
  if (headingIds) plugins.push(remarkHeadingIds);
  return plugins;
}
