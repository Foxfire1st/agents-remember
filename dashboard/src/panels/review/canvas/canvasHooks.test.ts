import { describe, expect, it, vi } from 'vitest';

import {
  admitFrameMessage,
  forwardedBody,
  MAX_FORWARDED_MESSAGE_BYTES,
  MAX_FORWARD_IN_FLIGHT,
  MAX_FORWARD_IN_FLIGHT_BYTES,
} from './canvasHooks';

describe('current-frame message bounds', () => {
  it('preserves an admitted message for the current-frame protocol', () => {
    const message = { type: 'open-evidence', reference: 'INV-1' };
    const body = forwardedBody(message);
    expect(body).toBe(JSON.stringify(message));
    expect(admitFrameMessage(new TextEncoder().encode(body as string).length, 0, 0)).toBe('admit');
  });

  it.each([64, 128])(
    'rejects a burst of %i oversized or unbounded bodies before serialization',
    (burst) => {
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
        for (let index = 0; index < burst; index += 1) {
          expect(forwardedBody(bodies[index % bodies.length])).toBeNull();
        }
        expect(stringify).not.toHaveBeenCalled();
      } finally {
        stringify.mockRestore();
      }
    },
  );

  it('rejects malformed or oversized messages and bounds both in-flight count and bytes', () => {
    expect(admitFrameMessage(null, 0, 0)).toBe('oversized');
    expect(admitFrameMessage(MAX_FORWARDED_MESSAGE_BYTES + 1, 0, 0)).toBe('oversized');
    expect(admitFrameMessage(1, MAX_FORWARD_IN_FLIGHT, 0)).toBe('busy');
    expect(admitFrameMessage(1, 0, MAX_FORWARD_IN_FLIGHT_BYTES)).toBe('busy');
    expect(admitFrameMessage(MAX_FORWARDED_MESSAGE_BYTES, 0, 0)).toBe('admit');
  });
});
