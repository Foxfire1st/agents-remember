// The tray's pure rules: bounds (MIK-R188 rule 11), the default target (rule 7), the preview line
// (rule 8) and the words a chip shows (rules 2 and 3). No storage, no network, no DOM.
import type { TrayBounds } from './trayPreferences';
import type { TextProvenance, TrayPick, TrayPickInput } from './types';

// ---- sizes and bounds ----

const encoder = new TextEncoder();
const utf8 = (text: string): number => encoder.encode(text).length;

// The bytes a pick adds to the composed message: its title, note and payload. An image travels as
// base64, four bytes for every three.
export function pickBytes(input: TrayPickInput, note = ''): number {
  const words = utf8(input.title) + utf8(note);
  if (input.kind === 'reference') return words + utf8(JSON.stringify(input.reference));
  if (input.kind === 'text') return words + utf8(input.text) + utf8(JSON.stringify(input.provenance));
  return words + 4 * Math.ceil(input.bytes / 3);
}

export function messageBytes(picks: readonly TrayPick[], text: string): number {
  return picks.reduce((sum, pick) => sum + pickBytes(pick.input, pick.note), utf8(text));
}

export function formatBytes(bytes: number): string {
  const mib = bytes / (1024 * 1024);
  if (mib >= 1) return `${Number.isInteger(mib) ? mib : mib.toFixed(1)} MiB`;
  const kib = bytes / 1024;
  if (kib >= 1) return `${Math.round(kib)} KiB`;
  return `${bytes} bytes`;
}

// Where each bound is set (rule 11, e6ac7a3e): a refusal names its bound and this place.
const BOUND_SOURCE = "set under Bounds in the tray's foot";

// An image over the bound per image, by its title and size; the store asks before it reads the bytes.
export function imageSizeRefusal(title: string, bytes: number, bounds: TrayBounds): string | null {
  if (bytes <= bounds.maxImageBytes) return null;
  return `“${title}” is refused: the image is ${formatBytes(bytes)}, over the bound of ${formatBytes(bounds.maxImageBytes)} per image (${BOUND_SOURCE}).`;
}

function imageOverBound(input: TrayPickInput, bounds: TrayBounds): string | null {
  return input.kind === 'image' ? imageSizeRefusal(input.title, input.bytes, bounds) : null;
}

// The image types the dashboard server takes; a pasted image of another type is refused on entry.
export const SENDABLE_IMAGE_TYPES: readonly string[] = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

export function imageTypeRefusal(title: string, type: string): string | null {
  if (SENDABLE_IMAGE_TYPES.includes(type)) return null;
  return `“${title}” is refused: its type ${type || '(none)'} is not one the tray sends (PNG, JPEG, GIF or WebP).`;
}

// Whether a pick may enter the tray. A refusal names the pick and the bound it is over.
export function refuseOnEntry(
  input: TrayPickInput,
  picks: readonly TrayPick[],
  text: string,
  bounds: TrayBounds,
): string | null {
  if (picks.length >= bounds.maxPicks)
    return `“${input.title}” is not added: the tray holds at most ${bounds.maxPicks} picks per message (${BOUND_SOURCE}). Remove one first.`;
  const image = imageOverBound(input, bounds);
  if (image) return image;
  const total = messageBytes(picks, text) + pickBytes(input);
  if (total > bounds.maxMessageBytes)
    return `“${input.title}” is refused: the message would be ${formatBytes(total)}, over the bound of ${formatBytes(bounds.maxMessageBytes)} per message (${BOUND_SOURCE}).`;
  return null;
}

