# Roadmap

This page holds directions and ideas. It holds no commitments and no dates: nothing on it is a
plan, and nothing on it is promised. Each direction names the part of today's product that it
builds on. Each direction under "Beyond code" also says what the product does not do today.

[The front page](README.md) and the [documentation](docs/README.md) describe the product as it
is.

## The model behind the directions

Agents Remember applies one model to code. Four parts of that model carry the directions on this
page.

- **A statement is tied to what realizes it.** A
  [knowledge record](docs/concepts.md#knowledge-record) states one rule, called an
  [invariant](docs/concepts.md#invariant). [Cards](docs/concepts.md#card) name the code that
  realizes the invariant and the tests that prove it. Each reference is an
  [anchor](docs/concepts.md#anchor): a path, a place in the file and the hash of the content.
- **A change answers for the knowledge it touches.** When code changes, the anchors on it go
  stale, and a [drift check](docs/concepts.md#drift-check) finds each of them. A task lands only
  after the [knowledge gate](docs/concepts.md#knowledge-gate) has compared the changed code with
  the changed memory.
- **A decision keeps what was not chosen.** A decision record keeps each rejected or deferred
  alternative with its reason and the condition under which to reconsider it.
- **A map for what no context window holds.** A card describes one source file, and a route
  overview describes one folder. An agent reads the part of the map that it needs.

The directions under "Beyond code" carry this model to subjects other than code.

## Beyond code

### Research: a map of a field that stays current

A field of research lives in thousands of publications. Each one adds a claim, an experiment or a
doubt, and no reader holds them all. The direction is a map of the field that is versioned like
code. It holds the claims, the hypotheses, the experiments and the evidence, each tied to the
publication it comes from. Agents read the publications from the oldest to the newest, so the map
also shows what the field believed at each time and why that changed.

A new publication does more than add an entry. It answers for the claims that it supports,
contradicts or leaves untouched, as a change of code answers for the knowledge it touches.

One picture. For about thirty years, detectors counted fewer neutrinos from the Sun than the
model of the Sun predicted. Two hypotheses stood side by side: the model of the Sun was wrong, or
the neutrinos changed on their way to the Earth. On the map, both hypotheses are records, each
with its evidence. A later experiment can decide between them, because it counts the neutrinos of
one kind and the neutrinos of all kinds. The map links that experiment to both hypotheses as a
candidate for the decision, before its result exists. When the result arrives, both hypotheses
change their state, and every claim that rested on the one that lost is marked for a new look.

The map is built toward a goal, for example the open problems between today's experiments and a
fusion power plant. The goal decides which publications enter the map and where the next result
matters most.

**What it builds on.** An invariant is tied by anchors to the code that realizes it and to the
tests that prove it, and an anchor goes stale when its content changes. A decision record keeps
the alternatives that were not chosen, each with the condition under which to reconsider it. When
a task changes what a decision names for reconsideration, the
[worklist](docs/concepts.md#worklist-and-knowledge-gate) of the task names the decision again, and
the task does not land until the Curator has answered it.

**What the product does not do today.** The product does none of this for research. Its anchors
point into the files of a code repository, its proofs are tests, and none of its tools reads a
publication. The condition under which to reconsider a decision is a sentence for a person: the
product never evaluates it.

### Model weights: a checked map of what the parts of a network do

A neural network is a file of numbers. Nothing in the file says what a part of the network does,
and a large network fits in no context window. The direction is a checked, versioned map of a
network: what a layer or a part of a layer does, tied to the weights that it describes and to the
experiment that proves it.

One picture. A statement on the map says that one part of a language model copies a word that
came earlier in the text. The statement is anchored to the weights of that part by their hash,
and an experiment on the running network proves it. Then someone edits the weights. The edit is a
change with a before and an after, like a change of code: the hash of the edited weights changes,
each statement on them goes stale, and the experiments run again. An agent that has never held
the whole network plans the next edit from the map.

A public source for the idea is the post
["Sparks of Weight-Space Programming"](https://rikiyat.github.io/blog/weight-space-programming/).
It reports that a language model can read and write the weights of small neural networks
directly. Its authors call their results very early. In the post, most of the networks fit into
the context window of the model, and a network of 1.5 million parameters did not.

**What it builds on.** Cards and route overviews are a map of code that fits in no context
window. An anchor holds the hash of the content it describes and goes stale when that content
changes. The proofs of an invariant are tests in the code repository.

**What the product does not do today.** The product reads source files, not weights. An anchor
names a whole file, a range of lines or a symbol, so it cannot name a part of a network. No
record holds an experiment on a running network as a proof.

### Organisations: decisions that keep their reasons

A company decides something, and two years later nobody knows why. The decision survives as a
line in a plan. The reasons, the assumptions and the alternatives left with the people and the
meetings. The direction is a memory of decisions: why a decision was made, under which
assumptions, what happened afterwards and what would justify opening it again.

One picture. A company decides against support for work without a network connection, because
its customers have reliable connections and the feature is costly. Two years later, a new group
of customers works without a connection. The changed assumption points at the old decision. The
people who decide see its reasons, the alternatives that were considered and the condition under
which the company wanted to look again. Nothing is reversed by itself: the memory brings the
question to the people who own it.

Such a memory keeps three states apart that an archive merges: an idea that was rejected, an idea
that was disproven, and an idea that was blocked by a constraint that no longer holds. Each
calls for a different answer when someone proposes the idea again.

**What it builds on.** A decision record holds its context and at least two alternatives, of
which one is chosen. Each rejected or deferred alternative carries its reason and the condition
under which to reconsider it, and the record links what the decision governs. When a task changes
what a decision names for reconsideration, the task answers: the alternative stays rejected, with
a reason, or the decision is raised and waits for the user.

**What the product does not do today.** The decisions of the product are decisions about code:
a decision governs code, knowledge records, routes and requirements. An alternative that was not
chosen is rejected or deferred. It has no state of its own for a disproven idea or for a blocked
idea. A decision comes back up only when a task changes what the decision names. A change in the
world outside the repository does not raise it.

## Nearer: for code

These directions stay with code. Each starts from what the product does today.

- **One answer to "what governs this code?"** Today the
  [intent review](docs/concepts.md#intent-review) shows the change of one task
  by the invariants it touches, and two tools name the card and the knowledge records of a source
  file. The direction is one place that answers the question for any change and for any piece of
  code: what it is, what it touches and what governs it.
- **Memory that compounds.** Today a landed task leaves its invariants, decisions and proofs in
  the memory, and a later task finds them through the files it works on: the tools name the card
  and the knowledge records of a path and read a record by its id, and no tool searches the
  memory by words. The direction is that this understanding becomes more useful
  with every task: agents draw on more of it when they plan and build, and the memory stays
  consistent as it grows.
- **One truth across repositories.** Today the context of one repository can include the code
  and the memory of another repository that its settings allow. The direction is that the
  memories of several repositories refer to each other, so that an invariant reaches every
  repository that expresses it.
- **Less work by hand.** Today the role agents carry a task from an agreed requirement to a
  landed change, and a Manager coordinates the tasks of a master. The direction is that a whole
  master runs to its landing with the user as the only person in it, at a lower cost in
  tokens and in attention.
- **Evidence that it helps.** Today the [benchmark runner](docs/concepts.md#benchmark-runner)
  prepares, for a [benchmark case](docs/benchmarks-methodology.md#case-design), one environment
  with the memory and one without it. The direction is a measurement that shows whether agents
  work better with this memory than without it.
