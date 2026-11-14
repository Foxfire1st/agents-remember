// The rendered repository document's links and media, resolved against the document's repository.
//
// A link to another file opens that file in the viewer; an anchor alone scrolls to its heading; an
// outside https address opens a new tab; a link that names no file of the repository is drawn as
// text that says so. A picture or video named by a repository path is drawn from the bytes route;
// an outside https address is drawn directly without a referrer; anything else is not fetched and
// its alternative text stands in its place.
import { useEffect, useState, type ComponentProps, type ReactNode } from "react";

import { css } from "../../styled-system/css";
import { resolvePath, resolveRepositoryPath } from "../data/files";
import { fileMediaUrl, mediaForPath } from "../data/media";
import { documentAnchor } from "./documentMarkdown";
import { parseSrcSet } from "./srcset";

export interface DocumentContext {
  repo: string;
  scope: string;
  path: string;
  openFile: (path: string, anchor?: string) => void;
  // A document whose paths are not repository paths (the notes reader) supplies its own existence
  // check and media address; the file viewer leaves them undefined and both use the repository.
  resolveLink?: (path: string) => Promise<{ exists: boolean; kind?: string }>;
  mediaUrl?: (path: string) => string;
  // A repository document may carry the HTML GitHub renders; a note may not. The notes reader
  // still gets links and picture/video addresses but leaves raw HTML unparsed.
  allowHtml?: boolean;
}

const link = css({
  font: "inherit",
  color: "cyan",
  background: "transparent",
  border: "0",
  padding: "0",
  cursor: "pointer",
  textDecoration: "underline",
});
const plain = css({ color: "muted" });
const mediaImage = css({ maxWidth: "100%" });
const mediaVideo = css({ maxWidth: "100%", background: "bg" });

function isFetchableExternal(src: string): boolean {
  return /^https:/i.test(src);
}

export function scrollToDocumentAnchor(container: ParentNode | null, id: string): void {
  if (!container || !id) return;
  const target = documentAnchor(container, id);
  target?.scrollIntoView({ block: "start" });
}

type LinkTarget =
  | { kind: "anchor"; value: string }
  | { kind: "external" }
  | { kind: "relative"; value: string }
  | { kind: "missing" };

