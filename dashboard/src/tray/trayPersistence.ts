// The tray survives a reload (MIK-R188 rule 6): the pick list, the developer's text and the target
// choice are kept in this dashboard origin's localStorage under `ar.tray.v1`. Image bytes are not
// here; they live in IndexedDB by SHA-256 (trayImages.ts). A stored value that does not read as a
// tray gives an empty tray, and a pick that does not read as a pick is dropped: so is a chat passage
// kept before every chat pick named its message's time (rule 3, ebdae1e5), which Send could not carry.
import { pickKey } from './pickKey';
import type { TrayPick, TrayPickInput } from './types';

export const TRAY_KEY = 'ar.tray.v1';

export interface KeptTray {
  picks: TrayPick[];
  text: string;
  targets: string[];
  targetsChosen: boolean;
  defaultDecided: boolean;
}

export const EMPTY_TRAY: KeptTray = { picks: [], text: '', targets: [], targetsChosen: false, defaultDecided: false };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isText = (value: unknown): value is string => typeof value === 'string';

// A passage's provenance: a panel, or a chat with its agent and its message's time.
function readsAsProvenance(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (value.source === 'panel') return isText(value.panel);
  return value.source === 'chat' && isText(value.agentId) && isText(value.messageTime) && Number.isFinite(Date.parse(value.messageTime));
}

function readsAsInput(value: unknown): value is TrayPickInput {
  if (!isRecord(value) || !isText(value.title)) return false;
  if (value.kind === 'reference') return isRecord(value.reference) && isText(value.reference.kind);
  if (value.kind === 'text') return isText(value.text) && readsAsProvenance(value.provenance);
  if (value.kind === 'image')
    return isText(value.sha256) && isText(value.mimeType) && typeof value.bytes === 'number';
  return false;
}

function readPick(value: unknown): TrayPick | null {
  if (!isRecord(value) || !isText(value.id) || !readsAsInput(value.input)) return null;
  return {
    id: value.id,
    key: pickKey(value.input),
    addedAt: isText(value.addedAt) ? value.addedAt : '',
    input: value.input,
    note: isText(value.note) ? value.note : '',
  };
}

function readStored(storage: Pick<Storage, 'getItem'> | undefined): unknown {
  try {
    const raw = storage?.getItem(TRAY_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function readPicks(values: unknown[]): TrayPick[] {
  const picks: TrayPick[] = [];
  for (const value of values) {
    const pick = readPick(value);
    if (pick && !picks.some((kept) => kept.key === pick.key)) picks.push(pick);
  }
  return picks;
}

export function readKeptTray(storage: Pick<Storage, 'getItem'> | undefined): KeptTray {
  const stored = readStored(storage);
  if (!isRecord(stored) || stored.version !== 1 || !Array.isArray(stored.picks)) return EMPTY_TRAY;
  const picks = readPicks(stored.picks);
  const targets = Array.isArray(stored.targets) ? stored.targets.filter(isText) : [];
  return {
    picks,
    text: isText(stored.text) ? stored.text : '',
    targets: [...new Set(targets)],
    targetsChosen: stored.targetsChosen === true,
    defaultDecided: stored.defaultDecided === true,
  };
}

// Writes the tray; answers the storage's refusal in words, or null when it was kept.
export function writeKeptTray(storage: Pick<Storage, 'setItem'> | undefined, tray: KeptTray): string | null {
  if (!storage) return 'this browser offers no local storage';
  try {
    storage.setItem(TRAY_KEY, JSON.stringify({ version: 1, ...tray }));
    return null;
  } catch (error) {
    return error instanceof Error && error.name ? `${error.name}: ${error.message}` : 'the browser refused the write';
  }
}
