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
  stackRepo,
  stackTaskName,
} from "./styles";
import type { EngineProcessView } from "./engineRoomTypes";

const headWrap = css({ display: "flex", alignItems: "center", gap: "0.35rem", minWidth: "0" });

function enclosureLabel(node: EngineProcessView["node"]) {
  return node.leafId || node.taskName;
}

function enclosureContext(node: EngineProcessView["node"]) {
  return node.leafId ? `${node.taskName} · ${node.repoName}` : node.repoName;
}

export function EnclosureStackList({
  views,
  selectedKey,
  onSelect,
}: {
  views: EngineProcessView[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <ListBox
      aria-label="Worktree enclosures"
      selectionMode="single"
      disallowEmptySelection
      selectedKeys={selectedKey ? [selectedKey] : []}
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
      {views.map(({ node, lifecycle }) => {
        const label = enclosureLabel(node);
        return (
          <ListBoxItem
            key={node.worktreeGroup}
            id={node.worktreeGroup}
            textValue={`${label} ${node.taskName} ${node.repoName}`}
            className={stackItem({ health: node.health })}
            data-testid="enclosure-stack-item"
          >
            <div className={stackItemHead}>
              <span className={headWrap}>
                <span className={healthDot({ health: node.health })} aria-hidden="true" />
                <span className={stackTaskName}>{label}</span>
              </span>
              <span className={phaseChip({ health: node.health })}>{node.phase}</span>
            </div>
            <div className={stackRepo}>{enclosureContext(node)}</div>
            <div className={stackMeta}>
              {lifecycle ? <span className={chip}>{lifecycle.state}</span> : null}
              <span className={chip}>review {node.humanReviewStatus}</span>
              <span className={chip}>closeout {node.closeoutStatus}</span>
              {node.integrationStatus !== "not-started" ? (
                <span className={chip}>integ {node.integrationStatus}</span>
              ) : null}
            </div>
          </ListBoxItem>
        );
      })}
    </ListBox>
  );
}
