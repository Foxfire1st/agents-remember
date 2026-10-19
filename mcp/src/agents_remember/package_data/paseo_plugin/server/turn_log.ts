/**
 * MIK-R183 rule 7: the plugin's own turn-end files.
 *
 * The plugin's `console` output reaches no file the architect can read, and the
 * product reads `host-activity.json` with `extra="forbid"`, so neither can carry
 * these rows. The plugin keeps its own file instead:
 * `<PASEO_HOME>/agents-remember/turn-ends.jsonl`, one JSON object per line,
 * turned over at 2 MiB to `turn-ends.1.jsonl` (the old archive is replaced) with
 * a new file begun. A write that fails is named once in
 * `<PASEO_HOME>/agents-remember/turn-ends.error.json`, with its time and error,
 * and the rule goes on without it.
 */

import { appendFileSync, mkdirSync, renameSync, statSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

/** The file of rule 7, the archive one turnover leaves, and the failure file. */
export const TURN_END_LOG_FILE = "turn-ends.jsonl";
export const TURN_END_LOG_ARCHIVE = "turn-ends.1.jsonl";
export const TURN_END_LOG_ERROR_FILE = "turn-ends.error.json";
/** The size at which the file is turned over. */
export const TURN_END_LOG_LIMIT_BYTES = 2 * 1024 * 1024;

/** One row of the file. Every field rule 7 names is here, and nothing else. */
export interface TurnEndLogRow {
  readonly at: string;
  readonly agent: string;
  readonly role: string;
  readonly task: string;
  readonly kind: string;
  readonly count: number;
  readonly reason: string;
}

export interface TurnEndLogOptions {
  /** The turnover size, for a test that must not write 2 MiB. */
  readonly limitBytes?: number;
  /** The clock a failure file's time comes from. */
  readonly now?: () => Date;
}

export interface TurnEndLog {
  append(row: TurnEndLogRow): void;
}

/** The plugin's file inside the daemon home. */
export function turnEndLogPath(home: string): string {
  return join(home, "agents-remember", TURN_END_LOG_FILE);
}

/** The failure file inside the daemon home. */
export function turnEndErrorPath(home: string): string {
  return join(home, "agents-remember", TURN_END_LOG_ERROR_FILE);
}

/**
 * The plugin's write-only log. `home` undefined (no `PASEO_HOME`) makes it a
 * no-op: the rule still runs, and nothing is claimed to be written.
 */
export function createTurnEndLog(home: string | undefined, options: TurnEndLogOptions = {}): TurnEndLog {
  const path = home === undefined || home === "" ? null : turnEndLogPath(home);
  const errorPath = home === undefined || home === "" ? null : turnEndErrorPath(home);
  const limit = options.limitBytes ?? TURN_END_LOG_LIMIT_BYTES;
  const now = options.now ?? (() => new Date());
  let failed = false;
  return {
    append(row) {
      if (path === null || errorPath === null) return;
      const line = `${JSON.stringify(row)}\n`;
      try {
        mkdirSync(dirname(path), { recursive: true });
        turnOverIfFull(path, Buffer.byteLength(line, "utf8"), limit);
        appendFileSync(path, line, "utf8");
      } catch (error) {
        // Rule 7: the failure is named in its own file, once, and the rule goes
        // on. `host-activity.json` is not touched — its readers forbid extra keys.
        if (failed) return;
        failed = true;
        try {
          writeFileSync(
            errorPath,
            `${JSON.stringify({
              at: now().toISOString(),
              error: error instanceof Error ? error.message : String(error),
            })}\n`,
            "utf8",
          );
        } catch {
          // Nothing left to write to; the rule still goes on without its rows.
        }
      }
    },
  };
}

/** Rename the file to the archive once the next row would take it past the limit. */
function turnOverIfFull(path: string, incomingBytes: number, limitBytes: number): void {
  let size = 0;
  try {
    size = statSync(path).size;
  } catch {
    return;
  }
  if (size === 0 || size + incomingBytes <= limitBytes) return;
  // A rename over the archive replaces it, so exactly one archive is kept.
  renameSync(path, join(dirname(path), TURN_END_LOG_ARCHIVE));
}
