"""Regression boundaries for disposable eval publication and its scratch CLI child.

Single caller: test_agent_eval_trial_setup. Host operations are replaced in a
fresh Python child; product CLI parsing, configuration loading and leaf
publication remain real. No actor or native host is started.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.kernel.primitives import checkout_coordination

_CHILD_CONFIG_PROBE = r"""
import json
import sys
from pathlib import Path
sys.path[:0] = json.loads(sys.argv.pop(1))
expected_guard = Path(sys.argv.pop(1))
from agents_remember.kernel.primitives import checkout_coordination as guard
assert Path(guard.__file__).resolve() == expected_guard
from agents_remember.kernel.primitives.runtime_config import load_config
from agents_remember.cli import paseo_runtime

kind, checkout, bootstrap, *arguments = sys.argv[1:]
expected = Path(arguments[-1]).resolve()
document = json.loads(expected.read_text())
guard._declared.clear()
assert guard.declared_execution_mode() is None
guard.resolve_checkout_location = lambda: guard.CheckoutLocation(Path(checkout), kind)

def inspect_settings(settings):
    config = load_config(expected)
    assert config.config_path.resolve() == expected, (config.config_path, expected)
    assert guard.declared_execution_mode() == "test"
    assert config.coordination_root == Path(document["coordinationRoot"])
    assert settings.command_config_path.resolve() == expected
    assert settings.home == Path(document["paseoRuntime"]["home"])
    return {
        "ok": True,
        "mode": guard.declared_execution_mode(),
        "config": str(config.config_path),
        "coordinationRoot": str(config.coordination_root),
        "home": str(settings.home),
    }

# Only the selected host operation is substituted. The CLI entrypoint, parser,
# settings loader and the exact production child bootstrap execute unchanged.
paseo_runtime._COMMANDS[arguments[1]] = ("test host boundary", inspect_settings)
sys.argv = ["agents-remember", *arguments]
exec(compile(bootstrap, "<captured-eval-scratch-cli>", "exec"), {"__name__": "__main__"})
"""


def assert_native_leaf_checkout_provisioning(case: Any, profile: Any) -> None:
    """Actual enclosure publication must not depend on pytest's global test mode."""
    case.native_leaf_case()
    for kind in ("primary", "linked"):
        location = checkout_coordination.CheckoutLocation(case.candidate, kind)
        with (
            case.subTest(checkout=kind),
            patch.dict(checkout_coordination._declared, {}, clear=True),
            patch.object(checkout_coordination, "resolve_checkout_location", return_value=location),
            patch(
                "agent_evals.trial_setup.BatchHost.start",
                side_effect=AssertionError("host start is forbidden"),
            ),
        ):
            case.assertIsNone(checkout_coordination.declared_execution_mode())
            trial = case.provision(profile=profile)
            binding = trial.native_setup["leaf"]
            case.assertEqual(checkout_coordination.declared_execution_mode(), "test")
            case.assertTrue(Path(binding["contractPath"]).is_file())
            case.assertTrue(Path(binding["publication"]["locatorPath"]).is_file())
            case.assertTrue(Path(binding["contractPath"]).is_relative_to(trial.project_root))
            case.assertFalse((trial.code_root / "retired.py").exists())
            case.assertFalse((trial.memory_root / "onboarding/retired.py.md").exists())
            case.assertFalse(location.coordination_root.exists())


def assert_scratch_cli_config_binding(case: Any, profile: Any) -> None:
    """Both checkout kinds load the named trial authority in a genuinely fresh child."""
    trial = case.provision(profile=profile)
    owner = trial.batch_host
    # Keep the original subprocess primitive before the runner boundary is patched.
    run = subprocess.run
    for kind in ("primary", "linked"):
        captured = []

        def inspect_child(argv, *, captured=captured, kind=kind, **kwargs):
            captured.append(list(argv))
            case.assertEqual(argv[:3], [str(case.candidate / "mcp/.venv/bin/python"), "-P", "-c"])
            case.assertEqual(argv[4:], ["paseo", "provision", "--config", str(owner.settings_path)])
            case.assertEqual(kwargs["cwd"], owner.sandbox_home)
            case.assertEqual(kwargs["env"], owner.environment_values)
            # The captured bootstrap is executed verbatim. A separate child harness
            # supplies only the checkout shape and a no-host operation substitute.
            done = run(
                [
                    *argv[:2],
                    "-c",
                    _CHILD_CONFIG_PROBE,
                    json.dumps(sys.path),
                    str(Path(checkout_coordination.__file__).resolve()),
                    kind,
                    str(case.candidate),
                    *argv[3:],
                ],
                **kwargs,
            )
            case.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            return done

        with (
            case.subTest(checkout=kind),
            patch("agent_evals.trial_setup.subprocess.run", side_effect=inspect_child),
        ):
            result = owner._command("provision", 30)
            case.assertEqual(len(captured), 1)
            case.assertEqual(result["mode"], "test")
            case.assertEqual(Path(result["config"]), owner.settings_path)
            case.assertEqual(
                result["coordinationRoot"],
                json.loads(owner.settings_path.read_text())["coordinationRoot"],
            )
            case.assertEqual(Path(result["home"]), owner.sandbox_home)
            case.assertFalse(
                (case.candidate.parent / "provider-runtime/dev-ar-coordination").exists()
            )
