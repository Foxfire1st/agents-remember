"""Access-only OAuth provisioning refuses refresh capability and unsafe lifetimes."""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from agent_evals.codex_access import AccessOnlyError, install_access_pool, validate_access_pool

DEADLINE = 2_000_000_000


def token(claims: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "synthetic." + payload + ".synthetic"


def credential(exp: Any = DEADLINE + 600) -> dict[str, str]:
    return {
        "access_token": token({"exp": exp}),
        "id_token": "SYNTHETIC-ID",
        "account_id": "SYNTHETIC-ACCOUNT",
    }


class CodexAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "hermes"
        self.home.mkdir(mode=0o700)
        self.source = self.root / "supplied.json"
        self.source.write_text(json.dumps(credential()))

    def pool(self) -> dict[str, Any]:
        return json.loads((self.home / "auth.json").read_text())

    def test_native_pool_shape_private_permissions_and_safe_receipt(self) -> None:
        receipt = install_access_pool(self.source, self.home, DEADLINE)
        pool = self.pool()
        self.assertEqual(set(pool), {"credential_pool"})
        self.assertEqual(set(pool["credential_pool"]), {"openai-codex"})
        row = pool["credential_pool"]["openai-codex"][0]
        self.assertEqual((row["source"], row["auth_type"]), ("manual", "oauth"))
        self.assertNotIn("refresh_token", row)
        self.assertEqual((self.home / "auth.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(validate_access_pool(self.home, DEADLINE)["status"], "valid")
        encoded = json.dumps(receipt)
        for value in credential().values():
            self.assertNotIn(value, encoded)
        self.assertNotIn("sha256", encoded)

    def test_rejects_refresh_recursively_without_disclosing_supplied_values(self) -> None:
        for location in (
            {"refresh_token": "SYNTHETIC-SECRET"},
            {"nested": [{"refresh_token": None}]},
        ):
            with self.subTest(location=list(location)):
                self.source.write_text(json.dumps({**credential(), **location}))
                with self.assertRaisesRegex(
                    AccessOnlyError, "codex-access-refresh-token-present"
                ) as raised:
                    install_access_pool(self.source, self.home, DEADLINE)
                self.assertNotIn("SYNTHETIC-SECRET", str(raised.exception))
                self.assertFalse((self.home / "auth.json").exists())

    def test_rejects_incomplete_or_ambiguous_expiry_and_exact_input_schema(self) -> None:
        inputs = [
            {**credential(), "access_token": "SYNTHETIC-SECRET"},
            {**credential(), "access_token": token({})},
            credential(True),
            credential(float("inf")),
            credential(DEADLINE - 1),
            {**credential(), "unexpected": "SYNTHETIC-SECRET"},
            {**credential(), "id_token": ""},
        ]
        for index, value in enumerate(inputs):
            with self.subTest(index=index):
                self.source.write_text(json.dumps(value))
                with self.assertRaises(AccessOnlyError) as raised:
                    install_access_pool(self.source, self.home, DEADLINE)
                self.assertNotIn("SYNTHETIC-SECRET", str(raised.exception))
                self.assertFalse((self.home / "auth.json").exists())
        self.source.write_text('{"access_token":"SYNTHETIC-SECRET","access_token":"duplicate"}')
        with self.assertRaises(AccessOnlyError):
            install_access_pool(self.source, self.home, DEADLINE)
        self.source.write_bytes(b"\xffSYNTHETIC-SECRET")
        with self.assertRaises(AccessOnlyError) as raised:
            install_access_pool(self.source, self.home, DEADLINE)
        self.assertNotIn("SYNTHETIC-SECRET", str(raised.exception))
        self.source.write_text(
            json.dumps({**credential(), "id_token": "é" * 30_000}, ensure_ascii=False)
        )
        with self.assertRaisesRegex(AccessOnlyError, "codex-access-file-limit"):
            install_access_pool(self.source, self.home, DEADLINE)
        self.assertFalse((self.home / "auth.json").exists())
        self.source.write_text(json.dumps(credential()))
        for deadline in (float("nan"), float("inf"), -1, True):
            with self.subTest(deadline=repr(deadline)), self.assertRaises(AccessOnlyError):
                install_access_pool(self.source, self.home, deadline)

    def test_expiry_boundary_and_native_metadata_survive_revalidation(self) -> None:
        self.source.write_text(json.dumps(credential(DEADLINE)))
        install_access_pool(self.source, self.home, DEADLINE)
        pool = self.pool()
        pool.update(providers={}, version=1, updated_at="2030-01-01T00:00:00Z")
        row = pool["credential_pool"]["openai-codex"][0]
        row.update(
            label="manual", priority=0, request_count=7, last_status="ok", last_status_at=None
        )
        (self.home / "auth.json").write_text(json.dumps(pool))
        self.assertEqual(validate_access_pool(self.home, DEADLINE)["status"], "valid")
        with self.assertRaisesRegex(AccessOnlyError, "codex-access-expiry-insufficient"):
            validate_access_pool(self.home, DEADLINE + 0.5)

    def test_changed_pool_cannot_add_refresh_provider_or_second_identity(self) -> None:
        install_access_pool(self.source, self.home, DEADLINE)
        baseline = self.pool()
        for mutation in ("refresh", "provider", "second", "source"):
            with self.subTest(mutation=mutation):
                pool = json.loads(json.dumps(baseline))
                row = pool["credential_pool"]["openai-codex"][0]
                if mutation == "refresh":
                    row["nested"] = [{"refresh_token": "SYNTHETIC-SECRET"}]
                elif mutation == "provider":
                    pool["providers"] = {"nous": {}}
                elif mutation == "second":
                    pool["credential_pool"]["openai-codex"].append(dict(row))
                else:
                    row["source"] = "device_code"
                (self.home / "auth.json").write_text(json.dumps(pool))
                with self.assertRaises(AccessOnlyError):
                    validate_access_pool(self.home, DEADLINE)

    def test_no_overwrite_or_linked_credential_paths(self) -> None:
        install_access_pool(self.source, self.home, DEADLINE)
        original = (self.home / "auth.json").read_bytes()
        with self.assertRaises(AccessOnlyError):
            install_access_pool(self.source, self.home, DEADLINE)
        self.assertEqual((self.home / "auth.json").read_bytes(), original)
        (self.home / "auth.json").chmod(0o644)
        with self.assertRaises(AccessOnlyError):
            validate_access_pool(self.home, DEADLINE)
        (self.home / "auth.json").chmod(0o600)
        linked = self.root / "linked-auth.json"
        os.link(self.home / "auth.json", linked)
        with self.assertRaises(AccessOnlyError):
            validate_access_pool(self.home, DEADLINE)
        linked.unlink()
        (self.home / "auth.json").unlink()
        (self.home / "auth.json").symlink_to(self.source)
        with self.assertRaises(AccessOnlyError):
            validate_access_pool(self.home, DEADLINE)
        (self.home / "auth.json").unlink()
        self.source.rename(self.root / "target.json")
        self.source.symlink_to(self.root / "target.json")
        with self.assertRaises(AccessOnlyError):
            install_access_pool(self.source, self.home, DEADLINE)

    def test_private_pool_storage_is_linear_and_reclaimed_for_parallel_trials(self) -> None:
        for count in (2, 42):
            with self.subTest(count=count):
                batch = self.root / f"batch-{count}"
                batch.mkdir()
                for index in range(count):
                    home = batch / str(index)
                    home.mkdir(mode=0o700)
                    install_access_pool(self.source, home, DEADLINE)
                    validate_access_pool(home, DEADLINE)
                self.assertEqual(len(list(batch.rglob("auth.json"))), count)
                self.assertEqual(len(list(batch.rglob("*"))), 2 * count)
                shutil.rmtree(batch)
                self.assertFalse(batch.exists())
