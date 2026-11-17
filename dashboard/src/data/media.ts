// The dashboard's one picture/video table, matching mcp/.../serving/media.py. A path's suffix
// decides whether a viewer draws it or a text route describes it as binary. The bytes routes
// serve only these types; the URL builders here are the only client-side address construction.
import { qs } from "./files";

export type MediaKind = "image" | "video";

export interface MediaType {
  kind: MediaKind;
  type: string;
  label: string;
}

const BY_SUFFIX: Record<string, MediaType> = {
  ".png": { kind: "image", type: "image/png", label: "PNG" },
  ".jpg": { kind: "image", type: "image/jpeg", label: "JPEG" },
  ".jpeg": { kind: "image", type: "image/jpeg", label: "JPEG" },
  ".gif": { kind: "image", type: "image/gif", label: "GIF" },
  ".webp": { kind: "image", type: "image/webp", label: "WebP" },
  ".svg": { kind: "image", type: "image/svg+xml", label: "SVG" },
  ".mp4": { kind: "video", type: "video/mp4", label: "MP4" },
  ".webm": { kind: "video", type: "video/webm", label: "WebM" },
};

export function mediaForPath(path: string | null | undefined): MediaType | null {
  if (!path) return null;
  const dot = path.lastIndexOf(".");
  if (dot < 0) return null;
  return BY_SUFFIX[path.slice(dot).toLowerCase()] ?? null;
}

export function mediaForType(type: string | null | undefined): MediaType | null {
  if (!type) return null;
  return Object.values(BY_SUFFIX).find((media) => media.type === type) ?? null;
}

export function formatBytes(size: number): string {
  return `${size.toLocaleString()} bytes`;
}

// The type line under a picture or player: "PNG · 12,345 bytes".
export function mediaCaption(media: MediaType | null, size: number | null | undefined): string {
  const label = media?.label ?? "media";
  return size == null ? label : `${label} · ${formatBytes(size)}`;
}

export const fileMediaUrl = (repo: string, scope: string, path: string, base = ""): string =>
  `${base}/api/files/bytes?${qs({ repo, scope, path })}`;

export const noteMediaUrl = (repo: string, master: string, path: string, base = ""): string =>
  `${base}/api/notes/bytes?${qs({ repo, master, path })}`;

export const knowledgeMediaUrl = (
  repo: string,
  commit: string,
  path: string,
  base = "",
): string => `${base}/api/knowledge/bytes?${qs({ repo, commit, path })}`;

export interface ChangeSetMediaAddress {
  repo: string;
  scope?: string;
  master?: string;
  leaf?: string;
  mode?: string;
  comparison?: number;
  kind: "code" | "memory";
  path: string;
  side: "before" | "after";
  generation?: {
    codeBase?: string;
    codeTip?: string;
    memoryBase?: string;
    memoryTip?: string;
  };
}

// The bytes of one change-set side. The address carries the same selectors as the diff read;
// the server resolves the revision itself, so no commit or revision is named here.
export function changeSetMediaUrl(target: ChangeSetMediaAddress, base = ""): string {
  const params: Record<string, string> = {
    repo: target.repo,
    kind: target.kind,
    path: target.path,
    side: target.side,
    ...selectorParams(target),
  };
  return `${base}/api/changeset/file-bytes?${qs(params)}`;
}

function selectorParams(target: ChangeSetMediaAddress): Record<string, string> {
  if (target.comparison !== undefined) return comparisonParams(target);
  if (target.leaf) return leafParams(target);
  if (target.master) return masterParams(target.master, target.generation);
  return { scope: target.scope ?? "" };
}

function comparisonParams(target: ChangeSetMediaAddress): Record<string, string> {
  return {
    master: target.master ?? "",
    leaf: target.leaf ?? "",
    comparison: String(target.comparison),
  };
}

function leafParams(target: ChangeSetMediaAddress): Record<string, string> {
  return { master: target.master ?? "", leaf: target.leaf ?? "", mode: target.mode ?? "committed" };
}

function masterParams(
  master: string,
  generation: ChangeSetMediaAddress["generation"],
): Record<string, string> {
  const params: Record<string, string> = { master };
  for (const [key, value] of Object.entries(generation ?? {})) {
    if (value) params[key] = value;
  }
  return params;
}
