"""One canonical completion relay: one observation owner, no alternate reader or push.

The completion relay is ``adapter evidence -> catalog -> notifier -> durable inbox -> owner``.
``LOCR-R14@v1`` requires that relay to be the ONLY progression path: the existing terminal
liveness sweeper is driven by exactly two lifecycle-owned entry points (one startup prime and
one recurring steady-state caller), no HTTP route may advance observation, and an owner is
reached through a durable inbox row rather than by any second completion protocol.

This module is the executable form of that claim. It measures six independent things over
``mcp/src/agents_remember`` and, for each, names the reviewed set and the remedy:

===========================  =========================================================
measurement                  what a change to it means
===========================  =========================================================
sweeper constructions        a second observation authority now exists
sweeper reads                the sweeper carrier reached a new scope -- the hand-over
                             a helper-based bypass would leave behind
sweeper mutation sites       something other than a declared entry point advanced
                             observation -- or the preserved notifier call was removed
observation body references  a new scope enters the shared observer pass, so a
                             request can make observation progress again
observation owner            a new scope *starts* observation: it awaits the startup
references                   prime or schedules the steady-state owner
route handler modules        the set of request entry points, which must stay disjoint
                             from every observation authority above
wire submission sites        a module reaches a seat outside the durable-inbox relay
===========================  =========================================================

Every constant below is a REVIEWED SET. Widening one is exactly the change this guard exists
to make visible, so it is never a routine edit -- it is the boundary R14 draws.

The reference censuses match a *name*, not a spelling: bare ``name(...)``, a bound
``from ... import name as other``, ``module.name(...)``, and ``getattr(module, "name")`` /
``getattr(module, "name", default)`` all resolve to the same authority. **Matching only the bare
call form was this guard's first delivered defect**: a route calling
``_app_lifespan._observe_terminal_catalog(...)`` is an ``ast.Attribute``, and a route scheduling
``asyncio.create_task(_terminal_observation_loop(runtime))`` names neither the body nor the
sweeper at all -- both restored the dashboard as the clock with every guard case green.

WHAT THIS GUARD DOES NOT COVER
------------------------------
1. It resolves literal member names -- attribute chains, one local-binding hop, and
   ``getattr(x, "literal"[, default])`` -- so ``self.liveness_sweeper.refresh``,
   ``sweeper = runtime.liveness_sweeper; sweeper.refresh()`` and
   ``getattr(runtime, "liveness_sweeper").refresh()`` are all reported. A member name COMPUTED at
   runtime (``getattr(runtime, field_name)``), a callable carried in a container
   (``HANDLERS["body"](...)``), and a callable handed across a function boundary and renamed there
   are NOT resolved. Measured non-catch: the computed-name form, reproduced at exit 0.
2. The sweeper census is receiver-shaped, not type-shaped: a brand-new observation class with a
   differently named method would be invisible to it. The single-construction rule below is what
   covers a second *instance* of the reviewed type.
3. The wire census closes the harness submission primitive. A second delivery mechanism built on
   a different primitive (a new IPC channel, a direct adapter call) is outside its reach.
4. It measures source shape only. That the declared entry points are *scheduled correctly* -- the
   startup prime runs before admission, the steady loop keeps its completion-relative cadence --
   is behavioural and is owned by the observation-loop and startup-prime suites. That they are
   scheduled *only from the serving startup scope* IS measured, by the owner-reference census.
5. Doctrine is deliberately out of scope for this module. R14's clauses 4-5 ("Roles do not gain
   instructions to poll...", "Subordinates do not gain a mandatory post-report completion
   message") are absence claims about the range's contribution and are evidenced by the range
   diff for this leaf. The campaign does carry an executable doctrine guard over the canonical
   ``skills/l-01-agent-lifecycles`` tree -- ``mcp/tests/test_lifecycle_turn_truth_doctrine.py``,
   owned by the doctrine-reconciliation requirement, whose ``second-completion-message`` claim
   already pins R14 clause 5. A SECOND doctrine guard here would be the duplicate mechanism R14
   forbids, so this module does not build one.

The binding scope is ``mcp/src/agents_remember`` excluding ``package_data`` (generated copies).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# --- the reviewed sets ---------------------------------------------------------------------------

TERMINAL_OBSERVATION_OWNER = "serving/_app_lifespan.py"
"""The one module allowed to advance terminal observation."""

SWEEPER_TYPE = "TerminalCatalogLivenessSweeper"
SWEEPER_TYPE_OWNER = "serving/terminal_liveness.py"
"""The existing sweeper: the only terminal-observation mutation authority."""

SWEEPER_CONSTRUCTION_OWNER = "serving/app.py"
"""The one module that builds the serving runtime's sweeper, and the only one that may."""

