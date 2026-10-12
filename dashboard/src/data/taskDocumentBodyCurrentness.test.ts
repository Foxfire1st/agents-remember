// L127-C-F01 open-reader update regressions: an already-fetched body never
// overrides a newer summary's head/step/review facts. Each case mirrors the
// sealed counterexample shape (same revision, fetched old body, newer summary)
// and asserts the merged document carries the newer fact.
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { taskDoc } from '../panels/detail-panel/test-utils';
import type { TaskDocNode } from '../types/projection';
import { useTaskDocumentBody } from './useTaskDocumentBody';

const leafPath = '/tasks/repo-a/m/01_leaf.json';

type LeafOver = Partial<TaskDocNode>;

function leaf(over: LeafOver = {}) {
  return taskDoc({
    id: 'LEAF',
    title: 'Leaf',
    bodyRevision: 'same-revision',
    statusNote: 'OLD-STATUS',
    headerNotes: [{ label: 'Gate', value: 'OLD-VALUE' }],
    type: 'OLD-TYPE',
    steps: [
      {
        id: 'S1',
        title: 'Step',
        status: 'pending',
        note: 'OLD-NOTE',
        substeps: [],
        noteTruncated: false,
        outcomeTruncated: false,
      },
    ],
    reviewState: {
      round: 1,
      pending: false,
      additionalRounds: 0,
      totalFindings: 0,
      openFindings: 0,
      findings: [],
    },
    ...over,
    kind: 'subTask',
    docPath: leafPath,
  });
}

async function mergedWith(oldDoc: ReturnType<typeof taskDoc>, newDoc: ReturnType<typeof taskDoc>) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, status: 200, json: async () => oldDoc })),
  );
  try {
    const { result, rerender } = renderHook(({ doc }) => useTaskDocumentBody(doc), {
      initialProps: { doc: oldDoc },
    });
    await waitFor(() => expect(result.current.state).toBe('available'));
    rerender({ doc: newDoc });
    return result.current.documentFor(newDoc);
  } finally {
    vi.unstubAllGlobals();
  }
}

