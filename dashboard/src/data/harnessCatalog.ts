/** One launchable harness advertised by the pre-session server catalog. */
export interface HarnessInfo {
  id: string;
  name: string;
  detected: boolean;
}

export type HarnessCatalogErrorKind = "http" | "network" | "protocol";

export type HarnessCatalogRead =
  | { status: "ready"; harnesses: HarnessInfo[] }
  | { status: "empty" }
  | { status: "error"; kind: HarnessCatalogErrorKind; detail: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseHarness(value: unknown, index: number): HarnessInfo | string {
  if (!isRecord(value)) return `harnesses[${index}] is not an object`;
  if (typeof value.id !== "string" || value.id.length === 0) {
    return `harnesses[${index}].id is not a non-empty string`;
  }
  if (typeof value.name !== "string" || value.name.length === 0) {
    return `harnesses[${index}].name is not a non-empty string`;
  }
  if (typeof value.detected !== "boolean") {
    return `harnesses[${index}].detected is not a boolean`;
  }
  return { id: value.id, name: value.name, detected: value.detected };
}

/**
 * Read and validate the server-owned launch catalog. Request lifetime belongs to the caller via
 * ``signal``; this function owns only HTTP/protocol classification and never retries.
 */
export async function readHarnessCatalog(
  base = "",
  options: { signal?: AbortSignal } = {},
): Promise<HarnessCatalogRead> {
  let response: Response;
  try {
    response = await fetch(`${base}/api/harnesses`, { signal: options.signal });
  } catch {
    return { status: "error", kind: "network", detail: "the daemon did not answer" };
  }
  if (!response.ok) {
    return { status: "error", kind: "http", detail: `HTTP ${response.status}` };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { status: "error", kind: "protocol", detail: "response body is not JSON" };
  }
  if (!isRecord(body) || !Array.isArray(body.harnesses)) {
    return {
      status: "error",
      kind: "protocol",
      detail: "response.harnesses is not an array",
    };
  }

  const harnesses: HarnessInfo[] = [];
  for (const [index, value] of body.harnesses.entries()) {
    const parsed = parseHarness(value, index);
    if (typeof parsed === "string") {
      return { status: "error", kind: "protocol", detail: parsed };
    }
    harnesses.push(parsed);
  }
  return harnesses.length === 0 ? { status: "empty" } : { status: "ready", harnesses };
}
