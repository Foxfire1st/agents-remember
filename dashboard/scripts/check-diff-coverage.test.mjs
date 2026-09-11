import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, test } from "vitest";

import {
  coveredStatementLines,
  executableStatementLines,
  measureDiffCoverage,
} from "./check-diff-coverage.mjs";

const statement = (start, end) => ({ start: { line: start }, end: { line: end } });
const entry = (statementMap, counts) => ({ statementMap, s: counts });
const cliPath = resolve(process.cwd(), "scripts/check-diff-coverage.mjs");

describe("check-diff-coverage executable-statement semantics", () => {
  test("the direct changed-lines CLI refuses before reading Git or coverage", () => {
    const env = { ...process.env };
    delete env.AR_DAGGER_TEST_ATTESTATION;
    const result = spawnSync(process.execPath, [cliPath], {
      encoding: "utf8",
      env,
    });

    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("changed-lines coverage is Dagger-only");
    expect(result.stdout).not.toContain("[diff-coverage] base=");
  });

  test("executableStatementLines spans every statement range", () => {
    const lines = executableStatementLines(
      entry(
        {
          a: statement(1, 1),
          b: statement(3, 5),
        },
        { a: 1, b: 0 },
      ),
    );
    expect([...lines].sort((l, r) => l - r)).toEqual([1, 3, 4, 5]);
  });

  test("coveredStatementLines spans only executed statement ranges", () => {
    const lines = coveredStatementLines(
      entry(
        {
          a: statement(1, 1),
          b: statement(3, 5),
        },
        { a: 1, b: 0 },
      ),
    );
    expect([...lines]).toEqual([1]);
  });

  test("denominator counts only changed lines v8 records as executable statements", () => {
    const changed = new Map([["src/panels/A.ts", new Set([1, 2, 3, 4, 5])]]);
    const coverage = new Map([
      [
        "src/panels/A.ts",
        entry(
          {
            a: statement(1, 1),
            b: statement(2, 3),
            c: statement(5, 5),
          },
          { a: 1, b: 0, c: 3 },
        ),
      ],
    ]);
    const result = measureDiffCoverage(changed, coverage);
    // Line 4 carries no statement, so it contributes nothing to the denominator; lines 1, 2, 3
    // and 5 are executable; only 1 and 5 were executed.
    expect(result).toEqual({
      covered: 2,
      total: 4,
      missing: ["src/panels/A.ts:2", "src/panels/A.ts:3"],
    });
  });

  test("files without a v8 entry record no executable lines", () => {
    const changed = new Map([["src/panels/Unmeasured.ts", new Set([1, 2])]]);
    expect(measureDiffCoverage(changed, new Map())).toEqual({
      covered: 0,
      total: 0,
      missing: [],
    });
  });

  test("test/dev/types files are excluded and dashboard/ keys are normalized", () => {
    const changed = new Map([
      ["src/panels/A.ts", new Set([1])],
      ["src/panels/A.test.ts", new Set([1])],
      ["src/test/fixtures/x.ts", new Set([1])],
      ["src/dev/scenario.ts", new Set([1])],
      ["src/types/t.ts", new Set([1])],
      ["dashboard/src/panels/B.ts", new Set([2])],
      ["tools/helper.ts", new Set([1])],
    ]);
    const coverage = new Map([
      ["src/panels/A.ts", entry({ a: statement(1, 1) }, { a: 4 })],
      ["src/panels/A.test.ts", entry({ a: statement(1, 1) }, { a: 0 })],
      ["src/panels/B.ts", entry({ a: statement(2, 2) }, { a: 0 })],
    ]);
    const result = measureDiffCoverage(changed, coverage);
    expect(result).toEqual({
      covered: 1,
      total: 2,
      missing: ["src/panels/B.ts:2"],
    });
  });
});
