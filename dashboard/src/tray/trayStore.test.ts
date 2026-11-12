// The tray's store (MIK-R188 rules 1, 6, 7 and 11; Failure and Recovery: a full browser storage).
import { describe, expect, it } from 'vitest';

import { createTrayStore, type TrayDeps } from './trayStore';
import type { TrayImageStore } from './trayImages';
import { DEFAULT_BOUNDS, type TrayBounds } from './trayPreferences';
import { TRAY_KEY } from './trayPersistence';
import { previewLine } from './trayModel';
import type { ComparisonRef, TrayPickInput } from './types';

class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  refuse: Error | null = null;
  get length() {
    return this.values.size;
  }
  clear() {
    this.values.clear();
  }
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  setItem(key: string, value: string) {
    if (this.refuse) throw this.refuse;
    this.values.set(key, value);
  }
}

function memoryImages(refuse: Error | null = null): TrayImageStore & { kept: Map<string, Blob> } {
  const kept = new Map<string, Blob>();
  return {
    kept,
    put: async (sha, blob) => {
      if (refuse) throw refuse;
      kept.set(sha, blob);
    },
    get: async (sha) => kept.get(sha) ?? null,
    remove: async (sha) => {
      kept.delete(sha);
    },
  };
}

// An image store whose writes wait for `release()`; `writing` settles when the first write starts.
function gatedImages(): TrayImageStore & { kept: Map<string, Blob>; writing: Promise<void>; release: () => void } {
  const kept = new Map<string, Blob>();
  let started = () => {};
  let release = () => {};
  const writing = new Promise<void>((resolve) => {
    started = resolve;
  });
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  return {
    kept,
    writing,
    release: () => release(),
    put: async (sha, blob) => {
      started();
      await released;
      kept.set(sha, blob);
    },
    get: async (sha) => kept.get(sha) ?? null,
    remove: async (sha) => {
      kept.delete(sha);
    },
  };
}

function deps(over: Partial<TrayDeps> = {}, bounds: TrayBounds = DEFAULT_BOUNDS): TrayDeps {
  return {
    storage: new MemoryStorage(),
    images: memoryImages(),
    bounds: () => bounds,
    shownChat: () => null,
    now: () => new Date('2026-10-19T14:05:00Z'),
    ...over,
  };
}

const invariant: TrayPickInput = {
  kind: 'reference',
  title: 'INV-1ZKP4R · A silent role-agent turn end',
  reference: { kind: 'invariant', repository: 'agents-remember', id: 'INV-1ZKP4R', revisionId: 'rev-3', memoryTree: 'a'.repeat(40) },
};
const task: TrayPickInput = {
  kind: 'reference',
  title: '260928-MIK-L89 · The review page and the tray',
  reference: { kind: 'task', reference: { repository: 'agents-remember', path: 'm/89.json' }, id: '260928-MIK-L89' },
};
const chatPassage: TrayPickInput = {
  kind: 'text',
  title: 'No, not forever.',
  text: 'No, not forever. The count is per seat.',
  provenance: { source: 'chat', agentId: 'agent-manager', messageId: 'm-1', messageTime: '2026-10-19T12:05:00Z' },
};

