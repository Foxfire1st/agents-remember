"""One canonical completion relay: the architecture guard for ``LOCR-R14@v1``.

The relay ``adapter evidence -> catalog -> notifier -> durable inbox -> owner`` may not grow a
second reader or a second delivery path. The shapes of drift below would each be invisible to the
behavioural suites, because each one *works*: a route refreshing the sweeper (GET becomes the
clock again), a route entering the shared observer pass or scheduling the steady-state owner, a
second sweeper instance, and a notifier module pushing a prompt straight at a seat instead of
persisting a durable row. This module makes all of them fail loudly.

**A name, not a spelling.** Every observation rule resolves the *name* it forbids in each form
this package writes it -- bare, bound alias, module-attribute, and literal ``getattr``. The first
delivered version matched only the bare call form; a reviewer defeated it twice with idiomatic
code (``_app_lifespan._observe_terminal_catalog(...)`` and
``asyncio.create_task(_terminal_observation_loop(runtime))``) that left every case green. Both
forms are now cases in ``ObservationPathPrimitiveTests`` and package pins in ``PackageRelayTests``.

WHAT DEFENDS WHAT
-----------------
The two halves below are deliberately different in kind, so satisfying one cannot fake the other.

``SweeperCensusPrimitiveTests`` / ``ObservationPathPrimitiveTests`` / ``WireSubmissionPrimitiveTests``
    Detector tests. Each plants one violation in a synthetic module and requires it reported,
    and plants one neighbouring shape that must stay clean. These are what make the package
    assertions below falsifiable *from inside the suite*: a reviewer can see the detector fire
    without having to run an external mutation campaign.
``PackageRelayTests``
    The measurement. `functools.cache`d one pass over ``mcp/src/agents_remember``, asserting the
    observed authority sets EQUAL the declared ones. Every one of them is preceded by a floor
    (``test_the_census_scans_the_whole_installed_package``, the route-handler count), because an
    empty collection compared against an expected collection passes in every state.

WHAT THIS GUARD DOES NOT COVER
------------------------------
The support module's docstring carries the full list. In short: a member name COMPUTED at runtime
(``getattr(runtime, field_name)`` -- the literal form *is* covered), a callable carried in a
container, a callable renamed across a function boundary, a brand-new observation class with a
differently named method, and a second delivery mechanism built on a primitive other than the
harness submission call. It also measures source shape only: that the declared entry points are
*scheduled correctly* is behavioural and belongs to the observation-loop and startup-prime suites,
though that they are scheduled *only from the serving startup scope* is measured here.

Doctrine is deliberately NOT this module's scope. R14's clauses 4-5 are absence claims evidenced
by the range diff; the campaign's executable doctrine guard over ``skills/l-01-agent-lifecycles``
is ``mcp/tests/test_lifecycle_turn_truth_doctrine.py`` (owned by the doctrine-reconciliation
requirement, already pinning R14 clause 5 via its ``second-completion-message`` claim), and a
second one here would be the duplicate mechanism R14 forbids.
"""

from __future__ import annotations

import ast
import functools
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember_test_support.code_quality import completion_relay as relay

PACKAGE_ROOT = MCP_SRC / "agents_remember"

# Floors that keep the package assertions below from passing on an empty measurement.
MINIMUM_MODULES_SCANNED = 900
MINIMUM_ROUTE_HANDLERS = 40
REQUIRED_ROUTE_MODULES = frozenset({"serving/_app_routes.py", "serving/_app_terminal_routes.py"})


@functools.cache
def census() -> relay.RelayCensus:
    return relay.measure_relay(PACKAGE_ROOT)


def _parsed(source: str) -> ast.Module:
    return ast.parse(source)


def _mutations(source: str, module: str = "fixture.py") -> list[str]:
    return [str(site) for site in relay.module_sweeper_mutations(_parsed(source), module)]


def _constructions(source: str, module: str = "fixture.py") -> list[str]:
    return [str(site) for site in relay.module_sweeper_constructions(_parsed(source), module)]


def _wire(source: str, module: str = "fixture.py") -> list[str]:
    return [str(site) for site in relay.module_wire_submissions(_parsed(source), module)]