SWEEPER_RECEIVER_FIELD = "liveness_sweeper"
"""The ``_ServingRuntime`` field that carries the one sweeper to its two entry points."""

SWEEPER_MUTATION_API = "refresh"
"""The sweeper's own mutation entry point; every call to it progresses observation."""

SWEEPER_MUTATION_AUTHORITIES = frozenset(
    {
        # R01's one shared body: the single place that reaches the mutation API. It is entered
        # by the startup prime (R18) and by the recurring steady-state caller (R01) and by
        # nothing else -- ``OBSERVATION_ENTRY_CALLERS`` below is that half of the claim.
        (TERMINAL_OBSERVATION_OWNER, "_observe_terminal_catalog"),
        # The pre-existing notifier handoff. It is not a third *progression* owner: the
        # notifier's predicates read current turn boundaries, and delivery correctness may not
        # depend on a browser polling the terminal route. Pinned here so it is neither deleted
        # (R14 requires the existing notifier authority to stay intact) nor multiplied.
        (TERMINAL_OBSERVATION_OWNER, "_agent_notifier_loop"),
    }
)

OBSERVATION_ENTRY_CALLERS = frozenset({"_prime_terminal_observation", "_terminal_observation_loop"})
"""Exactly the two lifecycle-owned entry points into the shared observation body."""

OBSERVATION_BODY = "_observe_terminal_catalog"
"""The one shared observer pass both entry points enter; reaching it IS entering the relay."""

OBSERVATION_OWNER_AUTHORITIES = frozenset({(TERMINAL_OBSERVATION_OWNER, "lifespan")})
"""The only (module, scope) pair allowed to reference the two lifecycle entry callables.

``lifespan`` is ``_serving_lifespan``'s inner startup body: it awaits the prime once and creates
the steady-state owner's task once. A reference from anywhere else -- a route scheduling the loop
is the shape that matters -- is a second start of observation progression.
"""

GETATTR_BUILTIN = "getattr"
"""The literal-name indirection resolved wherever a member name is matched."""

WIRE_SUBMISSION_API = "submit_control_prompt"
"""The harness control primitive that actually reaches a seat's session."""

WIRE_SUBMISSION_AUTHORITIES = frozenset(
    {
        # The relay's own delivery leg: a durable inbox row reaches a seat here and nowhere else.
        "serving/inbox_delivery.py",
        # The primitive's definition and its in-package delegating caller.
        "serving/harness_control_client.py",
        # Explicit operator/developer controls (R14 preservation boundary): a submitted prompt
        # is the operator asking for one, not a relay-authored completion.
        "serving/harness_control_api.py",
        "serving/_app_terminal_routes.py",
    }
)

MUTATION_REMEDIATION = (
    "drive observation through agents_remember.serving._app_lifespan's shared observer body "
    "(_observe_terminal_catalog), which the startup prime and the recurring steady-state owner "
    "already enter; do not add a third caller, and do not reach the sweeper from a route"
)
READ_REMEDIATION = (
    "the sweeper field is read only inside the two declared mutation owners; reaching it from a "
    "new scope (including handing it to a helper) is a new route to the mutation path"
)
CONSTRUCTION_REMEDIATION = (
    "use the one TerminalCatalogLivenessSweeper built in serving/app.py for the serving runtime; "
    "a second instance is a second observation authority"
)
ENTRY_CALLER_REMEDIATION = (
    "the shared observer body has exactly two callers: the startup prime "
    "(_prime_terminal_observation) and the recurring steady-state owner "
    "(_terminal_observation_loop); route any new progression through those"
)
OWNER_REFERENCE_REMEDIATION = (
    "the two lifecycle entry callables are referenced only from the serving startup scope, which "
    "awaits the prime and creates the steady-state task; do not start or re-enter either from a "
    "route, an observer, a notifier, or any new module"
)
WIRE_REMEDIATION = (
    "deliver through a durable inbox row and agents_remember.serving.inbox_delivery."
    "deliver_inbox_entry; do not push a prompt from an observer, notifier, or new module"
)

