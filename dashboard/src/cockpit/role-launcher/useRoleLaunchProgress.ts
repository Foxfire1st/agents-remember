import { useEffect, useState } from "react";

export function useRoleLaunchProgress(requestId: string | null): string {
  const [phase, setPhase] = useState<{ requestId: string; text: string } | null>(null);
  useEffect(() => {
    if (!requestId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const read = async () => {
      try {
        const response = await fetch("/api/role-launch/progress/" + encodeURIComponent(requestId), { signal: controller.signal });
        if (response.ok) {
          const value = await response.json() as { phase?: string };
          if (!controller.signal.aborted) setPhase({ requestId, text: value.phase === "starting" ? "Starting agent…" : "Preparing workspace…" });
        }
      } catch { /* The dispatch receipt remains the authority if this working notice is unavailable. */ }
      if (!controller.signal.aborted) timer = setTimeout(() => void read(), 500);
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [requestId]);
  return phase?.requestId === requestId ? phase.text : "Preparing workspace…";
}
