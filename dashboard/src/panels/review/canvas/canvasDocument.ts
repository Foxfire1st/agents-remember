/** Document liveness is tied to a port, because the iframe WindowProxy survives navigation. */
import { useCallback, useEffect, useRef, type RefObject } from 'react';

import type { CanvasSession } from './canvasApi';

function phaseOf(event: MessageEvent, session: CanvasSession, frame: HTMLIFrameElement | null) {
  if (event.source !== frame?.contentWindow) return null;
  const value = event.data;
  if (value?.schema !== 'ar-canvas-document/v1' || value.session !== session.session) return null;
  return value.phase;
}

export function useDocumentPresence(
  session: CanvasSession | null,
  frameRef: RefObject<HTMLIFrameElement | null>,
  onDeparture: () => void,
) {
  const inspect = useRef<() => void>(() => undefined);
  useEffect(() => {
    if (!session) return;
    let departed = false;
    let port: MessagePort | null = null;
    let challenge: string | null = null;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const leave = () => {
      if (departed) return;
      departed = true;
      clearTimeout(timeout);
      // The departed document's port is closed at departure, before its channel is revoked, not
      // later when React runs this effect's cleanup: nothing reads the port once departed.
      port?.close();
      port = null;
      onDeparture();
    };
    inspect.current = () => {
      if (!session.sandbox.includes('allow-scripts')) return;
      challenge = crypto.randomUUID();
      clearTimeout(timeout);
      timeout = setTimeout(leave, 250);
      port?.postMessage(challenge);
    };
    const onMessage = (event: MessageEvent) => {
      if (departed) return;
      const phase = phaseOf(event, session, frameRef.current);
      if (phase === 'departed') leave();
      if (phase !== 'entered' || event.ports.length !== 1) return;
      if (port) {
        leave();
        return;
      }
      port = event.ports[0];
      port.onmessage = (answer) => {
        if (challenge !== null && answer.data === challenge) {
          challenge = null;
          clearTimeout(timeout);
        }
      };
      if (challenge !== null) port.postMessage(challenge);
    };
    window.addEventListener('message', onMessage);
    return () => {
      departed = true;
      inspect.current = () => undefined;
      clearTimeout(timeout);
      port?.close();
      window.removeEventListener('message', onMessage);
    };
  }, [session, frameRef, onDeparture]);
  return useCallback(() => inspect.current(), []);
}
