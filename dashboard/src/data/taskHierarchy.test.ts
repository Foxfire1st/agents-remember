import { describe, expect, it } from "vitest";

import { findParentTaskMatch, taskDocParentKey } from "./taskHierarchy";
import { seriesSelectionKey, taskDocSelectionKey } from "./taskIdentity";
import type { SeriesNode, SeriesSubTaskNode } from "../types/projection";

// These feed `series.subTasks`, so they are SERIES rows: they carry `createdAt` and never a
// cross-series `linkedLifecycleId`.
function ref(overrides: Partial<SeriesSubTaskNode> & Pick<SeriesSubTaskNode, "number" | "file">): SeriesSubTaskNode {
  return { name: overrides.number, status: "open", scope: "", ...overrides };
}

function series(
  overrides: Partial<SeriesNode> & Pick<SeriesNode, "seriesId" | "docPath" | "subTasks">,
): SeriesNode {
  return {
    repository: "repo",
    title: overrides.seriesId,
    status: "open",
    objective: "",
    doneCount: 0,
    totalCount: overrides.subTasks.length,
    seriesTokenTotal: 0,
    sections: [],
    decisions: [],
    ...overrides,
  };
}

describe("findParentTaskMatch", () => {
  it("resolves a sub-task doc through ./-relative ref files", () => {
    const alpha = series({
      seriesId: "alpha",
      docPath: "tasks/alpha/master.md",
      subTasks: [ref({ number: "1", file: "./01-first.md" })],
    });
    const match = findParentTaskMatch({ kind: "subTask", docPath: "tasks/alpha/01-first.md" }, [alpha]);
    expect(match?.series.seriesId).toBe("alpha");
    expect(match?.number).toBe("1");
  });

  it("normalizes ..-relative ref files across folders", () => {
    const alpha = series({
      seriesId: "alpha",
      docPath: "tasks/alpha/master.md",
      subTasks: [ref({ number: "2", file: "../beta/02-x.md" })],
    });
    expect(
      findParentTaskMatch({ kind: "subTask", docPath: "tasks/beta/02-x.md" }, [alpha])?.number,
    ).toBe("2");
  });

  it("prefers the first series in list order when two series name the same doc", () => {
    const target = ref({ number: "1", file: "./01-shared.md" });
    const first = series({ seriesId: "first", docPath: "tasks/x/master.md", subTasks: [target] });
    const second = series({ seriesId: "second", docPath: "tasks/x/master.md", subTasks: [target] });
    const match = findParentTaskMatch({ kind: "subTask", docPath: "tasks/x/01-shared.md" }, [first, second]);
    expect(match?.series.seriesId).toBe("first");
  });

  it("prefers the earliest ref by creation order within a series", () => {
    const alpha = series({
      seriesId: "alpha",
      docPath: "tasks/alpha/master.md",
      subTasks: [
        ref({ number: "late", file: "./01-first.md", createdAt: "2026-07-02" }),
        ref({ number: "early", file: "./01-first.md", createdAt: "2026-07-01" }),
      ],
    });
    expect(
      findParentTaskMatch({ kind: "subTask", docPath: "tasks/alpha/01-first.md" }, [alpha])?.number,
    ).toBe("early");
  });

  it("lets a non-empty doc id override the ref number", () => {
    const alpha = series({
      seriesId: "alpha",
      docPath: "tasks/alpha/master.md",
      subTasks: [ref({ number: "1", file: "./01-first.md" })],
    });
    expect(
      findParentTaskMatch({ kind: "subTask", docPath: "tasks/alpha/01-first.md", id: "1b" }, [alpha])?.number,
    ).toBe("1b");
  });

  it("never matches master docs and returns undefined for unknown docs", () => {
    const alpha = series({
      seriesId: "alpha",
      docPath: "tasks/alpha/master.md",
      subTasks: [ref({ number: "1", file: "./master.md" })],
    });
    expect(findParentTaskMatch({ kind: "master", docPath: "tasks/alpha/master.md" }, [alpha])).toBeUndefined();
    expect(findParentTaskMatch({ kind: "subTask", docPath: "tasks/other/09-x.md" }, [alpha])).toBeUndefined();
  });

  it("caches per seriesList identity: a fresh array observes new refs, the same array does not", () => {
    const alpha = series({ seriesId: "alpha", docPath: "tasks/alpha/master.md", subTasks: [] });
    const doc = { kind: "subTask", docPath: "tasks/alpha/01-first.md" };
    const list = [alpha];
    expect(findParentTaskMatch(doc, list)).toBeUndefined();
    alpha.subTasks.push(ref({ number: "1", file: "./01-first.md" }));
    expect(findParentTaskMatch(doc, list)).toBeUndefined();
    expect(findParentTaskMatch(doc, [alpha])?.number).toBe("1");
  });
});

describe("taskDocParentKey", () => {
  it("keys onto the series doc when it is a master, else onto the series id", () => {
    const alpha = series({
      seriesId: "alpha",
      docPath: "tasks/alpha/master.md",
      subTasks: [ref({ number: "1", file: "./01-first.md" })],
    });
    const doc = { kind: "subTask", docPath: "tasks/alpha/01-first.md" };
    expect(taskDocParentKey(doc, [alpha], new Set(["tasks/alpha/master.md"]))).toBe(
      taskDocSelectionKey("tasks/alpha/master.md"),
    );
    expect(taskDocParentKey(doc, [alpha], new Set())).toBe(seriesSelectionKey("alpha"));
  });
});
