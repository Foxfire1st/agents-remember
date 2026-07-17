import { cleanup, render, waitFor } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  INSPECTOR_VIRTUALIZE_THRESHOLD,
  VirtualizedInspectorList,
} from "./VirtualizedInspectorList";

const original: Record<string, PropertyDescriptor | undefined> = {};
beforeAll(() => {
  for (const [property, value] of [
    ["offsetHeight", 240],
    ["offsetWidth", 320],
  ] as const) {
    original[property] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, property);
    Object.defineProperty(HTMLElement.prototype, property, {
      configurable: true,
      get: () => value,
    });
  }
});
afterAll(() => {
  for (const property of ["offsetHeight", "offsetWidth"]) {
    const descriptor = original[property];
    if (descriptor) Object.defineProperty(HTMLElement.prototype, property, descriptor);
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[property];
  }
});
afterEach(cleanup);

function list(rows: string[]) {
  return (
    <VirtualizedInspectorList
      rows={rows}
      rowKey={(row) => row}
      renderRow={(row) => row}
      label="test ledger"
      testId="test-ledger"
    />
  );
}

describe("VirtualizedInspectorList", () => {
  it("keeps 100 rows as ordinary DOM list items", () => {
    const rows = Array.from({ length: INSPECTOR_VIRTUALIZE_THRESHOLD }, (_, index) => `row-${index}`);
    const view = render(list(rows));
    expect(view.getByTestId("test-ledger").getAttribute("data-virtualized")).toBe("false");
    expect(view.getAllByTestId("test-ledger-item")).toHaveLength(100);
  });

  it("virtualizes past 100 without slicing the accessible total", async () => {
    const rows = Array.from(
      { length: INSPECTOR_VIRTUALIZE_THRESHOLD + 1 },
      (_, index) => `row-${index}`,
    );
    const view = render(list(rows));
    const ledger = view.getByTestId("test-ledger");
    expect(ledger.getAttribute("data-virtualized")).toBe("true");
    await waitFor(() => expect(view.getAllByTestId("test-ledger-item").length).toBeGreaterThan(0));
    expect(view.getAllByTestId("test-ledger-item").length).toBeLessThan(rows.length);
    expect(view.getAllByTestId("test-ledger-item")[0].getAttribute("aria-setsize")).toBe("101");
  });
});
