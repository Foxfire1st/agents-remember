// Scroll-memory test fixtures: the jsdom scroll shim (scrollTo/scrollHeight/clientHeight) and the
// feed/geometry helpers shared by the two scroll-memory split files. installScrollMemoryGeometry
// registers the describe-scoped beforeEach/afterEach (call it inside the describe body).
import { afterEach, beforeEach } from "vitest";

import type { ConversationItem } from "../../../../data/conversation/types";
import { msg } from "./test-utils";

export const alignedTops: number[] = [];

export function installScrollMemoryGeometry(): void {
  beforeEach(() => {
    alignedTops.length = 0;
    Object.defineProperty(HTMLElement.prototype, "scrollTo", {
      configurable: true,
      value: function (this: HTMLElement, options?: { top?: number }) {
        const top = options?.top ?? 0;
        alignedTops.push(top);
        this.scrollTop = top;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", { configurable: true, value: 6000 });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", { configurable: true, value: 600 });
  });
  afterEach(() => {
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
    delete proto.scrollTo;
    delete proto.scrollHeight;
    delete proto.clientHeight;
  });
}

export function feedOf(count: number): ConversationItem[] {
  return Array.from({ length: count }, (_, index) =>
    msg({ itemId: `m-${index + 1}`, globalOrdinal: index + 1 }),
  );
}

export function pinGeometry(viewport: HTMLElement, scrollHeight: number, clientHeight: number): void {
  Object.defineProperty(viewport, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(viewport, "clientHeight", { configurable: true, value: clientHeight });
}
