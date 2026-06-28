import { dashboardStore } from "./store";

// The state channel's named SSE events (serving/app.py + serving/delta.py). Each is merged
// into the store by key; the browser's EventSource auto-reconnects and the server
// re-snapshots, so a reconnect simply replaces the store via the `snapshot` event again.
const STATE_EVENTS = [
  "lifecycle",
  "lifecycle.removed",
  "enclosure",
  "enclosure.removed",
  "provider",
  "provider.removed",
  "metrics",
  "analytics",
] as const;

/** Subscribe to `GET /api/stream` (the folded-projection channel). Returns a disposer. */
export function connectState(base = ""): () => void {
  const source = new EventSource(`${base}/api/stream`);
  const store = dashboardStore.getState(); // actions are stable references

  source.addEventListener("snapshot", (event) => {
    store.applySnapshot(JSON.parse((event as MessageEvent).data));
  });
  for (const name of STATE_EVENTS) {
    source.addEventListener(name, (event) => {
      store.applyDelta(name, JSON.parse((event as MessageEvent).data));
    });
  }
  source.addEventListener("open", () => store.setConn("live"));
  source.addEventListener("error", () => store.setConn("signal-lost")); // SIGNAL LOST fiction

  return () => source.close();
}

/**
 * Subscribe to `GET /api/events` (the raw `ar-observer-event/v1` channel). Verbatim JSONL
 * lines for the Event River; the browser carries the opaque byte-offset `Last-Event-ID`
 * cursor across reconnects. A separate connection from the state channel by design.
 */
export function connectEvents(
  onLine: (line: string) => void,
  base = "",
  onReady?: () => void,
): () => void {
  const source = new EventSource(`${base}/api/events`);
  source.addEventListener("event", (event) => onLine((event as MessageEvent).data));
  source.addEventListener("ready", () => onReady?.());
  return () => source.close();
}
