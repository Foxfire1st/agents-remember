import { css } from '../../../styled-system/css';

// The look of the tasks column is the one the developer approved (MIK-R144 rule 3b, the pages of
// notes/run3/operations-sidebar-proposal/pages/). Those pages are drawn on a base of 14 px; the
// dashboard's rem is 16 px. The column's text follows the task page's text instead (MIK-R163
// rule 1): a row's title is 0.8rem like a row of the task page, every other text of the column
// and of the bell's layer is 0.72rem like the page's smaller text. The panel around it
// (grammar/Panel) and its title are the dashboard's shared parts and keep the dashboard's size.
// The tabs in the head (the grouping switch, the bell, "Clear all") are the view tabs at the
// column's size: `columnTabButton` of grammar/viewTabLook.

// The caret's cell. A row without a caret keeps it empty, so the marks stand in one line.
export const caretCell = css({
  flex: 'none',
  width: '1ch',
  fontSize: '0.72rem',
  color: 'muted',
});