def _entries(source: str, module: str = "serving/_app_terminal_routes.py") -> list[str]:
    return [str(site) for site in relay.module_observation_entry_calls(_parsed(source), module)]


def _owner_refs(source: str, module: str = "serving/_app_terminal_routes.py") -> list[str]:
    return [
        str(site) for site in relay.module_observation_owner_references(_parsed(source), module)
    ]


class SweeperCensusPrimitiveTests(unittest.TestCase):
    """The detector fires on the exact drift R14 forbids, and stays quiet on its neighbours."""

    def test_a_route_handler_that_refreshes_the_sweeper_is_reported(self) -> None:
        # The non-conforming case R14 names outright: GET keeps a fallback refresh.
        source = (
            "from fastapi import FastAPI\n"
            "def register(app, runtime):\n"
            "    @app.get('/api/terminal/sessions')\n"
            "    def api_terminal_sessions():\n"
            "        if runtime.observer_appears_stale():\n"
            "            runtime.liveness_sweeper.refresh()\n"
            "        return {'sessions': []}\n"
        )
        self.assertEqual(
            _mutations(source, "serving/_app_terminal_routes.py"),
            [
                "serving/_app_terminal_routes.py:6  in api_terminal_sessions  "
                "[sweeper mutation] reaches refresh through the liveness_sweeper carrier"
            ],
        )
        self.assertEqual(relay.module_route_handlers(_parsed(source)), ["api_terminal_sessions"])

    def test_a_local_alias_of_the_sweeper_is_followed_to_its_refresh(self) -> None:
        # Rebinding the field to a local name is the obvious way to launder the receiver.
        aliased = (
            "def loop(runtime):\n    sweeper = runtime.liveness_sweeper\n    sweeper.refresh()\n"
        )
        chained = "def loop(self):\n    helper = self\n    helper.liveness_sweeper.refresh()\n"
        self.assertEqual(len(_mutations(aliased)), 1)
        self.assertEqual(len(_mutations(chained)), 1)
        self.assertEqual(relay.bound_sweeper_names(_parsed(aliased)), frozenset({"sweeper"}))

    def test_an_unrelated_refresh_on_another_receiver_is_not_reported(self) -> None:
        # The package really does call ``.refresh`` on other objects; the rule is receiver-shaped
        # and must not turn every one of them into a finding.
        source = (
            "def rebuild(self, thread_id):\n"
            "    return self._child_history.refresh(thread_id)\n"
            "def cache(self):\n"
            "    self._cache.refresh()\n"
        )
        self.assertEqual(_mutations(source), [])

    def test_a_literal_getattr_receiver_is_followed_to_its_refresh(self) -> None:
        # M6's literal half: the member name is written as a string, so only a literal
        # ``getattr`` reader sees it. The computed-name form stays a declared blind spot.
        receiver = "def loop(runtime):\n    getattr(runtime, 'liveness_sweeper').refresh()\n"
        member = "def loop(runtime):\n    getattr(runtime.liveness_sweeper, 'refresh')()\n"
        with_default = (
            "def loop(runtime):\n    getattr(runtime, 'liveness_sweeper', None).refresh()\n"
        )
        self.assertEqual(len(_mutations(receiver)), 1)
        self.assertEqual(len(_mutations(member)), 1)
        self.assertEqual(len(_mutations(with_default)), 1)
        self.assertEqual(len(relay.module_sweeper_reads(_parsed(receiver), "fixture.py")), 1)

    def test_a_second_sweeper_construction_is_reported(self) -> None:
        source = (
            "from agents_remember.serving.terminal_liveness import (\n"
            "    TerminalCatalogLivenessSweeper as Sweeper,\n"
            ")\n"
            "def build(second_source):\n"
            "    return Sweeper(config=second_source)\n"
        )
        self.assertEqual(len(_constructions(source)), 1)


