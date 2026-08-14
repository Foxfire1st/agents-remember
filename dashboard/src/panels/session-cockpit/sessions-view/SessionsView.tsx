// The canonical Chats cockpit: rail / stage / inspector as a react-resizable-panels group with
// the narrow-width rules (inspector auto-collapses <~1100px, rail <~900px — both reopenable) and
// the keep-alive stage/rail/inspector anatomy. The state/effects/commands live in
// sessionsViewController.ts and the render panels in sessionsViewBody.tsx.
import { memo } from "react";

import { SessionsViewBody } from "./sessionsViewBody";
import {
  useSessionsViewController,
  type SessionsViewProps,
} from "./sessionsViewController";

export type { SessionsViewProps } from "./sessionsViewController";

function SessionsViewImpl(props: SessionsViewProps) {
  const view = useSessionsViewController(props);
  return <SessionsViewBody view={view} />;
}

// Memoized to bound tab-switch CPU cost: a keep-alive cockpit layer — the shell re-renders on every
// view switch, and the memo gate skips this whole subtree unless `active` (or another prop)
// actually changed; the cockpit's own store subscriptions still drive its updates.
export const SessionsView = memo(SessionsViewImpl);
