// The explainer's four routes and the answers the column reads (MIK-R106 v2 rules 3, 4 and 7): the
// paths, the shape the server must answer, and the tolerances this client offers the host.
import { describe, expect, it, vi } from 'vitest';

import {
  buildExplainer,
  explainerPath,
  parseAttemptAnswer,
  parseExplainerState,
  parseRetireAnswer,
  readExplainerState,
  retireExplainer,
} from './client';
import { ADDRESS } from './testSupport';

describe('the explainer’s routes (rule 3, 4 and 7)', () => {
  it('names one path per press, with every part escaped', () => {
    expect(explainerPath({ repository: 'repo', master: 'm m', leaf: 'L/181' }, 'build')).toBe(
      '/api/explainer/repo/m%20m/L%2F181/build',
    );
    expect(explainerPath(ADDRESS, 'state')).toBe('/api/explainer/repo/master/L181/state');
    expect(explainerPath(ADDRESS, 'update')).toBe('/api/explainer/repo/master/L181/update');
    expect(explainerPath(ADDRESS, 'retire')).toBe('/api/explainer/repo/master/L181/retire');
  });
});

describe('reading the state answer', () => {
  it('reads the column’s own shape', () => {
    const read = parseExplainerState({
      seat: 'live',
      agentId: 'a-exp',
      workspaceId: 'wks',
      generation: 2,
      binding: { kind: 'recorded', comparison: 8, label: '#8' },
      build: { attempt: 'a1', state: 'done', startedAt: 't', steps: [], failedStep: null, reason: null },
      staleParts: [{ id: 'p3', title: 'Part 3', reason: 'moved' }],
    });
    expect(read.kind).toBe('state');
    if (read.kind !== 'state') return;
    expect(read.value).toMatchObject({ seat: 'live', agentId: 'a-exp', generation: 2 });
    expect(read.value.binding).toEqual({ kind: 'recorded', comparison: 8, label: '#8' });
    expect(read.value.staleParts).toHaveLength(1);
  });

  it('reads the host’s spelling: a seat record, an owner and a number, stale parts under stale', () => {
    const read = parseExplainerState({
      state: 'ok',
      seat: { state: 'closed' },
      binding: { owner: 'planned', number: 0, reference: 'review-comparison:m/L181/planned' },
      stale: { parts: [{ id: 'p3', title: 'Part 3', reason: 'moved' }], total: 1 },
    });
    expect(read.kind).toBe('state');
    if (read.kind !== 'state') return;
    expect(read.value.seat).toBe('closed');
    expect(read.value.binding).toEqual({ kind: 'planned', comparison: 0, label: 'planned · no changes' });
    expect(read.value.staleParts.map((part) => part.id)).toEqual(['p3']);
  });

  it('treats an archived or retired seat as no seat, and an unknown one as unavailable', () => {
    const archived = parseExplainerState({ seat: { state: 'archived' }, retired: true });
    expect(archived.kind).toBe('state');
    if (archived.kind === 'state') expect(archived.value).toMatchObject({ seat: null, retiredAt: '' });
    expect(parseExplainerState({ seat: 'on-fire' })).toMatchObject({ kind: 'unavailable' });
    expect(parseExplainerState(null)).toMatchObject({ kind: 'unavailable' });
    expect(parseExplainerState({ kind: 'unavailable', detail: 'no host' })).toEqual({
      kind: 'unavailable',
      detail: 'no host',
    });
  });

  it('reads a build carried as `building` with its steps', () => {
    const read = parseExplainerState({
      seat: { state: 'closed' },
      building: {
        state: 'building',
        step: 'copy-code',
        steps: [
          { name: 'Copy the code snapshot, read-only', state: 'running', counts: { kind: 'bytes', done: 50, total: 100 } },
        ],
      },
    });
    if (read.kind !== 'state') throw new Error('not a state');
    expect(read.value.build?.state).toBe('running');
    expect(read.value.build?.steps[0]).toMatchObject({ name: 'Copy the code snapshot, read-only', state: 'running' });
  });

  it('reads nested explainer identity and comparison_owner/comparison_number bindings from /state', () => {
    const read = parseExplainerState({
      state: 'ok',
      seat: { state: 'live' },
      binding: {
        comparison_owner: 'agents_remember.application.review_tree_comparison',
        comparison_number: 8,
        comparison_reference: 'review-comparison:master/L181/8',
      },
      explainer: {
        workspace: '/coord/explainer/repo/master/L181',
        present: true,
        generation: 3,
        agentId: 'a-exp-nested',
        seat: 'live',
        retired: null,
      },
    });
    expect(read.kind).toBe('state');
    if (read.kind !== 'state') return;
    expect(read.value).toMatchObject({
      seat: 'live',
      agentId: 'a-exp-nested',
      workspaceId: '/coord/explainer/repo/master/L181',
      generation: 3,
      binding: { kind: 'recorded', comparison: 8, label: '#8' },
    });

    const planned = parseExplainerState({
      state: 'ok',
      binding: {
        comparison_owner: 'planned',
        comparison_number: 0,
        comparison_reference: 'review-comparison:master/L181/planned',
      },
      explainer: {
        workspace: '/coord/explainer/repo/master/L181',
        present: true,
        generation: 1,
        agentId: 'a-exp-planned',
        seat: 'closed',
        building: {
          state: 'running',
          steps: [{ name: 'Prepare packet', state: 'running', counts: { kind: 'files', done: 2, total: 4 } }],
        },
      },
    });
    expect(planned.kind).toBe('state');
    if (planned.kind !== 'state') return;
    expect(planned.value).toMatchObject({
      seat: 'closed',
      agentId: 'a-exp-planned',
      workspaceId: '/coord/explainer/repo/master/L181',
      generation: 1,
      binding: { kind: 'planned', comparison: 0, label: 'planned · no changes' },
    });
    expect(planned.value.build?.state).toBe('running');
  });
});

