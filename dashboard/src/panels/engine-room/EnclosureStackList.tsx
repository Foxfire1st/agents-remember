import { ListBox, ListBoxItem, type Selection } from "react-aria-components";

import { css } from "../../../styled-system/css";
import {
  chip,
  healthDot,
  phaseChip,
  stackItem,
  stackItemHead,
  stackList,
  stackMeta,
  stackTaskName,
} from "./engineRoomStyles";
import type { EngineProcessView } from "./engineRoomTypes";

const headWrap = css({ display: "flex", alignItems: "center", gap: "0.35rem", minWidth: "0" });

export function EnclosureStackList({
  views,
  selectedId,
  onSelect,
}: {
  views: EngineProcessView[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ListBox
      aria-label="Worktree enclosures"
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={selectedId ? [selectedId] : []}
      onSelectionChange={(keys: Selection) => {
        if (keys === "all") return;
        for (const key of keys) {
          if (typeof key === "string") {
            onSelect(key);
            break;
          }
        }
      }}
      className={stackList}
      data-testid="enclosure-stack-list"
    >
      {views.map(({ node, lifecycle }) => (
        <ListBoxItem
          key={node.id}
          id={node.id}
          textValue={`${node.taskName} ${node.repoName}`}
          className={stackItem({ health: node.health })}
          data-testid="enclosure-stack-item"
        >
          <div className={stackItemHead}>
            <span className={headWrap}>
              <span className={healthDot({ health: node.health })} aria-hidden="true" />
              <span className={stackTaskName}>{node.taskName}</span>
            </span>
            <span className={phaseChip({ health: node.health })}>{node.phase}</span>
          </div>
          <div className={stackMeta}>
            <span>{node.repoName}</span>
            {lifecycle ? <span className={chip}>{lifecycle.state}</span> : null}
            <span className={chip}>review {node.humanReviewStatus}</span>
            <span className={chip}>closeout {node.closeoutStatus}</span>
            {node.integrationStatus !== "not-started" ? (
              <span className={chip}>integ {node.integrationStatus}</span>
            ) : null}
          </div>
        </ListBoxItem>
      ))}
    </ListBox>
  );
}