function decode(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

function classifyLink(href: string, documentPath: string): LinkTarget {
  if (href.startsWith("#")) {
    const anchor = decode(href.slice(1));
    return anchor === null ? { kind: "missing" } : { kind: "anchor", value: anchor };
  }
  if (/^https?:/i.test(href)) return { kind: "external" };
  const relative = resolveRepositoryPath(documentPath, href);
  return relative ? { kind: "relative", value: relative } : { kind: "missing" };
}

function anchorOf(href: string): string | undefined {
  const at = href.indexOf("#");
  if (at <= 0) return undefined;
  return decode(href.slice(at + 1)) ?? undefined;
}

// A relative link resolves once. Until the answer lands it is plain text, so a click can never
// land nowhere; a path the repository does not hold stays plain text that says so.
function DocumentLink({
  href = "",
  children,
  context,
}: {
  href?: string;
  children: ReactNode;
  context: DocumentContext;
}) {
  const target = classifyLink(href, context.path);
  const [state, setState] = useState<"checking" | "file" | "missing">(
    target.kind === "relative" ? "checking" : target.kind === "missing" ? "missing" : "file",
  );

  const relative = target.kind === "relative" ? target.value : null;
  const resolveLink = context.resolveLink;

  useEffect(() => {
    if (!relative) return;
    let alive = true;
    setState("checking");
    const resolved = resolveLink
      ? resolveLink(relative)
      : resolvePath(context.repo, context.scope, relative);
    resolved
      .then((answer) => {
        if (alive) setState(answer.exists && answer.kind === "file" ? "file" : "missing");
      })
      .catch(() => {
        if (alive) setState("missing");
      });
    return () => {
      alive = false;
    };
  }, [relative, context.repo, context.scope, resolveLink]);

  if (target.kind === "anchor")
    return (
      <button
        type="button"
        className={link}
        data-testid="document-anchor"
        onClick={(event) =>
          scrollToDocumentAnchor(
            event.currentTarget.closest("[data-document-root]"),
            target.value,
          )
        }
      >
        {children}
      </button>
    );
  if (target.kind === "external")
    return (
      <a className={link} href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">
        {children}
      </a>
    );
  if (state === "file" && target.kind === "relative")
    return (
      <button
        type="button"
        className={link}
        data-testid="document-file-link"
        title={`open ${target.value}`}
        onClick={() => context.openFile(target.value, anchorOf(href))}
      >
        {children}
      </button>
    );
  return (
    <span className={plain} data-testid="document-link-missing">
      {children} (no such file)
    </span>
  );
}

function DocumentImage({
  src = "",
  srcSet,
  alt = "",
  context,
  ...rest
}: ComponentProps<"img"> & { context: DocumentContext }) {
  const width = rest.width;
  const height = rest.height;
  const resolvedSet = resolveSrcSet(srcSet, context);
  if (isFetchableExternal(src))
    return (
      <img
        className={mediaImage}
        src={src}
        srcSet={resolvedSet}
        alt={alt}
        width={width}
        height={height}
        referrerPolicy="no-referrer"
        loading="lazy"
      />
    );
  const relative = resolveRepositoryPath(context.path, src);
  const media = relative ? mediaForPath(relative) : null;
  if (!relative || !media)
    return (
      <span className={plain} data-testid="document-image-refused">
        {alt || src || "image not shown"}
      </span>
    );
  return (
    <img
      className={mediaImage}
      src={(context.mediaUrl ?? ((path) => fileMediaUrl(context.repo, context.scope, path)))(relative)}
      srcSet={resolvedSet}
      alt={alt}
      width={width}
      height={height}
      referrerPolicy="no-referrer"
      loading="lazy"
      data-media-kind={media.kind}
    />
  );
}

function resolvedMediaSrc(src: string | undefined, context: DocumentContext): string | null {
  if (!src) return null;
  if (isFetchableExternal(src)) return src;
  const relative = resolveRepositoryPath(context.path, src);
  if (!relative || !mediaForPath(relative)) return null;
  return (context.mediaUrl ?? ((path) => fileMediaUrl(context.repo, context.scope, path)))(
    relative,
  );
}

function resolveSrcSet(value: string | undefined, context: DocumentContext): string | undefined {
  if (!value) return undefined;
  const kept = parseSrcSet(value).flatMap(({ url, descriptor }) => {
    const resolved = resolvedMediaSrc(url, context);
    return resolved ? [`${resolved}${descriptor ? ` ${descriptor}` : ""}`] : [];
  });
  return kept.length ? kept.join(", ") : undefined;
}

function DocumentVideo({
  src,
  poster,
  context,
  children,
  node: _node,
  ...rest
}: ComponentProps<"video"> & { context: DocumentContext; node?: unknown }) {
  void _node;
  const media = mediaForPath(resolveRepositoryPath(context.path, src ?? ""));
  const resolved = resolvedMediaSrc(src, context);
  const resolvedPoster = poster
    ? isFetchableExternal(poster)
      ? poster
      : (resolvedMediaSrc(poster, context) ?? undefined)
    : undefined;
  if (src && !resolved)
    return (
      <span className={plain} data-testid="document-video-refused">
        video not shown
      </span>
    );
  return (
    <video className={mediaVideo} controls preload="metadata" {...rest} poster={resolvedPoster}>
      {src ? <source src={resolved ?? undefined} type={media?.type} /> : null}
      {children}
      <track kind="captions" />
    </video>
  );
}

function DocumentSource({
  src,
  srcSet,
  type,
  context,
  node: _node,
  ...rest
}: ComponentProps<"source"> & { context: DocumentContext; node?: unknown }) {
  void _node;
  const relative = resolveRepositoryPath(context.path, src ?? "");
  const media = relative ? mediaForPath(relative) : null;
  const resolved = resolvedMediaSrc(src, context);
  const resolvedSet = resolveSrcSet(srcSet, context);
  if (src && !resolved) return null;
  return (
    <source
      {...rest}
      src={resolved ?? undefined}
      srcSet={resolvedSet}
      type={type ?? media?.type}
    />
  );
}

export function documentComponents(context: DocumentContext) {
  return {
    a: ({ href, children }: ComponentProps<"a"> & { children?: ReactNode }) => (
      <DocumentLink href={href} context={context}>
        {children}
      </DocumentLink>
    ),
    img: (props: ComponentProps<"img">) => <DocumentImage {...props} context={context} />,
    video: (props: ComponentProps<"video">) => <DocumentVideo {...props} context={context} />,
    source: (props: ComponentProps<"source">) => <DocumentSource {...props} context={context} />,
    // ReactMarkdown adds `language-*` to a fenced code block after the hast tree is cleaned;
    // the document mode's "no class reaches the dashboard" rule is enforced again here.
    code: ({ node: _node, className: _className, ...props }: ComponentProps<"code"> & { node?: unknown; className?: string }) => {
      void _node;
      void _className;
      return <code {...props} />;
    },
    pre: ({ node: _node, className: _className, ...props }: ComponentProps<"pre"> & { node?: unknown; className?: string }) => {
      void _node;
      void _className;
      return <pre {...props} />;
    },
  };
}
