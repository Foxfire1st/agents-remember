import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { RevisionFrame } from './RevisionFrame';
import { channel, json, props } from './ExplanationView.test-utils';

afterEach(() => {
  cleanup();
  document.documentElement.removeAttribute('data-effects');
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function serveFrame(pending?: Promise<void>) {
  const messages: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/messages')) {
        messages.push(String(init?.body));
        const reply = () =>
          json({ state: 'opened', operation: 'open-evidence', reference: 'INV-1' });
        return pending ? pending.then(reply) : Promise.resolve(reply());
      }
      if (url.endsWith('/revoke')) return Promise.resolve(json({ state: 'ok', revoked: true }));
      if (url === '/api/canvas/sessions') return Promise.resolve(json(channel('bounded-frame', 8)));
      return Promise.resolve(json({ state: 'refused', reason: 'unmocked', detail: url }, 404));
    }),
  );
  return messages;
}

function dispatch(frame: HTMLElement, data: unknown) {
  window.dispatchEvent(
    new MessageEvent('message', { source: (frame as HTMLIFrameElement).contentWindow, data }),
  );
}

describe('the mounted revision frame preserves forwarding bounds', () => {
  it.each([64, 128])(
    'rejects %i unbounded bodies before serialization and keeps its channel usable',
    async (burst) => {
      const messages = serveFrame();
      const onEvidence = vi.fn();
      render(
        <RevisionFrame
          target={props.target}
          revision={8}
          caption="revision 8"
          onEvidence={onEvidence}
        />,
      );
      const frame = await screen.findByTestId('explanation-frame');
      const bodies = [
        { type: 'status', payload: { detail: 'x'.repeat(1024 * 1024) } },
        { type: 'status', payload: { nested: { another: { level: 'unbounded' } } } },
        {
          type: 'status',
          payload: Object.fromEntries(Array.from({ length: 10000 }, (_, i) => [`field${i}`, i])),
        },
      ];
      const stringify = vi.spyOn(JSON, 'stringify');
      try {
        act(() => {
          for (let index = 0; index < burst; index += 1)
            dispatch(frame, bodies[index % bodies.length]);
        });
        expect(stringify).not.toHaveBeenCalled();
        expect(messages).toHaveLength(0);
      } finally {
        stringify.mockRestore();
      }
      act(() => dispatch(frame, { type: 'open-evidence', reference: 'INV-1' }));
      await waitFor(() => expect(onEvidence).toHaveBeenCalledTimes(1));
      expect(messages).toHaveLength(1);
      expect(screen.getByTestId('explanation-frame')).toBe(frame);
    },
  );

  it('does not inspect or serialize further bodies while four requests are in flight', async () => {
    let finish!: () => void;
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const messages = serveFrame(pending);
    const mounted = render(
      <RevisionFrame target={props.target} revision={8} caption="revision 8" />,
    );
    const frame = await screen.findByTestId('explanation-frame');
    act(() => {
      for (let index = 0; index < 4; index += 1) {
        dispatch(frame, { type: 'status', requestId: `request-${index}` });
      }
    });
    expect(messages).toHaveLength(4);
    const stringify = vi.spyOn(JSON, 'stringify');
    const body = { type: 'status' };
    const payload = vi.fn(() => {
      throw new Error('a saturated channel inspected the body');
    });
    Object.defineProperty(body, 'payload', { enumerable: true, get: payload });
    try {
      act(() => {
        for (let index = 0; index < 128; index += 1) dispatch(frame, body);
      });
      expect(payload).not.toHaveBeenCalled();
      expect(stringify).not.toHaveBeenCalled();
      expect(messages).toHaveLength(4);
    } finally {
      stringify.mockRestore();
    }
    mounted.unmount();
    await act(async () => {
      finish();
      await pending;
    });
  });
});

describe('the current frame receives the dashboard effects policy', () => {
  it.each([true, false])(
    'starts with effects=%s and follows both toggle directions without changing its binding',
    async (initial) => {
      if (initial) delete document.documentElement.dataset.effects;
      else document.documentElement.dataset.effects = 'off';
      serveFrame();
      const mounted = render(
        <RevisionFrame target={props.target} revision={8} caption="revision 8" />,
      );
      const frame = (await screen.findByTestId('explanation-frame')) as HTMLIFrameElement;
      const sent = vi.spyOn(frame.contentWindow!, 'postMessage');
      // The document's existing liveness port answers the host's load challenge.
      const port = {
        onmessage: null as ((event: MessageEvent) => void) | null,
        postMessage: (challenge: unknown) => {
          port.onmessage?.({ data: challenge } as MessageEvent);
        },
        close: vi.fn(),
      };
      act(() => {
        window.dispatchEvent(
          new MessageEvent('message', {
            source: frame.contentWindow,
            data: { schema: 'ar-canvas-document/v1', session: 'bounded-frame', phase: 'entered' },
            ports: [port as unknown as MessagePort],
          }),
        );
        fireEvent.load(frame);
      });
      const bindings = () =>
        sent.mock.calls
          .map(([message]) => message)
          .filter((message) => message.schema === 'ar-canvas-binding/v1');
      const expected = {
        schema: 'ar-canvas-binding/v1',
        protocolVersion: 1,
        session: 'bounded-frame',
        revision: 8,
        comparison: 'review-comparison:task/leaf/9',
      };
      await waitFor(() => expect(bindings()).toEqual([{ ...expected, effects: initial }]));
      act(() => {
        if (initial) document.documentElement.dataset.effects = 'off';
        else delete document.documentElement.dataset.effects;
      });
      await waitFor(() =>
        expect(bindings()).toEqual([
          { ...expected, effects: initial },
          { ...expected, effects: !initial },
        ]),
      );
      act(() => {
        if (initial) delete document.documentElement.dataset.effects;
        else document.documentElement.dataset.effects = 'off';
      });
      await waitFor(() =>
        expect(bindings()).toEqual([
          { ...expected, effects: initial },
          { ...expected, effects: !initial },
          { ...expected, effects: initial },
        ]),
      );
      expect(screen.getByTestId('explanation-frame')).toBe(frame);
      expect(fetch).toHaveBeenCalledTimes(1);
      mounted.unmount();
      expect(port.close).toHaveBeenCalledOnce();
    },
  );
});
