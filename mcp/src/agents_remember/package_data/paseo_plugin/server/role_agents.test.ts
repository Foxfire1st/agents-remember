import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { readRoleAgents, roleAgentsPath } from "./role_agents.ts";

test("unchanged role files reuse one parsed value at both history sizes; registration replaces it", () => {
  const home = mkdtempSync(join(tmpdir(), "ar-role-cache-"));
  const path = roleAgentsPath(home);
  const record = { role: "worker", task: "repo/leaf.json", reportPath: "/report" };
  mkdirSync(dirname(path), { recursive: true });
  try {
    for (const size of [8, 256]) {
      const agents = Object.fromEntries(Array.from({ length: size }, (_, i) => [String(i), record]));
      writeFileSync(path, JSON.stringify({ agents }));
      const first = readRoleAgents(home);
      assert.equal(Object.keys(first).length, size);
      for (let i = 0; i < 20; i++) assert.strictEqual(readRoleAgents(home), first);
      writeFileSync(path, JSON.stringify({ agents: { parent: record, child: record } }));
      const pruned = readRoleAgents(home);
      assert.notStrictEqual(pruned, first);
      assert.deepEqual(pruned, { parent: record, child: record });
      assert.strictEqual(readRoleAgents(home), pruned);
    }
    writeFileSync(path, "{bad");
    assert.throws(() => readRoleAgents(home));
    rmSync(path);
    assert.deepEqual(readRoleAgents(home), {});
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
