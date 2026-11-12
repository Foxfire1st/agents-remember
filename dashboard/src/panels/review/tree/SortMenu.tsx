// The one Sort control (MIK-R89 v2 rule 3; mockup `sort-menu`, app.js:241-255): it replaces the Order
// button and keeps its stored preference (triageOrderPreference.ts). L89's menu holds the
// deterministic sorts; L183 adds the judgment group. The four memory sorts are shown unavailable,
// with the reason, while the card read gives no memory values; Priority then applies.
//
// The popup is the mockup's (MIK-R89 v2 rule 1 as amended by d9a1ec8f item 4): 350 px wide, each row a
// mark, the name and its short reason at the end, a rule between the groups, and one explanatory
// section. The four memory rows carry their reason as the item's description, spoken but not drawn,
// so each stays one line in 350 px; the section says where their facts come from, or why they are
// unavailable.
import { useId } from 'react';
import type { Selection } from 'react-aria-components';
import {
  Button,
  Header,
  Menu,
  MenuItem,
  MenuSection,
  MenuTrigger,
  Popover,
  Separator,
  Text,
} from 'react-aria-components';

import type { SortOption } from '../changeSetOrder';
import { isJudgmentSort, MEMORY_SORTS, SORT_LABELS, type ReviewSort } from '../reviewSelection';
import { treeOrderStore } from '../triageOrderPreference';
import * as styles from './treeStyles';

function SortItem({ option, chosen }: { option: SortOption; chosen: boolean }) {
  const memory = MEMORY_SORTS.has(option.sort);
  return (
    <MenuItem
      id={option.sort}
      textValue={option.label}
      className={styles.menuItem}
      data-testid="review-tree-sort-option"
      data-sort={option.sort}
    >
      <span aria-hidden="true">{chosen ? '●' : ''}</span>
      <Text slot="label">{option.label}</Text>
      <Text slot="description" className={memory ? styles.unseen : styles.menuWhy}>
        {option.available ? option.hint : `unavailable: ${option.reason ?? ''}`}
      </Text>
    </MenuItem>
  );
}

// The explanatory section: where the memory facts come from, or the one reason the four are
// unavailable.
function memoryHint(sorts: readonly SortOption[]): string {
  const unavailable = sorts.find((option) => MEMORY_SORTS.has(option.sort) && !option.available);
  return unavailable
    ? `Unavailable: ${unavailable.reason ?? 'no memory values'}.`
    : 'Read from the after side’s index.';
}

export function SortMenu({
  sorts,
  sort,
  requested,
  onSort = (next) => treeOrderStore.getState().setOrder(next),
}: {
  sorts: readonly SortOption[];
  // The sort applied, and the stored one (they differ while a memory sort is unavailable; the tree's
  // status line then says why, `sortFallback`).
  sort: ReviewSort;
  requested: ReviewSort;
  onSort?: (sort: ReviewSort) => void;
}) {
  const hint = useId();
  const unavailable = sorts.filter((option) => !option.available).map((option) => option.sort);
  const select = (keys: Selection) => {
    const [key] = keys === 'all' ? [] : [...keys];
    if (key !== undefined) onSort(key as ReviewSort);
  };
  return (
    <MenuTrigger>
      <Button className={styles.control} data-testid="review-tree-sort" aria-label={`Sort: ${SORT_LABELS[sort]}`}>
        <span className={styles.sortWord}>Sort:</span> <span className={styles.sortKey}>{SORT_LABELS[sort]}</span> ▾
      </Button>
      <Popover className={styles.popover} placement="bottom start" data-testid="review-tree-sort-popover">
        <Menu
          aria-label="Sort"
          aria-describedby={hint}
          selectionMode="single"
          disallowEmptySelection
          selectedKeys={[requested]}
          disabledKeys={unavailable}
          onSelectionChange={select}
        >
          <MenuSection>
            <Header className={styles.menuGroup}>Always available</Header>
            {sorts
              .filter((option) => !MEMORY_SORTS.has(option.sort) && !isJudgmentSort(option.sort))
              .map((option) => (
                <SortItem key={option.sort} option={option} chosen={option.sort === requested} />
              ))}
          </MenuSection>
          <Separator className={styles.menuRule} />
          <MenuSection>
            <Header className={styles.menuGroup}>Memory facts at the location</Header>
            {sorts
              .filter((option) => MEMORY_SORTS.has(option.sort))
              .map((option) => (
                <SortItem key={option.sort} option={option} chosen={option.sort === requested} />
              ))}
          </MenuSection>
          {sorts.some((option) => isJudgmentSort(option.sort)) ? (
            <>
              <Separator className={styles.menuRule} />
              <MenuSection data-testid="review-tree-sort-judgment-section">
                <Header className={styles.menuGroup}>Explainer’s judgment</Header>
                {sorts
                  .filter((option) => isJudgmentSort(option.sort))
                  .map((option) => (
                    <SortItem key={option.sort} option={option} chosen={option.sort === requested} />
                  ))}
              </MenuSection>
            </>
          ) : null}
        </Menu>
        <p id={hint} className={styles.menuHint} data-testid="review-tree-sort-hint">
          {memoryHint(sorts)}
        </p>
      </Popover>
    </MenuTrigger>
  );
}

// The line the tree shows while the stored sort is unavailable and Priority applies.
export function sortFallback(sorts: readonly SortOption[], sort: ReviewSort, requested: ReviewSort): string {
  if (requested === sort) return '';
  const option = sorts.find((item) => item.sort === requested);
  return `${SORT_LABELS[requested]} is unavailable: ${option?.reason ?? 'no memory values'}; Priority applies.`;
}