describe('the presses', () => {
  it('posts the first request once and reads its answer', async () => {
    const fetcher = vi.fn(async () =>
      new Response(
        JSON.stringify({ attempt: 'a1', state: { seat: 'closed', agentId: 'a-exp', workspaceId: 'wks' } }),
        { status: 202, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const answer = await buildExplainer(
      ADDRESS,
      { target: 'new-explainer', message: 'explain it', picks: [], planned: false, stalePartIds: [] },
      fetcher as unknown as typeof fetch,
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(String((fetcher.mock.calls[0] as unknown[])[0])).toBe('/api/explainer/repo/master/L181/build');
    expect(answer.kind).toBe('accepted');
    if (answer.kind === 'accepted') expect(answer.answer.state.seat).toBe('closed');
  });

  it('posts bodies the real route accepts: a bounded request id, a bounded one-segment receipt id', async () => {
    // The shapes serving/explainer.py validates: `_send_facts` takes a non-empty requestId of at
    // most MAX_REQUEST_ID (128) characters and a non-empty composed message of at most
    // MAX_MESSAGE (32768); `_receipt_id` takes a receipt id of at most 64 characters that
    // `receipt_id_segment` admits as one file name (no separator, no traversal, no absolute path).
    const bodies: Array<Record<string, unknown>> = [];
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      bodies.push({ url, ...(typeof init?.body === 'string' ? JSON.parse(init.body) : {}) });
      return new Response(
        JSON.stringify({ attempt: 'a1', state: { seat: 'closed', agentId: 'a-exp', workspaceId: 'wks' } }),
        { headers: { 'Content-Type': 'application/json' } },
      );
    }) as unknown as typeof fetch;
    const answer = await buildExplainer(
      ADDRESS,
      {
        target: 'new-explainer',
        // A composed reference-only first turn: the picks name the reference, the text box stayed empty.
        message: '',
        picks: [
          {
            id: 'p1',
            kind: 'reference',
            title: 'INV-1ZKP4R · the seat’s return',
            note: '',
            reference: { kind: 'invariant', repository: 'repo', id: 'INV-1ZKP4R', memoryTree: 'm'.repeat(40) },
          },
        ],
        planned: false,
        stalePartIds: [],
      },
      fetcher,
    );
    expect(answer.kind).toBe('accepted');
    const buildBody = bodies[0];
    expect(buildBody.url).toBe('/api/explainer/repo/master/L181/build');
    const requestId = buildBody.requestId;
    expect(typeof requestId).toBe('string');
    expect((requestId as string).length).toBeGreaterThan(0);
    expect((requestId as string).length).toBeLessThanOrEqual(128);
    expect((requestId as string)).not.toMatch(/[\\/]/);
    // The route composes the picks into the first turn, so an empty message with a pick passes.
    expect(buildBody.picks).toHaveLength(1);

    const retireMock = vi.fn(async () =>
      new Response(
        JSON.stringify({
          receipt: {
            receiptId: 'r1',
            agentId: 'a-exp',
            generation: 1,
            state: 'finished',
            step: 'finished',
            endedByMarker: 3,
            endedByTree: 3,
            remaining: [],
            detail: '',
          },
          state: { seat: null, retiredAt: '2026-10-19T15:12:00Z' },
        }),
        { headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const retireFetcher = retireMock as unknown as typeof fetch;
    const retired = await retireExplainer(ADDRESS, retireFetcher);
    expect(retired.kind).toBe('retired');
    const retireInit = ((retireMock.mock.calls[0] as unknown[])[1] as RequestInit);
    const retireBody = JSON.parse(String(retireInit.body)) as { receiptId?: string };
    const receiptId = retireBody.receiptId;
    expect(typeof receiptId).toBe('string');
    expect((receiptId as string).length).toBeGreaterThan(0);
    expect((receiptId as string).length).toBeLessThanOrEqual(64);
    expect(receiptId).not.toMatch(/[\\/]/);
    expect(receiptId).not.toBe('..');
    expect(receiptId?.startsWith('/')).toBe(false);
    expect(receiptId?.startsWith('.')).toBe(false);
  });

  it('names a refusal instead of throwing, and keeps the server’s reason', async () => {
    const fetcher = vi.fn(async () =>
      new Response(JSON.stringify({ reason: 'task-archived', detail: 'the master is retired' }), {
        status: 409,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const answer = await buildExplainer(
      ADDRESS,
      { target: 'new-explainer', message: 'explain it', picks: [], planned: false, stalePartIds: [] },
      fetcher as unknown as typeof fetch,
    );
    expect(answer).toEqual({ kind: 'refused', reason: 'task-archived', detail: 'the master is retired' });
  });

  it('reads Retire’s receipt, with its two counts and what remains', async () => {
    const fetcher = vi.fn(async () =>
      new Response(
        JSON.stringify({
          receipt: {
            receiptId: 'r1',
            agentId: 'a-exp',
            generation: 1,
            state: 'finished',
            step: 'finished',
            endedByMarker: 3,
            endedByTree: 3,
            remaining: [4321],
            detail: '',
          },
          state: { seat: null, retiredAt: '2026-10-19T15:12:00Z' },
        }),
        { headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const answer = await retireExplainer(ADDRESS, fetcher as unknown as typeof fetch);
    expect(answer.kind).toBe('retired');
    if (answer.kind === 'retired') expect(answer.answer.receipt).toMatchObject({ endedByTree: 3, endedByMarker: 3, remaining: [4321] });
    const retireBody = JSON.parse(String(((fetcher.mock.calls[0] as unknown[])[1] as RequestInit).body)) as { receiptId?: string };
    expect(typeof retireBody.receiptId).toBe('string');
    expect(retireBody.receiptId?.length).toBeGreaterThan(0);
  });

  it('reads native snake_case retire and held-delivery build envelopes', () => {
    const retired = parseRetireAnswer({
      state: 'ok',
      retire: {
        receipt: {
          receipt_id: 'rec-1',
          agent_id: 'a-exp',
          generation: 2,
          state: 'finished',
          step: 'finished',
          ended_by_marker: 2,
          ended_by_tree: 2,
          remaining: [],
          detail: 'done',
          finished_at: '2026-10-19T15:12:00Z',
        },
        workspaceRemoved: true,
      },
    });
    expect(retired).not.toBeNull();
    expect(retired?.receipt).toMatchObject({
      receiptId: 'rec-1',
      agentId: 'a-exp',
      generation: 2,
      state: 'finished',
      endedByMarker: 2,
      endedByTree: 2,
      remaining: [],
    });
    expect(retired?.state.retiredAt).toBe('2026-10-19T15:12:00Z');

    const built = parseAttemptAnswer({
      state: 'ok',
      seat: {
        agentId: 'a-exp',
        workspace: '/wks',
        generation: 1,
        seat: 'live',
        delivery: { state: 'held', reason: 'provider-rate-limit', detail: 'queued until window resets' },
      },
    });
    expect(built).not.toBeNull();
    expect(built?.state).toMatchObject({ seat: 'live', agentId: 'a-exp', workspaceId: '/wks', generation: 1 });
    expect(built?.held).toEqual({ reason: 'provider-rate-limit', detail: 'queued until window resets' });
  });

  it('says the host could not be reached rather than failing the page', async () => {
    const fetcher = vi.fn(async () => {
      throw new Error('connection refused');
    });
    expect(await readExplainerState(ADDRESS, fetcher as unknown as typeof fetch)).toEqual({
      kind: 'unavailable',
      detail: 'connection refused',
    });
    expect(await retireExplainer(ADDRESS, fetcher as unknown as typeof fetch)).toMatchObject({ kind: 'refused', reason: 'unreachable' });
  });

  it('reads no attempt answer from something that is not one', () => {
    expect(parseAttemptAnswer({ nope: true })).not.toBeNull();
    expect(parseAttemptAnswer(null)).toBeNull();
    expect(parseRetireAnswer({ state: {} })).toBeNull();
  });
});
