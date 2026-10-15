"""MIK-R81 rule 3 (F14): the warm location cache authenticates what it serves.

The key carries the canonical coordination root, every hit re-validates confinement, and
the locator/manifest identities are the stat of the bytes parsed, sampled from the same
opens -- a replacement or deletion during the fill can never bind the old location to
newly settled operands.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agents_remember.worktrees.integration.lifecycle import lifecycle_location_cache as cache_module
from agents_remember.worktrees.integration.lifecycle import (
    lifecycle_operation_location as location_module,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_location_cache import (
    resolve_lifecycle_operation_location,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.worktree_contract import write_contract
from lifecycle_enclosure_test_support import enclosure_contract, publish_test_enclosure


class LocationCacheScopeTests(unittest.TestCase):
    def _published(self, root: Path):  # type: ignore[no-untyped-def]
        contract, text = enclosure_contract(root)
        write_contract(contract.contract_path, contract)
        location = publish_test_enclosure(contract, text)
        resolved = resolve_lifecycle_operation_location(
            contract.coordination_root, contract.contract_path
        )
        return contract, location, resolved

    def test_wrong_root_never_serves_another_checkout(self) -> None:
        """A cache filled under root A refuses under root B; it never serves A's location."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, location, _ = self._published(Path(tmp) / "a")
            other = Path(tmp) / "b" / "coordination"
            (other / "tasks").mkdir(parents=True)
            with self.assertRaises(LifecycleOperationLocationError) as raised:
                resolve_lifecycle_operation_location(other, contract.contract_path)
            self.assertEqual(raised.exception.status, "operation-location-invalid")
            # Root A still serves its own location: the B refusal cached nothing for A.
            again = resolve_lifecycle_operation_location(
                contract.coordination_root, contract.contract_path
            )
            self.assertEqual(again.locator.locatorId, location.locator.locatorId)
            # Each root holds its own entry: B refuses again without touching A.
            with self.assertRaises(LifecycleOperationLocationError) as refused:
                resolve_lifecycle_operation_location(other, contract.contract_path)
            self.assertEqual(refused.exception.status, "operation-location-invalid")
            third = resolve_lifecycle_operation_location(
                contract.coordination_root, contract.contract_path
            )
            self.assertEqual(third.locator.locatorId, location.locator.locatorId)

    def test_manifest_deleted_during_fill_is_not_served(self) -> None:
        """The fill binds the stat of the bytes read: a manifest deleted mid-fill misses."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, location, _ = self._published(Path(tmp))
            manifest = Path(location.manifest_path)
            real_read_manifest = location_module._read_manifest

            def read_then_delete(path: Path, contract_path: Path):  # type: ignore[no-untyped-def]
                model, stat, raw = real_read_manifest(path, contract_path)
                manifest.unlink(missing_ok=True)
                return model, stat, raw

            cache_module._location_cache.clear()
            with mock.patch.object(location_module, "_read_manifest", read_then_delete):
                filled = resolve_lifecycle_operation_location(
                    contract.coordination_root, contract.contract_path
                )
            self.assertEqual(filled.locator.locatorId, location.locator.locatorId)
            self.assertFalse(manifest.exists(), "the fill raced a manifest deletion")
            with self.assertRaises(LifecycleOperationLocationError):
                resolve_lifecycle_operation_location(
                    contract.coordination_root, contract.contract_path
                )

    def test_locator_created_after_adoption_required_is_seen(self) -> None:
        """A missing-locator negative misses once the locator is published."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, text = enclosure_contract(Path(tmp))
            write_contract(contract.contract_path, contract)
            with self.assertRaises(LifecycleOperationLocationError) as raised:
                resolve_lifecycle_operation_location(
                    contract.coordination_root, contract.contract_path
                )
            self.assertEqual(raised.exception.status, "operation-location-adoption-required")
            publish_test_enclosure(contract, text)
            resolved = resolve_lifecycle_operation_location(
                contract.coordination_root, contract.contract_path
            )
            self.assertTrue(resolved.locator.locatorId)

    def test_served_locations_are_copies(self) -> None:
        """Mutating a served location never poisons the cached outcome."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, location, _ = self._published(Path(tmp))
            first = resolve_lifecycle_operation_location(
                contract.coordination_root, contract.contract_path
            )
            self.assertIsNot(first, location)
            first.locator.state = "reserved"  # type: ignore[assignment]
            second = resolve_lifecycle_operation_location(
                contract.coordination_root, contract.contract_path
            )
            self.assertEqual(second.locator.state, location.locator.state)
            self.assertEqual(second.locator.locatorId, location.locator.locatorId)

    def test_mid_read_rewrite_retries_to_consistent_bytes(self) -> None:
        """An in-place write between read and fstat never binds old bytes to new stats."""

        with tempfile.TemporaryDirectory() as tmp:
            _, location, _ = self._published(Path(tmp))
            locator_path = Path(location.locator_path)
            first_raw = locator_path.read_bytes()
            second_raw = first_raw + b" "
            calls: list[int] = []

            def flaky_fstat(fileno: int) -> SimpleNamespace:
                calls.append(fileno)
                if len(calls) == 2:
                    # An in-place write lands after the first read completes: the
                    # after-sample disagrees, so the first bytes must be discarded.
                    locator_path.write_bytes(second_raw)
                    first = os.stat(locator_path)
                    return SimpleNamespace(
                        st_mtime_ns=first.st_mtime_ns + 1,
                        st_size=first.st_size,
                        st_ctime_ns=first.st_ctime_ns + 1,
                    )
                current = os.stat(locator_path)
                return SimpleNamespace(
                    st_mtime_ns=current.st_mtime_ns,
                    st_size=current.st_size,
                    st_ctime_ns=current.st_ctime_ns,
                )

            with mock.patch.object(cache_module.os, "fstat", flaky_fstat):
                _, identity, raw = location_module._read_locator(locator_path, locator_path)
            # Exactly one retry: the consistent second attempt's bytes and identity.
            self.assertEqual(len(calls), 4)
            self.assertEqual(raw, second_raw)
            settled = os.stat(locator_path)
            self.assertEqual(identity, (settled.st_mtime_ns, settled.st_size, settled.st_ctime_ns))

    def test_persistent_mid_read_change_fails_closed(self) -> None:
        """A file that keeps changing under the read is refused, never bound."""

        with tempfile.TemporaryDirectory() as tmp:
            _, location, _ = self._published(Path(tmp))
            locator_path = Path(location.locator_path)
            settled = os.stat(locator_path)
            flip = [True]

            def never_settled(fileno: int) -> SimpleNamespace:
                flip[0] = not flip[0]
                skew = 1 if flip[0] else 2
                return SimpleNamespace(
                    st_mtime_ns=settled.st_mtime_ns + skew,
                    st_size=settled.st_size,
                    st_ctime_ns=settled.st_ctime_ns,
                )

            cache_module._location_cache.clear()
            with (
                mock.patch.object(cache_module.os, "fstat", never_settled),
                self.assertRaises(LifecycleOperationLocationError) as raised,
            ):
                location_module._read_locator(locator_path, locator_path)
            self.assertEqual(raised.exception.status, "operation-location-invalid")
            self.assertEqual(raised.exception.observed.get("errorType"), "changed during read")
            self.assertEqual(len(cache_module._location_cache), 0)

    def test_unavailable_descriptor_identity_is_refused(self) -> None:
        """An fstat failure refuses the read: no model is built on bytes without identity."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, location, _ = self._published(Path(tmp))
            locator_path = Path(location.locator_path)

            def no_identity(fileno: int) -> SimpleNamespace:
                raise OSError("bad file descriptor")

            cache_module._location_cache.clear()
            with (
                mock.patch.object(cache_module.os, "fstat", no_identity),
                self.assertRaises(LifecycleOperationLocationError) as raised,
            ):
                location_module._read_locator(locator_path, locator_path)
            self.assertEqual(raised.exception.status, "operation-location-invalid")
            # The resolution refuses too, and caches neither a location nor an error.
            with (
                mock.patch.object(cache_module.os, "fstat", no_identity),
                self.assertRaises(LifecycleOperationLocationError),
            ):
                resolve_lifecycle_operation_location(
                    contract.coordination_root, contract.contract_path
                )
            self.assertEqual(len(cache_module._location_cache), 0)
            resolved = resolve_lifecycle_operation_location(
                contract.coordination_root, contract.contract_path
            )
            self.assertEqual(resolved.locator.locatorId, location.locator.locatorId)

    def test_positive_put_without_identities_is_refused(self) -> None:
        """The cache refuses positive entries whose consumed identities are unavailable."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, location, _ = self._published(Path(tmp))
            root = contract.coordination_root.resolve(strict=False)
            key = f"{root.as_posix()}\0{contract.contract_path.as_posix()}"
            contract_stat = cache_module._stat_identity(contract.contract_path)
            cache_module._location_cache.pop(key, None)
            cache_module._location_cache_put(
                key,
                contract_stat,
                location=location,
                identities=cache_module._ObservedIdentity(),
            )
            self.assertIsNone(cache_module._location_cache_get(key))

    def test_locator_replaced_during_fill_serves_fresh_location(self) -> None:
        """A locator replaced mid-fill cannot pin the old identity: the next read misses."""

        with tempfile.TemporaryDirectory() as tmp:
            contract, location, _ = self._published(Path(tmp))
            real_read_locator = location_module._read_locator
            reads = 0

            def read_then_touch(path: Path, contract_path: Path):  # type: ignore[no-untyped-def]
                nonlocal reads
                reads += 1
                model, stat, raw = real_read_locator(path, contract_path)
                if reads == 1:
                    # Same bytes, new identity: the fill must not bind the old stats.
                    os.utime(path, ns=(stat[0] + 1, stat[0] + 1))
                return model, stat, raw

            cache_module._location_cache.clear()
            with mock.patch.object(location_module, "_read_locator", read_then_touch):
                filled = resolve_lifecycle_operation_location(
                    contract.coordination_root, contract.contract_path
                )
                self.assertEqual(filled.locator.locatorId, location.locator.locatorId)
                # The stored old identity misses against the touched file: a fresh read.
                before = reads
                again = resolve_lifecycle_operation_location(
                    contract.coordination_root, contract.contract_path
                )
            self.assertGreater(reads, before)
            self.assertEqual(again.locator.locatorId, location.locator.locatorId)
