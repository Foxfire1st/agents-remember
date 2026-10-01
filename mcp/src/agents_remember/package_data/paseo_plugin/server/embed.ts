import { readFileSync } from "node:fs";
import { join } from "node:path";

// One pair of the `paseoRuntime.embed` setting: a dashboard origin and the frame base URL a
// browser on that origin uses to reach this daemon.
export interface EmbedEntry {
  dashboardOrigin: string;
  frameBaseUrl: string;
}

// `agents-remember paseo provision` publishes the embed list here and reloads the plugin
// whenever the list changes, so a value read at load is the list in effect.
export function embedListPath(): string {
  const home = process.env.PASEO_HOME;
  if (!home) {
    throw new Error("PASEO_HOME is not set in the plugin process");
  }
  return join(home, "agents-remember", "embed.json");
}

function isEmbedEntry(value: unknown): value is EmbedEntry {
  const entry = value as Partial<EmbedEntry> | null;
  return (
    typeof entry === "object" &&
    entry !== null &&
    typeof entry.dashboardOrigin === "string" &&
    typeof entry.frameBaseUrl === "string"
  );
}

// A daemon home provision never wrote has no embed list: that is the empty list. A file that
// exists but does not hold a list of pairs is an error, not an empty list.
export function readEmbedList(): EmbedEntry[] {
  const path = embedListPath();
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    if ((error as { code?: string }).code === "ENOENT") {
      return [];
    }
    throw error;
  }
  const entries: unknown = (JSON.parse(text) as { embed?: unknown }).embed;
  if (!Array.isArray(entries) || !entries.every(isEmbedEntry)) {
    throw new Error(`${path} does not hold an embed list`);
  }
  return entries;
}
