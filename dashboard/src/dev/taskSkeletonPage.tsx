import { useEffect, useState } from 'react';

import { dashboardStore } from '../data/store';
import { DetailPanel } from '../panels/detail-panel/DetailPanel';
import snapshot from '../fixtures/snapshot.json';
import { asServedProjection } from '../test/servedProjection';
import { RICH_DOC_PATHS, richApiAnswer, richTaskDocuments } from './taskSkeletonDocs';

// A deterministic mounted-UI surface for the task-page skeletons (MIK-R127
// evidence): the real task page (the Operations DetailPanel) opened against
// the seeded snapshot documents, one form per `?form=` value, so a reviewer
// can screenshot the mounted sprint / master / task skeletons at one URL each.
// `?long=1` appends clearly synthetic long blocks (ten-line objective/design,
// ten requirements, ten references, a long freeform section) to exercise the
// page's bounded previews and own views; the documents stay the seeded SIM
// representatives and the augmentation is disclosed in the evidence log.
// `?rich=1` swaps in the made-up documents of taskSkeletonDocs.ts, which hold
// every block of every form, and answers the page's own requests with made-up
// data. `?width=735` draws the panel as wide as the cockpit's middle column,
// since the page lays itself out by its panel and not by the window.
const FULL_PROJECTION = asServedProjection(snapshot);

function lengthen(text: string, prefix: string): string {
  const lines = Array.from({ length: 10 }, (_, i) => `${prefix} evidence line ${i + 1}`);
  return `${text}\n${lines.join('\n')}`;
}

const FORM_DOC_PATH: Record<string, string> = {
  sprint: '/tasks/agents-remember/sim-sprint/task.json',
  master: '/tasks/agents-remember/sim-master-a/task.json',
  task: '/tasks/sim/01.md',
};

function longProjection(): typeof FULL_PROJECTION {
  const projection = structuredClone(FULL_PROJECTION);
  for (const doc of projection.analytics.taskDocuments) {
    doc.objective = lengthen(doc.objective, 'Objective');
    doc.design = lengthen(doc.design ?? '', 'Design');
    for (let i = 0; i < 9; i += 1) {
      doc.requirements.push(`Evidence requirement ${i + 1}`);
      doc.references.push(`Evidence reference ${i + 1}`);
    }
    doc.sections.push({
      kind: 'freeform',
      heading: 'Evidence',
      body: `Evidence section prose.\n\n${Array.from({ length: 10 }, (_, i) => `Evidence body line ${i + 1}`).join('\n')}`,
    });
  }
  return projection;
}

function richProjection(): typeof FULL_PROJECTION {
  const projection = structuredClone(FULL_PROJECTION);
  projection.analytics.taskDocuments = richTaskDocuments();
  projection.analytics.series = [];
  projection.lifecycles = [];
  projection.closeoutQueues = [];
  return projection;
}

// The page's own requests, answered from the made-up documents. Anything else
// goes to the network as before.
function answerFromRichDocuments(): () => void {
  const original = window.fetch;
  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const answer = richApiAnswer(url, typeof init?.body === 'string' ? init.body : undefined);
    if (answer === undefined) return original(input, init);
    return new Response(JSON.stringify(answer), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  return () => {
    window.fetch = original;
  };
}

export function TaskSkeletonPage() {
  const params = new URLSearchParams(window.location.search);
  const form = params.get('form') ?? 'task';
  const long = params.get('long') === '1';
  const rich = params.get('rich') === '1';
  const width = Number(params.get('width')) || 0;
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const restore = rich ? answerFromRichDocuments() : undefined;
    const projection = rich ? richProjection() : long ? longProjection() : FULL_PROJECTION;
    dashboardStore.getState().applySnapshot(projection);
    setReady(true);
    return restore;
  }, [long, rich]);
  if (!ready) return null;
  const formPaths: Record<string, string> = rich ? RICH_DOC_PATHS : FORM_DOC_PATH;
  const selected = formPaths[form] ?? formPaths.task;
  const panel = <DetailPanel selectedId={`taskdoc:${selected}`} />;
  return width > 0 ? <div style={{ width: `${width}px`, margin: '0 auto' }}>{panel}</div> : panel;
}
