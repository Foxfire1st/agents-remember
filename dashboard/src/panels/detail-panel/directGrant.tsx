import { useEffect,useRef,useState } from 'react';
import { cx } from '../../../styled-system/css';
import { postDirectExecutionGrant } from '../../data/directExecutionGrant';
import { taskReferenceKey,type DocumentTaskEntry } from '../../data/taskReference';
import type {
DirectGrantNode,
TaskDocumentRef,
TaskSummary
} from '../../types/projection';
import {
directGiven,
directKnob,
directLine,
directNote,
directSwitch,
directSwitchOff,
directSwitchOn,
} from './styles';

function DirectExecutionSwitch({ entry, doc }: { entry: DocumentTaskEntry; doc: TaskSummary }) {
  const docRef = entry.reference;
  const docKey = taskReferenceKey(docRef);
  const { grant, pending, notice, run } = useDirectGrant(docKey, docRef, doc.directGrant ?? null);
  const on = grant !== null;
  return (
    <div className={directLine} data-testid="taskdoc-direct-line">
      <span>Direct execution</span>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="Direct execution"
        className={cx(directSwitch, on ? directSwitchOn : directSwitchOff)}
        data-testid="taskdoc-direct-switch"
        disabled={pending}
        onClick={() => void run(on ? 'revoke' : 'give')}
      >
        <span className={directKnob} />
      </button>
      {on && grant ? <span className={directGiven}>given by {grant.grantedBy}</span> : null}
      {notice ? (
        <span className={directNote} data-testid="taskdoc-direct-note">
          {' '}
          · {notice}
        </span>
      ) : null}
    </div>
  );
}

// The grant's words where it cannot be changed (MIK-R150 rule 12): a task that
// is not planning shows `Direct · given by <who>` when it has a grant and
// nothing when it has none.
function DirectExecutionWords({ doc }: { doc: TaskSummary }) {
  const served = doc.directGrant ?? null;
  if (!served) return null;
  return (
    <div className={directLine} data-testid="taskdoc-direct-line">
      <span className={directGiven}>Direct · given by {served.grantedBy}</span>
    </div>
  );
}

// Under the head of the task's own page (MIK-R150 rules 9, 10 and 12): one
// switch for a planning leaf, bare words for any other granted state, and
// nothing otherwise. A master page never renders this; rows carry no control.
export function TaskDirectGrant({ entry, doc }: { entry: DocumentTaskEntry; doc: TaskSummary }) {
  if (doc.kind !== 'subTask') return null;
  if (entry.stage === 'planned') return <DirectExecutionSwitch entry={entry} doc={doc} />;
  return <DirectExecutionWords doc={doc} />;
}

// Two served grants match when the server names the same giver at the same time;
// the row's other fields never reopen the control's state.
function sameServedGrant(next: DirectGrantNode | null, prev: DirectGrantNode | null): boolean {
  return (
    (next?.grantedBy ?? null) === (prev?.grantedBy ?? null) &&
    (next?.grantedAt ?? null) === (prev?.grantedAt ?? null)
  );
}

// The control's live grant: the served add/remove/issuer change replaces it, a
// refused or unreachable request keeps the actual grant, a selection change resets
// it, and a late reply is ignored. The sync keys on the document key and the
// served giver/instant content — never on grant object identity.
function useDirectGrant(
  docKey: string,
  docRef: TaskDocumentRef | undefined,
  initialGrant: DirectGrantNode | null | undefined,
): {
  grant: DirectGrantNode | null;
  pending: boolean;
  notice: string | null;
  run: (action: 'give' | 'revoke') => Promise<void>;
} {
  const fallback = initialGrant ?? null;
  const [grant, setGrant] = useState<DirectGrantNode | null>(fallback);
  const [pending, setPending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const served = useRef<{ key: string; grant: DirectGrantNode | null }>({
    key: docKey,
    grant: fallback,
  });
  const callId = useRef(0);
  // The current render's grant for the sync below. A ref mirror (never a
  // dependency) so the effect lists the content mark it truly depends on.
  const grantRef = useRef<DirectGrantNode | null>(fallback);
  grantRef.current = fallback;
  const servedMark = `${fallback?.grantedBy ?? ''}\0${fallback?.grantedAt ?? ''}`;
  useEffect(() => {
    const next = grantRef.current;
    if (served.current.key !== docKey) {
      served.current = { key: docKey, grant: next };
      callId.current += 1;
      setGrant(next);
      setNotice(null);
      setPending(false);
      return;
    }
    if (!sameServedGrant(next, served.current.grant)) {
      served.current = { key: docKey, grant: next };
      setGrant(next);
    }
  }, [docKey, servedMark]);
  const run = async (action: 'give' | 'revoke') => {
    if (!docRef) return;
    const mine = ++callId.current;
    setPending(true);
    setNotice(null);
    const outcome = await postDirectExecutionGrant(docRef.repository, docRef.path, action);
    if (mine !== callId.current) return;
    setPending(false);
    if (outcome.ok) {
      served.current = { key: docKey, grant: outcome.grant };
      setGrant(outcome.grant);
    }
    setNotice(outcome.text);
  };
  return { grant, pending, notice, run };
}