// Whether the tray may be sent as it stands. The bounds may have changed since a pick entered.
export function refuseOnSend(picks: readonly TrayPick[], text: string, bounds: TrayBounds): string | null {
  if (picks.length > bounds.maxPicks)
    return `Send is refused: the tray holds ${picks.length} picks, over the bound of ${bounds.maxPicks} picks per message (${BOUND_SOURCE}).`;
  for (const pick of picks) {
    const image = imageOverBound(pick.input, bounds);
    if (image) return image;
  }
  const total = messageBytes(picks, text);
  if (total > bounds.maxMessageBytes)
    return `Send is refused: the message is ${formatBytes(total)}, over the bound of ${formatBytes(bounds.maxMessageBytes)} per message (${BOUND_SOURCE}).`;
  return null;
}

// ---- the default target (rule 7) ----

// The first pick sets the tray's default target once. A text pick from a chat makes that chat the
// default. Any other pick (an object, a panel highlight, a pasted image) makes the chat shown in the
// column the default; with no chat shown there is none, and Send stays disabled.
export function defaultTargetFor(input: TrayPickInput, shownChat: string | null): string | null {
  if (input.kind === 'text' && input.provenance.source === 'chat') return input.provenance.agentId;
  return shownChat;
}

// ---- the preview line (rule 8) ----

const counted = (n: number, one: string, many: string): string => `${n} ${n === 1 ? one : many}`;

// "One message: 2 references the agent opens with AR's tools, 1 note, your text."
export function previewLine(picks: readonly TrayPick[], text: string): string {
  const references = picks.filter((pick) => pick.input.kind === 'reference').length;
  const passages = picks.filter((pick) => pick.input.kind === 'text').length;
  const images = picks.filter((pick) => pick.input.kind === 'image').length;
  const notes = picks.filter((pick) => pick.note.trim() !== '').length;
  const parts = [
    references ? `${counted(references, 'reference', 'references')} the agent opens with AR's tools` : '',
    passages ? counted(passages, 'highlighted passage', 'highlighted passages') : '',
    images ? counted(images, 'image', 'images') : '',
    notes ? counted(notes, 'note', 'notes') : '',
    text.trim() ? 'your text' : '',
  ].filter(Boolean);
  return parts.length ? `One message: ${parts.join(', ')}.` : 'Nothing to send yet: add a pick or write your text.';
}

// ---- a chip's words ----

// "HH:MM" in the developer's local time, or null when the time cannot be read.
export function clockTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const time = new Date(iso);
  if (Number.isNaN(time.getTime())) return null;
  return `${String(time.getHours()).padStart(2, '0')}:${String(time.getMinutes()).padStart(2, '0')}`;
}

export const shortAgent = (agentId: string): string => `agent ${agentId.slice(0, 8)}`;

// Where a highlighted passage came from: "from Manager · 14:05", or "from Operations" for a
// dashboard panel. A chat passage always names its message's time (rule 3, ebdae1e5).
export function provenanceLine(provenance: TextProvenance, roleOf: (agentId: string) => string | null): string {
  if (provenance.source === 'panel') return `from ${provenance.panel}`;
  const who = roleOf(provenance.agentId) ?? shortAgent(provenance.agentId);
  return `from ${who} · ${clockTime(provenance.messageTime) ?? provenance.messageTime}`;
}

// A passage's chip title: its first line, at most 60 characters.
export function passageTitle(text: string): string {
  const first = text.trim().split('\n', 1)[0] ?? '';
  return first.length > 60 ? `${first.slice(0, 59)}…` : first;
}

const REFERENCE_KIND_WORDS: Record<string, string> = {
  invariant: 'invariant',
  family: 'family',
  'review-block': 'review block',
  'code-without-invariant': 'code without invariant',
  task: 'task document',
  'file-line': 'file lines',
  'change-set': 'change set',
};

// The chip's second line before the note: what kind of thing the pick is.
export function pickKindWords(input: TrayPickInput): string {
  if (input.kind === 'image') return `image · ${formatBytes(input.bytes)}`;
  if (input.kind === 'text') return 'highlighted passage';
  const reference = input.reference;
  const words = REFERENCE_KIND_WORDS[reference.kind] ?? reference.kind;
  if ('comparison' in reference && reference.comparison) return `${words} · #${reference.comparison.number}`;
  return words;
}
