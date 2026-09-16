# Reference — Rationale, Provenance, And Credits

> **Reference-only.** Nothing in this file is injected into the normative path. It exists so that
> rationale and history stop competing with obligations for a seat's attention, while remaining
> available to a human or an architecture seat that needs to know *why* a rule reads the way it does.
> The normative sources are `../SKILL.md`, `../core/`, `../roles/`, and `../operations/`.

## Why the corpus is shaped this way

Role duties were scattered through shared lifecycle prose, role files, templates, and generated
installs. Repeated wording hid contradictions, and every new agent paid for context it did not need.
This corpus therefore separates four kinds of text that used to be interleaved:

| Kind | Where it lives now | Why |
| --- | --- | --- |
| Rules that genuinely apply across roles | `../core/` — authored once | A shared rule restated per role drifts silently |
| Rules specific to one seat | `../roles/<role>.md` — self-contained | A seat must never need another role's prose to learn its own obligations |
| Procedure scoped to one kind of work | `../operations/` — nine blocks | Long interwoven procedures become separately loadable instead of padding every role |
| Rationale, history, superseded rulings | this directory | Reason matters to reviewers and architects, not to a seat mid-task |

The consolidation rule that produced it: **frequency of repetition is never authority.** A stale
imperative does not become canonical because it appears in nine files. Each existing obligation was
given an explicit old anchor, a disposition, and a new anchor. That migration map belongs to the task
that produced it (`260915-CAPS-L1`, `notes/reports/caps-l1-obligation-map.md` under the coordination
tasks tree) rather than to the shipped corpus, because it describes the corpus's own history.

## Provenance of the consolidation

- Source: the atomic experimental master `260915_role-capsules-and-native-eve`, leaf
  `260915-CAPS-L1`, branch `ar/260915-caps-l1`, based on IAS
  `ar/260713_improved-agentic-system` at `67b21aeb66df96a971a33ae431a13992f2528b45`.
- The role-instruction doctrine accepted on the ambient checkout branch
  `ar/260831_lifecycle-owned-completion-relay` (LOCR) which IAS lacked was carried in as applicable
  developer rulings — see `rulings.md`. LOCR's serving/runtime changes (terminal observer, owner
  signals, app routes, dashboard) are **not** part of this corpus and were not imported.
- The architecture that fixes the authority boundaries, the six-section readable order, the
  single-source rule, and the "no second `capsules/` prose tree" constraint is the master's
  `design/architecture.md`.

## Why a manifest and not a generator

`../composition-manifest.json` is routing metadata: role → core + role + operation blocks, plus the
operation vocabulary and applicability. It deliberately contains **no prose**. The deterministic
compiler that consumes it is a later leaf; this corpus only has to make the source selection
explicit, unambiguous, and checkable without a model call. Keeping the map in the corpus (rather
than in a build script) means a reviewer can diff the routing against the files that exist.

## Why the launcher is not a tenth role

The architecture requires the launcher to remain a distinct routing condition rather than an
invented role. Authoring the launcher's obligations in `../core/launcher.md` keeps that boundary
structural: enumerating `roles/` shows only seats, and the router's condition 3 still has one
complete home for its duties.

The registry holds **ten** roles, and the launcher is not one of them. The tenth is `bootstrap`, the
new user's first-hour **free agent** (developer ruling 2026-09-16): it has its own `roles/bootstrap.md`
and its own operation, and it is reached by a session-open call with its role and no task document
rather than by dispatch. So the count and the distinction are two separate facts: the launcher is not
a role at all, while `bootstrap` is a role that simply is not a task seat.

## Why some templates keep their own shape

`../templates/` holds two different things:

- **Field schemas** a spawning seat compiles from (`architect-brief`, `worker-brief`, `manager-brief`,
  `curator-brief`) — the fields are the artifact; the rules they used to restate now live in the role
  and operation files they point at.
- **Artifact shapes** the corpus requires by name (`turn-report`, `verdict`,
  `master-handover-packet`, `conversation-handover-packet`, `impact-analysis`,
  `onboarding-coherency`, `orchestration-task`, `deep-research-report`) — the shape is the contract a
  consumer reads, so it stays.

## Credits

This skill absorbs and supersedes `l-01-session-job-lifecycle` and `l-02-agent-orchestration`
(converged 2026-07-05: lifecycle and job are one entity — one lifecycle per agent type). The
orchestration vocabulary adopts the parked `260619_agentic-control-plane` spec — jobs as
model-interpreted markdown (D6), the knob block (D7), role + lens in one file (D10), the
ambient-singleton rule (D11), per-harness variants (D12), the judge rung, short-lived workers with
structured handoff, dev-talks-to-one-architect (D15) — which in turn credits **Archon** and the
**agent-control-plane** project (D14); that credit carries forward.

## Historical notes kept out of the normative path

- **Lenses** (`../lenses.md`) are how the scoping seats read a piece of work. They were moved out of
  every role file because a dispatched role never picks a lens — its brief already carries the flavor.
- **Criteria catalogs** (`../criteria/`) are the reviewer's test bench, bound per review type. Their
  promotion ratchet (candidate → standing at ≥2 catches; standing → spot-check after N dry
  engagements; mechanizable → graduates into a gate) is reviewer doctrine, not general role reading.
- **The per-harness variant question** (D12) was answered by developer decision 2026-07-05: no
  per-harness role files exist, and harness preference is deployment configuration, not doctrine.