_ROUTE_METHODS = frozenset(
    {"get", "post", "put", "patch", "delete", "websocket", "head", "options"}
)
"""FastAPI registration decorators that turn a function into a request entry point."""


@dataclass(frozen=True)
class RelaySite:
    """One place the relay is reached, with the scope that reaches it."""

    module: str
    scope: str
    line: int
    form: str
    detail: str

    @property
    def owner(self) -> tuple[str, str]:
        """The (module, enclosing scope) pair an authority set is expressed in."""
        return (self.module, self.scope)

    def __str__(self) -> str:
        return f"{self.module}:{self.line}  in {self.scope}  [{self.form}] {self.detail}"


@dataclass(frozen=True)
class RelayCensus:
    """Everything one pass over the package measured about the relay."""

    modules_scanned: int
    sweeper_constructions: tuple[RelaySite, ...]
    sweeper_reads: tuple[RelaySite, ...]
    sweeper_mutations: tuple[RelaySite, ...]
    observation_entry_calls: tuple[RelaySite, ...]
    observation_owner_references: tuple[RelaySite, ...]
    route_modules: tuple[str, ...]
    route_handler_count: int
    wire_submissions: tuple[RelaySite, ...]

    @property
    def mutation_owners(self) -> frozenset[tuple[str, str]]:
        return frozenset(site.owner for site in self.sweeper_mutations)

    @property
    def read_owners(self) -> frozenset[tuple[str, str]]:
        return frozenset(site.owner for site in self.sweeper_reads)

    @property
    def construction_modules(self) -> frozenset[str]:
        return frozenset(site.module for site in self.sweeper_constructions)

    @property
    def entry_caller_scopes(self) -> frozenset[str]:
        return frozenset(site.scope for site in self.observation_entry_calls)

    @property
    def observation_owner_owners(self) -> frozenset[tuple[str, str]]:
        return frozenset(site.owner for site in self.observation_owner_references)

    @property
    def unreviewed_wire_modules(self) -> tuple[RelaySite, ...]:
        return tuple(
            site for site in self.wire_submissions if site.module not in WIRE_SUBMISSION_AUTHORITIES
        )


def render_sites(sites: Sequence[RelaySite], *, headline: str, remediation: str) -> str:
    """The whole site list with the fix named -- never just the first offender."""
    if not sites:
        return ""
    body = "\n".join(f"  {site}" for site in sites)
    return f"{headline} ({len(sites)} found)\n{body}\nremediation: {remediation}"


def render_modules(modules: Sequence[str], *, headline: str, remediation: str) -> str:
    """The same rendering for a module-level finding, which has no line or scope."""
    if not modules:
        return ""
    body = "\n".join(f"  {module}" for module in modules)
    return f"{headline} ({len(modules)} found)\n{body}\nremediation: {remediation}"


def package_modules(package_root: Path) -> list[Path]:
    """Every module the census applies to, in a stable order."""
    return [path for path in sorted(package_root.rglob("*.py")) if "package_data" not in path.parts]


# --- per-module analysis -------------------------------------------------------------------------


def _scoped_nodes(tree: ast.AST) -> Iterator[tuple[str, ast.AST]]:
    """Every node with the name of the function or class containing it."""

    def descend(scope: str, node: ast.AST) -> Iterator[tuple[str, ast.AST]]:
        yield scope, node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from descend(child.name, child)
            else:
                yield from descend(scope, child)

    yield from descend("<module>", tree)


def _import_bindings(tree: ast.AST, wanted: frozenset[str]) -> dict[str, str]:
    """Bare names this module bound via ``from ... import <wanted>``, mapped to the canonical name.

    Binding the name is the whole bypass: ``from x import submit_control_prompt as push``
    leaves nothing named ``submit_control_prompt`` at the call site.
    """
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in wanted:
                bindings[alias.asname or alias.name] = alias.name
    return bindings


def _import_aliases(tree: ast.AST, wanted: frozenset[str]) -> frozenset[str]:
    """The bare names :func:`_import_bindings` bound, for callers that only need membership."""
    return frozenset(_import_bindings(tree, wanted))


