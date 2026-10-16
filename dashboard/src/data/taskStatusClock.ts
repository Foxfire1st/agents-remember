import { useSyncExternalStore } from 'react';

// Every visible task word observes the same instant, including surfaces opened mid-turn.
let now = Date.now();
let timer: number | undefined;
const listeners = new Set<() => void>();
const snapshot = () => now;
const stopped = () => undefined;
const inactive = () => () => {};

function subscribe(listener: () => void) {
  if (listeners.size === 0) {
    now = Date.now();
    timer = window.setInterval(() => {
      now = Date.now();
      for (const changed of listeners) changed();
    }, 10_000);
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) {
      window.clearInterval(timer);
      timer = undefined;
    }
  };
}

export function useTaskStatusNow(active: boolean): number | undefined {
  return useSyncExternalStore(active ? subscribe : inactive, active ? snapshot : stopped, stopped);
}