class ObservationPathPrimitiveTests(unittest.TestCase):
    """Every written form of "a route enters or starts observation" is reported."""

    def test_a_module_attribute_call_into_the_shared_body_is_reported(self) -> None:
        # F1: the bare-name census could not see this, and the guard's own remediation text
        # names the module-qualified form.
        source = (
            "from fastapi import FastAPI\n"
            "from agents_remember.serving import _app_lifespan\n"
            "def register(app, runtime):\n"
            "    @app.get('/api/terminal/sessions')\n"
            "    def api_terminal_sessions():\n"
            "        _app_lifespan._observe_terminal_catalog(runtime, 'route-fallback')\n"
        )
        self.assertEqual(len(_entries(source)), 1)
        self.assertEqual(len(relay.module_route_handlers(_parsed(source))), 1)

    def test_a_literal_getattr_call_into_the_shared_body_is_reported(self) -> None:
        source = (
            "def api_terminal_sessions(runtime):\n"
            "    getattr(_app_lifespan, '_observe_terminal_catalog')(runtime, 'x')\n"
        )
        self.assertEqual(len(_entries(source)), 1)

    def test_an_aliased_import_of_the_shared_body_is_still_reported(self) -> None:
        # The R-ALIAS control: alias handling must not regress while the form set widens.
        source = (
            "from agents_remember.serving._app_lifespan import (\n"
            "    _observe_terminal_catalog as body,\n"
            ")\n"
            "def api_terminal_sessions(runtime):\n"
            "    body(runtime, 'x')\n"
        )
        self.assertEqual(
            _entries(source),
            [
                "serving/_app_terminal_routes.py:5  in api_terminal_sessions  "
                "[observation entry] reaches the shared observer body _observe_terminal_catalog"
            ],
        )

    def test_a_reference_to_an_owner_callable_outside_startup_is_reported(self) -> None:
        # F2: a route scheduling the steady-state owner names neither the body nor the sweeper.
        scheduled = (
            "from agents_remember.serving._app_lifespan import (\n"
            "    _terminal_observation_loop,\n"
            ")\n"
            "def api_terminal_sessions(runtime):\n"
            "    asyncio.create_task(_terminal_observation_loop(runtime))\n"
        )
        qualified = (
            "def api_terminal_sessions(runtime):\n"
            "    return _app_lifespan._prime_terminal_observation(runtime)\n"
        )
        self.assertEqual(len(_owner_refs(scheduled)), 1)
        self.assertEqual(len(_owner_refs(qualified)), 1)
        self.assertEqual(_entries(scheduled), [])


class WireSubmissionPrimitiveTests(unittest.TestCase):
    """The delivery leg is closed on the harness submission primitive, aliases included."""

    def test_an_import_alias_of_the_wire_primitive_is_followed(self) -> None:
        source = (
            "from agents_remember.serving.harness_control_client import (\n"
            "    submit_control_prompt as push,\n"
            ")\n"
            "def notify(target, text):\n"
            "    push(target, text)\n"
        )
        self.assertEqual(len(_wire(source)), 1)

    def test_a_second_package_module_pushing_the_wire_is_unreviewed(self) -> None:
        # A notifier module pushing a completion straight at a seat, with no durable row. This
        # drives the whole census, so it also proves ``measure_relay`` walks a real tree.
        with tempfile.TemporaryDirectory() as root:
            package = Path(root) / "agents_remember"
            (package / "serving").mkdir(parents=True)
            (package / "serving" / "owner_signals.py").write_text(
                "def emit_completion(target, text):\n"
                "    return submit_control_prompt(target, text)\n",
                encoding="utf-8",
            )
            measured = relay.measure_relay(package)
        self.assertEqual(measured.modules_scanned, 1)
        self.assertEqual(
            [site.module for site in measured.unreviewed_wire_modules],
            ["serving/owner_signals.py"],
        )