describe('open-reader summary currentness (L127-C-F01)', () => {
  it('carries the newer status note', async () => {
    const oldDoc = leaf();
    const merged = await mergedWith(oldDoc, { ...oldDoc, statusNote: 'NEW-STATUS' });
    expect(merged.statusNote).toBe('NEW-STATUS');
  });

  it('carries the newer header notes', async () => {
    const oldDoc = leaf();
    const merged = await mergedWith(oldDoc, {
      ...oldDoc,
      headerNotes: [{ label: 'Gate', value: 'NEW-VALUE' }],
    });
    expect(merged.headerNotes).toEqual([{ label: 'Gate', value: 'NEW-VALUE' }]);
  });

  it('carries the newer type', async () => {
    const oldDoc = leaf();
    const merged = await mergedWith(oldDoc, { ...oldDoc, type: 'NEW-TYPE' });
    expect(merged.type).toBe('NEW-TYPE');
  });

  it('carries the newer step notes', async () => {
    const oldDoc = leaf();
    const merged = await mergedWith(oldDoc, {
      ...oldDoc,
      steps: [
        {
          id: 'S1',
          title: 'Step',
          status: 'pending',
          note: 'NEW-NOTE',
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ],
    });
    expect(merged.steps[0].note).toBe('NEW-NOTE');
  });

  it('carries the newer review round', async () => {
    const oldDoc = leaf();
    const merged = await mergedWith(oldDoc, {
      ...oldDoc,
      reviewState: {
        round: 2,
        pending: false,
        additionalRounds: 0,
        totalFindings: 0,
        openFindings: 0,
        findings: [],
      },
    });
    expect(merged.reviewState?.round).toBe(2);
  });

  it('still prefers the fetched body for long-form content', async () => {
    const oldDoc = leaf({ objective: '', requirements: [] });
    const fetched = { ...oldDoc, objective: 'fetched objective', requirements: ['r1'] };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => fetched })),
    );
    try {
      const { result } = renderHook((doc: ReturnType<typeof taskDoc>) => useTaskDocumentBody(doc), {
        initialProps: oldDoc,
      });
      await waitFor(() => expect(result.current.state).toBe('available'));
      const merged = result.current.documentFor(oldDoc);
      expect(merged.objective).toBe('fetched objective');
      expect(merged.requirements).toEqual(['r1']);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('current-body full restoration (L127-C-F06)', () => {
  const longStatus = `OLD-STATUS ${'x'.repeat(10000)}`;
  const longNote = `OLD-NOTE ${'y'.repeat(10000)}`;
  const longFinding = `OLD-FINDING ${'z'.repeat(7990)}`;

  function previewed() {
    return leaf({
      statusNote: longStatus.slice(0, 400),
      statusNoteTruncated: true,
      headerNotes: Array.from({ length: 6 }, (_, i) => ({ label: `L${i}`, value: `V${i}` })),
      headerNotesTotal: 10,
      steps: [
        {
          id: 'S1',
          title: 'Step',
          status: 'pending',
          note: longNote.slice(0, 400),
          substeps: [],
          noteTruncated: true,
          outcomeTruncated: false,
        },
      ],
      reviewState: {
        round: 1,
        pending: false,
        additionalRounds: 0,
        totalFindings: 10,
        openFindings: 10,
        findings: Array.from({ length: 10 }, (_, i) => ({
          findingId: `F${i + 1}`,
          description: longFinding.slice(0, 400),
          open: true,
          descriptionTruncated: true,
        })),
      },
    });
  }

  function whole() {
    const doc = previewed();
    return {
      ...doc,
      statusNote: longStatus,
      statusNoteTruncated: false,
      headerNotes: Array.from({ length: 10 }, (_, i) => ({ label: `L${i}`, value: `V${i}` })),
      steps: [
        {
          id: 'S1',
          title: 'Step',
          status: 'pending' as const,
          note: longNote,
          substeps: [],
          noteTruncated: false,
          outcomeTruncated: false,
        },
      ],
      reviewState: {
        round: 1,
        pending: false,
        additionalRounds: 0,
        totalFindings: 10,
        openFindings: 10,
        findings: Array.from({ length: 10 }, (_, i) => ({
          findingId: `F${i + 1}`,
          description: longFinding,
          open: true,
          descriptionTruncated: false,
        })),
      },
    };
  }

  it('restores preview-truncated fields from the current body', async () => {
    const summary = previewed();
    const body = whole();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => body })),
    );
    try {
      const { result } = renderHook((doc: ReturnType<typeof taskDoc>) => useTaskDocumentBody(doc), {
        initialProps: summary,
      });
      await waitFor(() => expect(result.current.state).toBe('available'));
      const merged = result.current.documentFor(summary);
      expect(merged.statusNote).toBe(longStatus);
      expect(merged.statusNoteTruncated).toBe(false);
      expect(merged.headerNotes).toHaveLength(10);
      expect(merged.steps[0].note).toBe(longNote);
      expect(merged.steps[0].noteTruncated).toBe(false);
      expect(merged.reviewState?.findings).toHaveLength(10);
      expect(merged.reviewState?.findings[0].description).toBe(longFinding);
      expect(merged.reviewState?.round).toBe(1);
      expect(merged.type).toBe('OLD-TYPE');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('keeps the newer summary when the cached body is stale', async () => {
    const oldDoc = leaf();
    const fetched = { ...oldDoc };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: true, status: 200, json: async () => fetched })),
    );
    try {
      const { result, rerender } = renderHook(({ doc }) => useTaskDocumentBody(doc), {
        initialProps: { doc: oldDoc },
      });
      await waitFor(() => expect(result.current.state).toBe('available'));
      // A newer revision arrives while the old body is cached: the summary
      // owns every fact until the refetch lands.
      const newer = { ...oldDoc, bodyRevision: 'new-revision', statusNote: 'NEW-STATUS' };
      rerender({ doc: newer });
      const merged = result.current.documentFor(newer);
      expect(merged.statusNote).toBe('NEW-STATUS');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