def _literal_getattr_name(node: ast.AST) -> str | None:
    """The literal attribute name of ``getattr(obj, "name"[, default])``, or ``None``.

    ``getattr`` with a *literal* second argument names the same member as the dotted form, so a
    guard that only reads ``ast.Attribute`` lets ``getattr(runtime, "liveness_sweeper")`` walk
    past it. A name computed at runtime is a different matter and stays a declared blind spot.
    """
    if not isinstance(node, ast.Call):
        return None
    function = node.func
    named = (isinstance(function, ast.Name) and function.id == GETATTR_BUILTIN) or (
        isinstance(function, ast.Attribute) and function.attr == GETATTR_BUILTIN
    )
    if not named or len(node.args) < 2:
        return None
    literal = node.args[1]
    if isinstance(literal, ast.Constant) and isinstance(literal.value, str):
        return literal.value
    return None


def _reached_name(node: ast.expr, wanted: frozenset[str], aliases: Mapping[str, str]) -> str | None:
    """Which wanted callable an expression reaches, in every form this package writes.

    Covered forms: ``name``, ``alias`` (a bound ``from ... import`` name), ``module.name``,
    ``getattr(module, "name")`` and ``getattr(module, "name", default)``. NOT covered: a name
    computed at runtime, a callable carried in a container or attribute of a foreign object, and
    a callable handed across a function boundary and renamed there.
    """
    if isinstance(node, ast.Name):
        if node.id in wanted:
            return node.id
        return aliases.get(node.id)
    if isinstance(node, ast.Attribute) and node.attr in wanted:
        return node.attr
    literal = _literal_getattr_name(node)
    if literal is not None and literal in wanted:
        return literal
    return None