describe('the tray store', () => {
  it('adds one pick per object: a second ⊕ on the same object adds nothing', () => {
    const store = createTrayStore(deps());
    expect(store.getState().add(invariant)).toBe(true);
    expect(store.getState().add({ ...invariant, title: 'another title, same object' })).toBe(false);
    expect(store.getState().picks).toHaveLength(1);
    expect(store.getState().add(task)).toBe(true);
    expect(store.getState().picks.map((pick) => pick.input.title)).toEqual([invariant.title, task.title]);
  });

  it('keeps whole files of distinct comparisons apart across a reload; the same whole file adds nothing', () => {
    const comparison: ComparisonRef = {
      repository: 'agents-remember',
      task: '260928-MIK',
      leaf: 'L89',
      number: 1,
      pinRef: 'refs/ar/review/260928-MIK/L89/1',
      trees: { codeBase: 'a'.repeat(40), codeCandidate: 'b'.repeat(40), memoryBase: 'c'.repeat(40), memoryCandidate: 'd'.repeat(40) },
    };
    const wholeFile = (over: Partial<ComparisonRef> = {}): TrayPickInput => ({
      kind: 'reference',
      title: 'x.ts · code without invariant',
      reference: { kind: 'code-without-invariant', comparison: { ...comparison, ...over }, hunk: { kind: 'file', path: 'dashboard/src/x.ts' } },
    });
    const otherLeaf = wholeFile({ leaf: 'L90', pinRef: 'refs/ar/review/260928-MIK/L90/1' });
    const otherTrees = wholeFile({ trees: { ...comparison.trees, codeCandidate: 'e'.repeat(40) } });
    const storage = new MemoryStorage();
    const store = createTrayStore(deps({ storage }));
    expect([wholeFile(), otherLeaf, otherTrees].map((input) => store.getState().add(input))).toEqual([true, true, true]);
    expect(store.getState().add(wholeFile())).toBe(false);
    const named = (state: ReturnType<typeof store.getState>) =>
      state.picks.map((pick) =>
        pick.input.kind === 'reference' && pick.input.reference.kind === 'code-without-invariant'
          ? `${pick.input.reference.comparison.leaf} ${pick.input.reference.comparison.trees.codeCandidate[0]}`
          : null,
      );
    expect(named(store.getState())).toEqual(['L89 b', 'L90 b', 'L89 e']);
    const reloaded = createTrayStore(deps({ storage }));
    expect(named(reloaded.getState())).toEqual(['L89 b', 'L90 b', 'L89 e']);
    expect(reloaded.getState().add(otherLeaf)).toBe(false);
  });

  it('keeps no bytes of an image refused by the count or the message bound, and keeps a live pick’s bytes', async () => {
    const png = (...bytes: number[]) => new File([new Uint8Array([137, 80, 78, 71, ...bytes])], 'shot.png', { type: 'image/png' });
    // The count bound: a full tray refuses two distinct images, and the stored bytes stay its pick's.
    const counted = memoryImages();
    const full = createTrayStore(deps({ images: counted }, { maxPicks: 1, maxImageBytes: 1024, maxMessageBytes: 1 << 20 }));
    expect(await full.getState().addImage(png(1))).toBe(true);
    const owned = [...counted.kept.keys()];
    expect([await full.getState().addImage(png(2)), await full.getState().addImage(png(3))]).toEqual([false, false]);
    expect(full.getState().refusal).toContain('picks per message');
    expect([...counted.kept.keys()]).toEqual(owned);
    // The message bound: one image fits, a second distinct one would take the message over it.
    const sized = memoryImages();
    const near = createTrayStore(deps({ images: sized }, { maxPicks: 20, maxImageBytes: 1024, maxMessageBytes: 80 }));
    expect(await near.getState().addImage(png(...new Array(26).fill(1)))).toBe(true);
    const kept = [...sized.kept.entries()];
    expect([await near.getState().addImage(png(...new Array(26).fill(2))), await near.getState().addImage(png(...new Array(26).fill(3)))]).toEqual([false, false]);
    expect(near.getState().refusal).toContain('per message');
    expect([...sized.kept.entries()]).toEqual(kept);
    // The tray fills while an image's bytes are written: those bytes go. Two pastes of one image
    // while it is written: one pick, and its bytes stay.
    const gated = gatedImages();
    const racing = createTrayStore(deps({ images: gated }, { maxPicks: 1, maxImageBytes: 1024, maxMessageBytes: 1 << 20 }));
    const late = racing.getState().addImage(png(4));
    await gated.writing;
    racing.getState().add(invariant);
    gated.release();
    expect(await late).toBe(false);
    expect(gated.kept.size).toBe(0);
    const twice = gatedImages();
    const room = createTrayStore(deps({ images: twice }));
    const both = Promise.all([room.getState().addImage(png(5)), room.getState().addImage(png(5))]);
    await twice.writing;
    twice.release();
    expect(await both).toEqual([true, false]);
    const image = room.getState().picks[0].input;
    expect([...twice.kept.keys()]).toEqual([image.kind === 'image' ? image.sha256 : null]);
  });

  it('keeps a note that is added, edited and removed, sets it by a quick intent, and removes a pick', () => {
    const store = createTrayStore(deps());
    store.getState().add(invariant);
    const id = store.getState().picks[0].id;
    store.getState().setNote(id, '  Is this cap the same as the others?  ');
    expect(store.getState().picks[0].note).toBe('Is this cap the same as the others?');
    store.getState().setNote(id, 'challenge this');
    expect(store.getState().picks[0].note).toBe('challenge this');
    expect(previewLine(store.getState().picks, '')).toBe("One message: 1 reference the agent opens with AR's tools, 1 note.");
    store.getState().setNote(id, '');
    expect(store.getState().picks[0].note).toBe('');
    store.getState().remove(id);
    expect(store.getState().picks).toEqual([]);
  });

  it('refuses a pick over a bound by name, before it enters', () => {
    const store = createTrayStore(deps({}, { maxPicks: 1, maxImageBytes: 1024, maxMessageBytes: 4096 }));
    store.getState().add(invariant);
    expect(store.getState().add(task)).toBe(false);
    expect(store.getState().refusal).toBe(
      "“260928-MIK-L89 · The review page and the tray” is not added: the tray holds at most 1 picks per message (set under Bounds in the tray's foot). Remove one first.",
    );
    store.getState().remove(store.getState().picks[0].id);
    const big: TrayPickInput = { kind: 'image', title: 'Screenshot · pasted 14:05', mimeType: 'image/png', sha256: 'f'.repeat(64), bytes: 2048 };
    expect(store.getState().add(big)).toBe(false);
    expect(store.getState().refusal).toContain('the image is 2 KiB, over the bound of 1 KiB per image');
    const long: TrayPickInput = { ...chatPassage, text: 'x'.repeat(5000) };
    expect(store.getState().add(long)).toBe(false);
    expect(store.getState().refusal).toContain('over the bound of 4 KiB per message');
    expect(store.getState().picks).toEqual([]);
  });

  it('sets the default target once, by the first pick (rule 7)', () => {
    // A chat passage makes its chat the default.
    const fromChat = createTrayStore(deps({ shownChat: () => 'agent-shown' }));
    fromChat.getState().add(chatPassage);
    expect(fromChat.getState().targets).toEqual(['agent-manager']);
    // An object pick makes the chat shown in the column the default; a later pick changes nothing.
    let shown: string | null = 'agent-shown';
    const fromObject = createTrayStore(deps({ shownChat: () => shown }));
    fromObject.getState().add(invariant);
    expect(fromObject.getState().targets).toEqual(['agent-shown']);
    shown = 'agent-other';
    fromObject.getState().add(chatPassage);
    expect(fromObject.getState().targets).toEqual(['agent-shown']);
    // No chat shown: no default.
    const none = createTrayStore(deps());
    none.getState().add(invariant);
    expect(none.getState().targets).toEqual([]);
    expect(none.getState().defaultDecided).toBe(true);
    // His own choice before the first pick stays.
    const chosen = createTrayStore(deps({ shownChat: () => 'agent-shown' }));
    chosen.getState().setTargets(['agent-architect']);
    chosen.getState().add(invariant);
    expect(chosen.getState().targets).toEqual(['agent-architect']);
  });

  it('decides the default again when the last pick is removed, and keeps a target he chose', () => {
    // The first pick set the shown chat as the default; that chat is no longer shown when the tray
    // empties, so the next first pick, with no chat shown, has no default and Send waits for his choice.
    let shown: string | null = 'agent-b';
    const store = createTrayStore(deps({ shownChat: () => shown }));
    store.getState().add(invariant);
    expect(store.getState().targets).toEqual(['agent-b']);
    shown = null;
    store.getState().remove(store.getState().picks[0].id);
    expect([store.getState().targets, store.getState().defaultDecided, store.getState().pendingDefault]).toEqual([[], false, false]);
    store.getState().add(task);
    expect([store.getState().targets, store.getState().defaultDecided]).toEqual([[], true]);
    // Removing one of two picks changes nothing about the target.
    const two = createTrayStore(deps({ shownChat: () => 'agent-b' }));
    two.getState().add(invariant);
    two.getState().add(task);
    two.getState().remove(two.getState().picks[0].id);
    expect([two.getState().targets, two.getState().defaultDecided]).toEqual([['agent-b'], true]);
    // A waiting default on the review page ends with the last pick too.
    const waiting = createTrayStore(deps());
    waiting.getState().setDeferDefault(true);
    waiting.getState().add(invariant);
    expect(waiting.getState().pendingDefault).toBe(true);
    waiting.getState().remove(waiting.getState().picks[0].id);
    expect([waiting.getState().pendingDefault, waiting.getState().defaultDecided]).toEqual([false, false]);
    // A target he chose stays through emptying.
    const chosen = createTrayStore(deps({ shownChat: () => 'agent-b' }));
    chosen.getState().setTargets(['agent-architect']);
    chosen.getState().add(invariant);
    chosen.getState().remove(chosen.getState().picks[0].id);
    expect([chosen.getState().targets, chosen.getState().targetsChosen]).toEqual([['agent-architect'], true]);
  });

  it('lets a first pick on the review page with its column closed wait for the chat the column shows', () => {
    const listeners: ((agentId: string) => void)[] = [];
    const show = (agentId: string) => listeners.forEach((listener) => listener(agentId));
    const store = createTrayStore(deps({ watchShownChat: (listener) => (listeners.push(listener), () => undefined) }));
    store.getState().setDeferDefault(true);
    store.getState().add(invariant);
    expect([store.getState().targets, store.getState().pendingDefault]).toEqual([[], true]);
    show('agent-task-chat');
    expect([store.getState().targets, store.getState().pendingDefault]).toEqual([['agent-task-chat'], false]);
    // Only the first pick waits, and only while it is still waiting.
    show('agent-other');
    expect(store.getState().targets).toEqual(['agent-task-chat']);

    // His own choice ends the wait; so does leaving the review page.
    const chosen = createTrayStore(deps({ watchShownChat: (listener) => (listeners.push(listener), () => undefined) }));
    chosen.getState().setDeferDefault(true);
    chosen.getState().add(invariant);
    chosen.getState().toggleTarget('agent-architect');
    show('agent-task-chat');
    expect(chosen.getState().targets).toEqual(['agent-architect']);
    const left = createTrayStore(deps({ watchShownChat: (listener) => (listeners.push(listener), () => undefined) }));
    left.getState().setDeferDefault(true);
    left.getState().add(invariant);
    left.getState().clearPendingDefault();
    show('agent-task-chat');
    expect(left.getState().targets).toEqual([]);
  });

  it('survives a reload: a new store reads the same tray from this origin’s storage', async () => {
    const storage = new MemoryStorage();
    const images = memoryImages();
    const first = createTrayStore(deps({ storage, images, shownChat: () => 'agent-shown' }));
    first.getState().add(invariant);
    first.getState().add(chatPassage);
    first.getState().setNote(first.getState().picks[0].id, 'explain');
    first.getState().setText('Two follow-ups on the risks you listed.');
    await first.getState().addImage(new File([new Uint8Array([137, 80, 78, 71])], 'shot.png', { type: 'image/png' }));
    expect(first.getState().persistence).toEqual({ state: 'kept' });

    const second = createTrayStore(deps({ storage, images }));
    const state = second.getState();
    expect(state.picks.map((pick) => [pick.input.kind, pick.note])).toEqual([
      ['reference', 'explain'],
      ['text', ''],
      ['image', ''],
    ]);
    expect(state.text).toBe('Two follow-ups on the risks you listed.');
    expect(state.targets).toEqual(['agent-shown']);
    const image = state.picks[2].input;
    expect(image.kind === 'image' && images.kept.has(image.sha256)).toBe(true);
    expect(JSON.parse(storage.getItem(TRAY_KEY) ?? '{}').version).toBe(1);
  });

  it('drops on reading a stored chat passage that names no message time, and keeps the rest', () => {
    const storage = new MemoryStorage();
    // A stored row as an earlier dashboard wrote it: its input is the stored JSON, not a pick of today's shape.
    const stored = (input: unknown, id: string) => ({ id, addedAt: '2026-10-19T14:05:00Z', input, note: '' });
    const untimed = { ...chatPassage, title: 'Before the time was named.', provenance: { ...chatPassage.provenance, messageTime: null } };
    const panelPassage: TrayPickInput = { kind: 'text', title: 'A line', text: 'A line', provenance: { source: 'panel', panel: 'Operations' } };
    storage.setItem(
      TRAY_KEY,
      JSON.stringify({
        version: 1,
        picks: [stored(invariant, 'p-1'), stored(untimed, 'p-2'), stored(chatPassage, 'p-3'), stored(panelPassage, 'p-4')],
        text: '',
        targets: [],
        targetsChosen: false,
        defaultDecided: true,
      }),
    );
    const store = createTrayStore(deps({ storage }));
    expect(store.getState().picks.map((pick) => pick.id)).toEqual(['p-1', 'p-3', 'p-4']);
  });

  it('keeps the tray in memory and says so when the browser storage refuses', async () => {
    const storage = new MemoryStorage();
    storage.refuse = Object.assign(new Error('The quota has been exceeded.'), { name: 'QuotaExceededError' });
    const store = createTrayStore(deps({ storage }));
    store.getState().add(invariant);
    expect(store.getState().picks).toHaveLength(1);
    expect(store.getState().persistence).toEqual({
      state: 'memory-only',
      detail:
        "The browser's storage refused the tray (QuotaExceededError: The quota has been exceeded.). The tray is kept in memory and will not survive a reload.",
    });

    const imageStore = createTrayStore(deps({ images: memoryImages(new Error('IndexedDB is full')) }));
    await imageStore.getState().addImage(new File([new Uint8Array([1, 2, 3])], 'shot.png', { type: 'image/png' }));
    expect(imageStore.getState().picks).toHaveLength(1);
    expect(imageStore.getState().persistence).toMatchObject({ state: 'memory-only' });
    expect(imageStore.getState().persistence.state === 'memory-only' && imageStore.getState().persistence).toMatchObject({
      detail: expect.stringContaining('refused an image (IndexedDB is full)'),
    });
    imageStore.getState().remove(imageStore.getState().picks[0].id);
    expect(imageStore.getState().persistence).toEqual({ state: 'kept' });
  });
});
