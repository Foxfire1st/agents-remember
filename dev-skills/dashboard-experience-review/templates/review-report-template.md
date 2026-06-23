# Dashboard Experience Review — <date>

**Target:** <frontend URL/port + backend> · **Mode:** <standalone | final-step-of-task `<task>`> ·
**Code under review:** <branch / worktree> · **Reviewer run:** dashboard-experience-review

> Findings only. Each finding is for a separate, gated fix job — nothing here was changed.

## 1 · Scenario Coverage Matrix

| Scenario | Step | Status | Note |
|---|---|---|---|
| W<N> <name> | <step> | supported / partial / **MISSING-VIEW** | <evidence / which view> |

## 2 · State Coverage Matrix

(view × forced-state; a blank/✗ cell is a missing state-rendering — see the missing-view matrix)

| View | content | empty(×3) | loading | partial | stale | error-classes | permission | overflow |
|---|---|---|---|---|---|---|---|---|
| <view> | ✓ | … | … | … | … | … | … | … |

## 3 · Missing-View Backlog (ranked)

(ranked by Ulwick importance × current satisfaction; scenario-blocking first)

1. **<missing view / state>** — blocks <scenario/step>; severity <0–4>; <why it matters>.

## 4 · Findings

| # | Sev | Dimension | OWNED / delegated-to | Finding | Evidence |
|---|---|---|---|---|---|
| 1 | 4 | <dimension> | OWNED \| design-review \| color-expert \| … | <what + where> | <settled-beat screenshot / console line> |

## 5 · Glance / self-explanatory verdict

<Per top-level view: can a newcomer name its purpose + first-3-things in ~5s? Is the entry point
obvious? Is hierarchy clear and disclosure progressive? One short paragraph + a verdict per view.>

## 6 · Delegations (by reference)

<For each delegate invoked: the view(s) handed off, and a one-line pointer to its findings folded into
section 4. Note any delegate that was unavailable at run time as a coverage gap.>

---

### Severity key
4 Blocker · 3 High · 2 Medium · 1 Low · 0 Note  (frequency × impact × persistence, mean across the
operator / incident-responder / expert personas; doctrine violations score one tier higher).
