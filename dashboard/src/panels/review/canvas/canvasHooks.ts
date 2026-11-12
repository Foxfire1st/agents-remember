/** Bounded message preparation shared by the mounted revision frames. */

/**
 * Forwarding bounds (L104-B08). The server refuses a body past `MAX_REQUEST_BYTES` after it has
 * charged for it; the page's own queue must not be able to make that charge at all. A message past
 * the ceiling, or a burst past the in-flight count or byte budget, is dropped *before* serializing
 * a request, under a named reason, and counted in a fixed-size tally. Dropping never blocks the
 * panel: the frame's own detail stays on screen and the channel stays live.
 */
export const MAX_FORWARDED_MESSAGE_BYTES = 4096;
export const MAX_FORWARD_IN_FLIGHT = 4;
export const MAX_FORWARD_IN_FLIGHT_BYTES = 8 * 1024;

/** Bound traversal and strings before JSON serialization (R4 L104-B08).
 * The registered envelope has one flat payload. Neither a large string, an array, a deep object,
 * nor arbitrarily many properties can make the trusted page walk or serialize unbounded data.
 * Structured-clone work inside the browser precedes delivery and is outside this adapter's bound.
 */
const MAX_FORWARD_ARRAY_ITEMS = 32;
const MAX_FORWARD_FIELDS = 64;

function dataProperty(value: object, key: string): unknown {
  const property = Object.getOwnPropertyDescriptor(value, key);
  if (!property || !('value' in property)) throw new Error('malformed');
  return property.value;
}

function container(value: unknown, depth: number, inArray: boolean): asserts value is object {
  const maxDepth = inArray ? 3 : 1;
  if (typeof value !== 'object' || value === null || Array.isArray(value) || depth > maxDepth) {
    throw new Error('malformed');
  }
}

function isScalar(value: unknown): value is null | number | boolean {
  return value === null || typeof value === 'number' || typeof value === 'boolean';
}

function copyArray(
  value: unknown[],
  depth: number,
  inArray: boolean,
  copyItem: (child: unknown, nextDepth: number, nextInArray: boolean) => unknown,
): unknown[] {
  if (depth < 1 || depth > 2 || inArray) throw new Error('malformed');
  if (value.length > MAX_FORWARD_ARRAY_ITEMS) throw new Error('oversized');
  return value.map((item) => copyItem(item, depth + 1, true));
}

export function forwardedBody(message: unknown): string | null {
  let fields = 0;
  let characters = 0;
  let sawArray = false;
  function copyObject(value: object, depth: number, inArray: boolean): Record<string, unknown> {
    const bounded: Record<string, unknown> = Object.create(null);
    const fieldLimit = sawArray ? MAX_FORWARD_FIELDS : 16;
    for (const key in value) {
      if (++fields > fieldLimit || key.length > 64) throw new Error('oversized');
      const child = dataProperty(value, key);
      if (inArray && typeof child === 'object' && child !== null) throw new Error('malformed');
      bounded[key] = copy(child, depth + 1, inArray);
    }
    return bounded;
  }
  function copy(value: unknown, depth: number, inArray = false): unknown {
    if (typeof value === 'string') {
      characters += value.length;
      if (characters > MAX_FORWARDED_MESSAGE_BYTES) throw new Error('oversized');
      return value;
    }
    if (isScalar(value)) return value;
    if (Array.isArray(value)) {
      sawArray = true;
      return copyArray(value, depth, inArray, copy);
    }
    container(value, depth, inArray);
    return copyObject(value, depth, inArray);
  }
  try {
    const body = JSON.stringify(copy(message, 0));
    return new TextEncoder().encode(body).length <= MAX_FORWARDED_MESSAGE_BYTES ? body : null;
  } catch {
    return null;
  }
}

/**
 * Admit one message for forwarding, or name why it is dropped.
 *
 * `bytes === null` means the message cannot be serialized at all, which is a refusal, not a crash.
 */
export function admitFrameMessage(
  bytes: number | null,
  inFlight: number,
  inFlightBytes: number,
): 'admit' | 'oversized' | 'busy' {
  if (bytes === null || bytes > MAX_FORWARDED_MESSAGE_BYTES) return 'oversized';
  if (inFlight >= MAX_FORWARD_IN_FLIGHT) return 'busy';
  if (inFlightBytes + bytes > MAX_FORWARD_IN_FLIGHT_BYTES) return 'busy';
  return 'admit';
}