def _assignment_pairs(tree: ast.AST) -> list[tuple[list[ast.expr], ast.expr]]:
    pairs: list[tuple[list[ast.expr], ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            pairs.append((list(node.targets), node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            pairs.append(([node.target], node.value))
    return pairs


def _is_sweeper_receiver(expression: ast.expr, bound: frozenset[str]) -> bool:
    """Whether an expression carries the serving runtime's sweeper.

    An attribute chain is followed to its root, so ``runtime.liveness_sweeper`` and
    ``self.liveness_sweeper`` both match; a ``getattr(obj, "liveness_sweeper"[, default])`` call
    matches on its literal name; and a bare name matches when it was bound from any of those.
    Names are collected module-wide rather than per function: over-approximating here can only
    ever *report* more, and a name meaning "the sweeper" in one scope and something else in
    another is not a shape this package has.
    """
    while isinstance(expression, ast.Attribute):
        if expression.attr == SWEEPER_RECEIVER_FIELD:
            return True
        expression = expression.value
    if _literal_getattr_name(expression) == SWEEPER_RECEIVER_FIELD:
        return True
    return isinstance(expression, ast.Name) and expression.id in bound


def bound_sweeper_names(tree: ast.AST) -> frozenset[str]:
    """Names bound, directly or through another binding, from the sweeper field."""
    bound: set[str] = set()
    pending = _assignment_pairs(tree)
    while pending:
        remaining: list[tuple[list[ast.expr], ast.expr]] = []
        progressed = False
        for targets, value in pending:
            if not _is_sweeper_receiver(value, frozenset(bound)):
                remaining.append((targets, value))
                continue
            progressed = True
            for target in targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
        if not progressed:
            break
        pending = remaining
    return frozenset(bound)


def _sweeper_constructor_names(tree: ast.AST) -> frozenset[str]:
    return _import_aliases(tree, frozenset({SWEEPER_TYPE})) | {SWEEPER_TYPE}


def _is_sweeper_mutation(node: ast.expr, bound: frozenset[str]) -> bool:
    """Whether one node reaches the sweeper's mutation API, in either written form."""
    if (
        isinstance(node, ast.Attribute)
        and node.attr == SWEEPER_MUTATION_API
        and _is_sweeper_receiver(node.value, bound)
    ):
        return True
    return (
        _literal_getattr_name(node) == SWEEPER_MUTATION_API
        and isinstance(node, ast.Call)
        and bool(node.args)
        and _is_sweeper_receiver(node.args[0], bound)
    )


def module_sweeper_mutations(tree: ast.AST, module: str) -> list[RelaySite]:
    """Every reach for the sweeper's mutation API in one parsed module, ordered by line.

    Both written forms count: ``<sweeper>.refresh`` and ``getattr(<sweeper>, "refresh")()``.
    """
    bound = bound_sweeper_names(tree)
    sites: list[RelaySite] = []
    for scope, node in _scoped_nodes(tree):
        if not isinstance(node, ast.expr) or not _is_sweeper_mutation(node, bound):
            continue
        sites.append(
            RelaySite(
                module=module,
                scope=scope,
                line=node.lineno,
                form="sweeper mutation",
                detail=f"reaches {SWEEPER_MUTATION_API} through the {SWEEPER_RECEIVER_FIELD} carrier",
            )
        )
    return sorted(sites, key=lambda site: site.line)


def module_sweeper_reads(tree: ast.AST, module: str) -> list[RelaySite]:
    """Every read of the sweeper carrier field in one parsed module, ordered by line.

    This is the wider net under the mutation rule: a scope that reads the field and hands it to
    a helper never names ``refresh`` itself, so only the hand-over is visible. Reading the field
    anywhere outside the two declared owners is what that hand-over looks like. Both written
    forms count: ``<obj>.liveness_sweeper`` and ``getattr(<obj>, "liveness_sweeper"[, default])``.
    """
    sites: list[RelaySite] = []
    for scope, node in _scoped_nodes(tree):
        if not isinstance(node, ast.expr):
            continue
        read = (isinstance(node, ast.Attribute) and node.attr == SWEEPER_RECEIVER_FIELD) or (
            _literal_getattr_name(node) == SWEEPER_RECEIVER_FIELD
        )
        if not read:
            continue
        sites.append(
            RelaySite(
                module=module,
                scope=scope,
                line=node.lineno,
                form="sweeper read",
                detail=f"reads the {SWEEPER_RECEIVER_FIELD} carrier",
            )
        )
    return sorted(sites, key=lambda site: site.line)


def module_sweeper_constructions(tree: ast.AST, module: str) -> list[RelaySite]:
    """Every construction of the sweeper type in one parsed module, ordered by line."""
    names = _sweeper_constructor_names(tree)
    sites = [
        RelaySite(
            module=module,
            scope=scope,
            line=node.lineno,
            form="sweeper construction",
            detail=f"builds a {SWEEPER_TYPE}",
        )
        for scope, node in _scoped_nodes(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in names
    ]
    return sorted(sites, key=lambda site: site.line)


def module_observation_entry_calls(
    tree: ast.AST, module: str, entry: str = OBSERVATION_BODY
) -> list[RelaySite]:
    """Every reference to the shared observation body in one parsed module, ordered by line.

    A *reference*, not only a call: reaching the shared body at all -- to call it, to schedule it,
    or to hand it on -- is entering the observation path, and the packet's non-conforming case is
    a route that does exactly that. Every written form counts: ``body(...)``, an aliased
    ``from ... import body as other``, ``module.body(...)``, ``getattr(module, "body")`` and
    ``getattr(module, "body", default)``.
    """
    aliases = _import_bindings(tree, frozenset({entry}))
    sites: list[RelaySite] = []
    for scope, node in _scoped_nodes(tree):
        if not isinstance(node, ast.expr):
            continue
        reached = _reached_name(node, frozenset({entry}), aliases)
        if reached is None:
            continue
        sites.append(
            RelaySite(
                module=module,
                scope=scope,
                line=node.lineno,
                form="observation entry",
                detail=f"reaches the shared observer body {reached}",
            )
        )
    return sorted(sites, key=lambda site: site.line)


def module_observation_owner_references(tree: ast.AST, module: str) -> list[RelaySite]:
    """Every reference to either lifecycle entry callable in one parsed module, by line.

    The body census above answers "who enters the shared pass"; this one answers "who *starts*
    observation at all". A route that never names the body or the sweeper can still schedule the
    steady-state owner -- ``asyncio.create_task(_terminal_observation_loop(runtime))`` is this
    package's own idiom for starting it -- and that restores the dashboard as the clock just as
    surely. Both callables are counted, in call and bare-reference form, with the same form set
    as the body census.
    """
    aliases = _import_bindings(tree, OBSERVATION_ENTRY_CALLERS)
    sites: list[RelaySite] = []
    for scope, node in _scoped_nodes(tree):
        if not isinstance(node, ast.expr):
            continue
        reached = _reached_name(node, OBSERVATION_ENTRY_CALLERS, aliases)
        if reached is None:
            continue
        sites.append(
            RelaySite(
                module=module,
                scope=scope,
                line=node.lineno,
                form="observation owner reference",
                detail=f"references the lifecycle entry callable {reached}",
            )
        )
    return sorted(sites, key=lambda site: site.line)


def module_wire_submissions(tree: ast.AST, module: str) -> list[RelaySite]:
    """Every reach for the harness submission primitive in one parsed module.

    Same form set as the observation censuses: bare name, bound alias, attribute, and literal
    ``getattr``.
    """
    aliases = _import_bindings(tree, frozenset({WIRE_SUBMISSION_API}))
    wanted = frozenset({WIRE_SUBMISSION_API})
    sites: list[RelaySite] = []
    for scope, node in _scoped_nodes(tree):
        if not isinstance(node, ast.Call):
            continue
        reached = _reached_name(node.func, wanted, aliases)
        if reached is None:
            continue
        sites.append(
            RelaySite(
                module=module,
                scope=scope,
                line=node.lineno,
                form="wire submission",
                detail=f"pushes through {reached}",
            )
        )
    return sorted(sites, key=lambda site: site.line)


def module_route_handlers(tree: ast.AST) -> list[str]:
    """Names of the functions one module registers as request entry points."""
    handlers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr in _ROUTE_METHODS
            ):
                handlers.append(node.name)
                break
    return handlers


def measure_relay(package_root: Path) -> RelayCensus:
    """One pass over the package: every measurement this guard asserts on."""
    constructions: list[RelaySite] = []
    reads: list[RelaySite] = []
    mutations: list[RelaySite] = []
    entry_calls: list[RelaySite] = []
    owner_references: list[RelaySite] = []
    wire: list[RelaySite] = []
    route_modules: list[str] = []
    route_handlers = 0
    paths = package_modules(package_root)
    for path in paths:
        module = path.relative_to(package_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        constructions.extend(module_sweeper_constructions(tree, module))
        reads.extend(module_sweeper_reads(tree, module))
        mutations.extend(module_sweeper_mutations(tree, module))
        entry_calls.extend(module_observation_entry_calls(tree, module))
        owner_references.extend(module_observation_owner_references(tree, module))
        wire.extend(module_wire_submissions(tree, module))
        handlers = module_route_handlers(tree)
        if handlers:
            route_modules.append(module)
            route_handlers += len(handlers)
    return RelayCensus(
        modules_scanned=len(paths),
        sweeper_constructions=tuple(constructions),
        sweeper_reads=tuple(reads),
        sweeper_mutations=tuple(mutations),
        observation_entry_calls=tuple(entry_calls),
        observation_owner_references=tuple(owner_references),
        route_modules=tuple(route_modules),
        route_handler_count=route_handlers,
        wire_submissions=tuple(wire),
    )


__all__ = [
    "CONSTRUCTION_REMEDIATION",
    "ENTRY_CALLER_REMEDIATION",
    "MUTATION_REMEDIATION",
    "OBSERVATION_BODY",
    "OBSERVATION_ENTRY_CALLERS",
    "OBSERVATION_OWNER_AUTHORITIES",
    "OWNER_REFERENCE_REMEDIATION",
    "READ_REMEDIATION",
    "SWEEPER_CONSTRUCTION_OWNER",
    "SWEEPER_MUTATION_API",
    "SWEEPER_MUTATION_AUTHORITIES",
    "SWEEPER_TYPE",
    "SWEEPER_TYPE_OWNER",
    "TERMINAL_OBSERVATION_OWNER",
    "WIRE_REMEDIATION",
    "WIRE_SUBMISSION_API",
    "WIRE_SUBMISSION_AUTHORITIES",
    "RelayCensus",
    "RelaySite",
    "bound_sweeper_names",
    "measure_relay",
    "module_observation_entry_calls",
    "module_observation_owner_references",
    "module_route_handlers",
    "module_sweeper_constructions",
    "module_sweeper_mutations",
    "module_sweeper_reads",
    "module_wire_submissions",
    "package_modules",
    "render_modules",
    "render_sites",
]
