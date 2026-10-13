import { css } from '../../../styled-system/css';

// The look of the tasks column is the one the developer approved (MIK-R144 rule 3b, the pages of
// notes/run3/operations-sidebar-proposal/pages/). Those pages are drawn on a base of 14 px; the
// dashboard's rem is 16 px. Every measure of the column (LifecycleList, AttentionQueue,
// TaskGroupDisclosure) is therefore the pages' number times 0.875, so the column has the pages'
// sizes in pixels: a row of 21 px with text of 11.2 px. The panel around it (grammar/Panel) and
// its title are the dashboard's shared parts and keep the dashboard's size. The tabs in the head
// (the grouping switch, the bell, "Clear all") are the view tabs at the column's size:
// `columnTabButton` of grammar/viewTabLook.

// The caret's cell. A row without a caret keeps it empty, so the marks stand in one line.
export const caretCell = css({
  flex: 'none',
  width: '1ch',
  fontSize: '0.595rem',
  color: 'muted',
});
