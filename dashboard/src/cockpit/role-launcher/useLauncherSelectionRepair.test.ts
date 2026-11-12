import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { TaskDocumentRef } from '../../types/projection';
import { chatLeaf, chatMaster, chatSprint } from '../../test/fixtures/documentChat';
import type { RoleLaunchSelection } from '../roleLaunchModel';
import {
  repairedSeededSelection,
  usePruneSeededSelection,
} from './useLauncherSelectionRepair';

const DOCS = [chatSprint, chatMaster, chatLeaf];

function refOf(entry: { reference?: TaskDocumentRef }): TaskDocumentRef {
  if (!entry.reference) throw new Error('Fixture has no document reference');
  return entry.reference;
}

const SPRINT_REF = refOf(chatSprint);
const MASTER_REF = refOf(chatMaster);
const LEAF_REF = refOf(chatLeaf);

function managerSelection(over: Partial<RoleLaunchSelection> = {}): RoleLaunchSelection {
  return {
    role: 'manager',
    sprintDocumentRef: { ...SPRINT_REF },
    masterDocumentRef: { ...MASTER_REF },
    ...over,
  };
}

describe('repairedSeededSelection payloads and order', () => {
  it('keeps a selection naming live documents (quiescence)', () => {
    expect(
      repairedSeededSelection(DOCS, { ...managerSelection(), taskDocumentRef: { ...LEAF_REF } }),
    ).toBeUndefined();
    expect(repairedSeededSelection(DOCS, managerSelection())).toBeUndefined();
  });

  it('prunes a vanished sprint back to the role alone', () => {
    expect(
      repairedSeededSelection(
        DOCS,
        managerSelection({ sprintDocumentRef: { repository: 'repo', path: 'gone/task.json' } }),
      ),
    ).toEqual({ role: 'manager' });
  });

  it('prunes a vanished master back to its sprint', () => {
    expect(
      repairedSeededSelection(
        DOCS,
        managerSelection({ masterDocumentRef: { repository: 'repo', path: 'gone/task.json' } }),
      ),
    ).toEqual({ role: 'manager', sprintDocumentRef: SPRINT_REF });
  });

  it('prunes a vanished task back to its master', () => {
    expect(
      repairedSeededSelection(DOCS, {
        ...managerSelection(),
        taskDocumentRef: { repository: 'repo', path: 'gone/leaf.json' },
      }),
    ).toEqual({ role: 'manager', sprintDocumentRef: SPRINT_REF, masterDocumentRef: MASTER_REF });
  });

  it('repairs the first invalid tier when several levels are stale', () => {
    expect(
      repairedSeededSelection(
        DOCS,
        managerSelection({
          sprintDocumentRef: { repository: 'repo', path: 'gone/task.json' },
          masterDocumentRef: { repository: 'repo', path: 'gone/master.json' },
        }),
      ),
    ).toEqual({ role: 'manager' });
  });
});

describe('seeded document repair scheduling', () => {
  it('repairs a stale seeded selection as soon as the documents arrive', () => {
    const onSelectionChange = vi.fn();
    renderHook(() =>
      usePruneSeededSelection({
        tasks: DOCS,
        role: 'manager',
        sprintDocumentRef: { repository: 'repo', path: 'gone/task.json' },
        masterDocumentRef: undefined,
        taskDocumentRef: undefined,
        onSelectionChange,
      }),
    );
    expect(onSelectionChange).toHaveBeenCalledTimes(1);
    expect(onSelectionChange).toHaveBeenCalledWith({ role: 'manager' });
  });

  it('leaves a live seeded selection alone', () => {
    const onSelectionChange = vi.fn();
    renderHook(() =>
      usePruneSeededSelection({
        tasks: DOCS,
        role: 'manager',
        sprintDocumentRef: { ...SPRINT_REF },
        masterDocumentRef: { ...MASTER_REF },
        taskDocumentRef: undefined,
        onSelectionChange,
      }),
    );
    expect(onSelectionChange).not.toHaveBeenCalled();
  });
});
