// L127-C-F09: every production branch dispatches on the same document form.
// Direct, series and root-lifecycle selections of sprint, master and task
// documents land on the matching skeleton — never a hard-coded master.
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { DetailPanel } from './DetailPanel';
import { seedProjection, seedTaskDocuments, seriesNode, taskDoc } from './test-utils';
import { enclosure, lifecycle } from '../lifecycle-list/test-utils';

const SPRINT_PATH = '/tasks/repo-a/sprint/task.json';
const MASTER_PATH = '/tasks/repo-a/m/task.json';
const LEAF_PATH = '/tasks/repo-a/m/01_leaf.json';

function sprintDoc() {
  return taskDoc({
    id: 'SPRINT',
    kind: 'master',
    form: 'sprint',
    orchestrates: ['master-a'],
    docPath: SPRINT_PATH,
    title: 'Dispatch sprint',
  });
}

function masterDoc() {
  return taskDoc({
    id: 'MASTER',
    kind: 'master',
    docPath: MASTER_PATH,
    title: 'Dispatch master',
  });
}

function leafDoc(lifecycleId?: string) {
  return taskDoc({
    id: 'LEAF',
    kind: 'subTask',
    docPath: LEAF_PATH,
    title: 'Dispatch leaf',
    lifecycleId,
  });
}

function seedWithSeries(docs: ReturnType<typeof taskDoc>[], seriesId: string, docPath: string) {
  seedProjection({
    analytics: {
      driftSnapshots: [],
      stalestSidecars: [],
      setupSummaries: [],
      setupProgress: [],
      routeCoverage: [],
      toolReports: [],
      ledgers: [],
      taskDocuments: docs,
      series: [seriesNode({ seriesId, title: `${seriesId} series`, docPath })],
      attentionQueue: [],
      engineProcesses: [],
    },
  });
}

describe('direct selections dispatch on form (L127-C-F09)', () => {
  it('draws the sprint skeleton for a direct sprint document', () => {
    seedTaskDocuments([sprintDoc()]);
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${SPRINT_PATH}`} />);
    expect(getByTestId('skeleton-sprint')).toBeTruthy();
  });

  it('draws the master skeleton for a direct master document', () => {
    seedTaskDocuments([masterDoc()]);
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${MASTER_PATH}`} />);
    expect(getByTestId('skeleton-master')).toBeTruthy();
  });

  it('draws the task skeleton for a direct leaf document', () => {
    seedTaskDocuments([leafDoc()]);
    const { getByTestId } = render(<DetailPanel selectedId={`taskdoc:${LEAF_PATH}`} />);
    expect(getByTestId('skeleton-task')).toBeTruthy();
  });
});

describe('series selections dispatch on form (L127-C-F09)', () => {
  it('draws the sprint skeleton for a sprint series', () => {
    seedWithSeries([sprintDoc()], 'sprint-series', SPRINT_PATH);
    const { getByTestId } = render(<DetailPanel selectedId="series:sprint-series" />);
    expect(getByTestId('skeleton-sprint')).toBeTruthy();
  });

  it('draws the master skeleton for a master series', () => {
    seedWithSeries([masterDoc()], 'master-series', MASTER_PATH);
    const { getByTestId } = render(<DetailPanel selectedId="series:master-series" />);
    expect(getByTestId('skeleton-master')).toBeTruthy();
  });
});

describe('root-lifecycle selections dispatch on form (L127-C-F09)', () => {
  function seedRootLifecycle(docs: ReturnType<typeof taskDoc>[]) {
    seedProjection({
      lifecycles: [lifecycle({ id: 'ROOT' })],
      enclosures: [enclosure({ enclosure: '/contracts/root', lifecycleId: 'ROOT' })],
      analytics: {
        driftSnapshots: [],
        stalestSidecars: [],
        setupSummaries: [],
        setupProgress: [],
        routeCoverage: [],
        toolReports: [],
        ledgers: [],
        taskDocuments: docs,
        series: [],
        attentionQueue: [],
        engineProcesses: [],
      },
    });
  }

  it('draws the sprint skeleton for a lifecycle-bound sprint document', () => {
    seedRootLifecycle([{ ...sprintDoc(), lifecycleId: 'ROOT' }]);
    const { getByTestId } = render(<DetailPanel selectedId="ROOT" />);
    expect(getByTestId('skeleton-sprint')).toBeTruthy();
  });

  it('draws the master skeleton for a lifecycle-bound master document', () => {
    seedRootLifecycle([{ ...masterDoc(), lifecycleId: 'ROOT' }]);
    const { getByTestId } = render(<DetailPanel selectedId="ROOT" />);
    expect(getByTestId('skeleton-master')).toBeTruthy();
  });

  it('draws the task skeleton for a lifecycle-bound leaf document', () => {
    seedRootLifecycle([leafDoc('ROOT')]);
    const { getByTestId } = render(<DetailPanel selectedId="ROOT" />);
    expect(getByTestId('skeleton-task')).toBeTruthy();
  });
});