class PackageRelayTests(unittest.TestCase):
    """The measured candidate: the relay has exactly the authorities R14 declares."""

    def test_the_census_scans_the_whole_installed_package(self) -> None:
        scanned = census().modules_scanned
        self.assertGreaterEqual(
            scanned,
            MINIMUM_MODULES_SCANNED,
            f"census scanned only {scanned} modules under {PACKAGE_ROOT}",
        )

    def test_the_liveness_sweeper_is_constructed_once_by_its_owner_module(self) -> None:
        measured = census()
        self.assertEqual(
            measured.construction_modules,
            frozenset({relay.SWEEPER_CONSTRUCTION_OWNER}),
            relay.render_sites(
                measured.sweeper_constructions,
                headline="terminal liveness sweeper constructions",
                remediation=relay.CONSTRUCTION_REMEDIATION,
            ),
        )
        self.assertEqual(len(measured.sweeper_constructions), 1)

    def test_the_observation_mutation_path_has_exactly_the_declared_owners(self) -> None:
        measured = census()
        self.assertEqual(
            measured.mutation_owners,
            relay.SWEEPER_MUTATION_AUTHORITIES,
            relay.render_sites(
                measured.sweeper_mutations,
                headline="sweeper mutation sites",
                remediation=relay.MUTATION_REMEDIATION,
            ),
        )

    def test_only_the_declared_observation_entry_callers_enter_the_shared_body(self) -> None:
        measured = census()
        self.assertEqual(
            measured.entry_caller_scopes,
            relay.OBSERVATION_ENTRY_CALLERS,
            relay.render_sites(
                measured.observation_entry_calls,
                headline="references to the shared observer body",
                remediation=relay.ENTRY_CALLER_REMEDIATION,
            ),
        )
        # Exact-count pin: the two entry points each reference the body once. A second reference
        # inside an already-declared scope would otherwise pass the owner-set equality.
        self.assertEqual(
            len(measured.observation_entry_calls),
            2,
            relay.render_sites(
                measured.observation_entry_calls,
                headline="references to the shared observer body",
                remediation=relay.ENTRY_CALLER_REMEDIATION,
            ),
        )

    def test_the_observation_owners_are_referenced_only_from_the_startup_scope(self) -> None:
        measured = census()
        # F2: a route that schedules the steady-state owner names neither the body nor the sweeper.
        self.assertEqual(
            measured.observation_owner_owners,
            relay.OBSERVATION_OWNER_AUTHORITIES,
            relay.render_sites(
                measured.observation_owner_references,
                headline="references to a lifecycle observation entry callable",
                remediation=relay.OWNER_REFERENCE_REMEDIATION,
            ),
        )
        self.assertEqual(
            len(measured.observation_owner_references),
            2,
            relay.render_sites(
                measured.observation_owner_references,
                headline="references to a lifecycle observation entry callable",
                remediation=relay.OWNER_REFERENCE_REMEDIATION,
            ),
        )

    def test_the_sweeper_field_is_read_only_by_the_declared_mutation_owners(self) -> None:
        measured = census()
        # A helper hand-over names the field, never ``refresh``: this is the wider net under the
        # mutation rule, and the count is asserted so a second read in an owner scope still fails.
        self.assertEqual(
            measured.read_owners,
            relay.SWEEPER_MUTATION_AUTHORITIES,
            relay.render_sites(
                measured.sweeper_reads,
                headline="reads of the sweeper carrier field",
                remediation=relay.READ_REMEDIATION,
            ),
        )
        self.assertEqual(
            len(measured.sweeper_reads),
            2,
            relay.render_sites(
                measured.sweeper_reads,
                headline="reads of the sweeper carrier field",
                remediation=relay.READ_REMEDIATION,
            ),
        )

    def test_route_handlers_exist_and_none_registers_observation_progress(self) -> None:
        measured = census()
        # Non-vacuity floor: an empty route set would make the intersection below meaningless.
        self.assertGreaterEqual(measured.route_handler_count, MINIMUM_ROUTE_HANDLERS)
        self.assertLessEqual(REQUIRED_ROUTE_MODULES, set(measured.route_modules))
        advancing_modules = {site.module for site in measured.sweeper_mutations} | {
            site.module
            for site in (*measured.observation_entry_calls, *measured.observation_owner_references)
        }
        in_route_modules = sorted(advancing_modules & set(measured.route_modules))
        self.assertEqual(
            in_route_modules,
            [],
            relay.render_modules(
                in_route_modules,
                headline="route modules that advance terminal observation",
                remediation=relay.MUTATION_REMEDIATION,
            ),
        )

    def test_only_the_reviewed_modules_reach_the_harness_wire(self) -> None:
        measured = census()
        unreviewed = measured.unreviewed_wire_modules
        self.assertEqual(
            unreviewed,
            (),
            relay.render_sites(
                unreviewed,
                headline="unreviewed reaches for the harness submission primitive",
                remediation=relay.WIRE_REMEDIATION,
            ),
        )
        # The census must actually have found the reviewed reaches, or the equality above is empty.
        self.assertGreaterEqual(len(measured.wire_submissions), 4)
