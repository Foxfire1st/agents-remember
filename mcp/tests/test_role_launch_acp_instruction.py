"""The wait instruction an ACP seat's launch appends, and the receipt field that says why not.

MIK-R216 rule 3 appends one sentence to an ACP seat's compiled prompt before the message is
published, so the stored first message already carries it. These cases drive real launches
through the dispatch route against the fake bridge, with the plugin's own file
(``<PASEO_HOME>/agents-remember/acp-providers.json``) written where the launch reads it:

* an ACP seat's stored first message and receipt prompt carry the sentence, and a seat whose
  provider is not ACP carries neither;
* a missing or unreadable file, or a provider the file does not name, adds nothing, and the
  receipt names the reason in its own field;
* the saved first message of an ACP seat still passes the retry check;
* the seat's capsule and its digest are what they are without the sentence;
* the reader refuses a document written for another schema, one with extra keys, and bytes that
  are not JSON.
"""

from __future__ import annotations

import hashlib
import json
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import test_paseo_launch as launch
from agents_remember.cli import role_launch_receipts
from agents_remember.cli.role_handover_artifacts import checked_handover_artifact_target
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.acp_providers import (
    ACP_PROVIDERS_SCHEMA,
    ACP_WAIT_INSTRUCTION,
    acp_providers_path,
    read_acp_providers,
)

PASEO_HOME = "paseo-home"
DOCUMENT = {
    "schema": ACP_PROVIDERS_SCHEMA,
    "paseoVersion": "0.12.0",
    "readAt": "2026-10-28T10:00:00+01:00",
    "files": ["/opt/paseo/node_modules/@getpaseo/server/dist/provider-registry.js"],
    "dropouts": {},
}


def providers_document(providers: dict[str, Any]) -> dict[str, Any]:
    return {**DOCUMENT, "providers": providers}


ACP_PROVIDER = {"class": "GenericACPAgentClient", "acp": True, "reason": "extends-acp"}
NATIVE_PROVIDER = {"class": "CodexAgentClient", "acp": False, "reason": "class-not-acp"}


class AcpInstructionLaunchTests(launch.PaseoLaunchTestCase):
    """The launch fixture, with the plugin's own provider file in the host's home."""

    def paseo_home(self) -> Path:
        runtime = self.config.paseo_runtime
        assert runtime is not None, "the fixture configures a Paseo runtime"
        return runtime.home

    def write_providers(self, providers: dict[str, Any]) -> Path:
        path = acp_providers_path(self.paseo_home())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(providers_document(providers)), encoding="utf-8")
        return path

    def request_on_leaf(self, leaf_path: str) -> RoleDispatchRequest:
        """A second seat of the same role, on its own leaf: its receipt is its own file."""

        return RoleDispatchRequest.model_validate(
            {
                "role": "worker",
                "requestId": uuid.uuid4(),
                "sprintDocumentRef": launch.SPRINT_REF,
                "masterDocumentRef": launch.MASTER_REF,
                "taskDocumentRef": TaskDocumentRef(repository=launch.REPO, path=leaf_path),
            }
        )

    def launched(self, request: RoleDispatchRequest) -> dict[str, Any]:
        status, _answer = self.dispatch(request)
        self.assertEqual(status, 200)
        return self.receipt(request)

    def launched_watched(
        self, request: RoleDispatchRequest
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """The receipt on disk now, and the one the launch wrote before it called the runtime.

        A launch drops ``replayRequest`` from its receipt once the agent exists, so the message
        the agent was given is read from the receipt as the launch itself wrote it.
        """

        observed: list[tuple[str, Any, Any, bool]] = []
        self.watch_launch(self.receipt_path(request), observed)
        receipt = self.launched(request)
        self.assertIn("agent-create", [command for command, *_rest in observed])
        return receipt, observed[0][1]

    def stored_message(self, receipt: dict[str, Any]) -> str:
        return Path(receipt["handoverArtifact"]["canonicalPath"]).read_text(encoding="utf-8")

    # -- rule 3: the sentence reaches the stored first message -------------------------------

    def test_an_acp_seats_first_message_carries_the_wait_instruction(self) -> None:
        self.write_providers({"codex": ACP_PROVIDER})

        receipt, saved = self.launched_watched(self.request("worker"))
        stored = self.stored_message(receipt)

        self.assertEqual(stored, f"{self.prompt}\n\n{ACP_WAIT_INSTRUCTION}")
        # The reference's digest was taken after the sentence joined the message, and the message
        # the agent is given is the artifact line followed by exactly those bytes.
        self.assertEqual(
            receipt["handoverArtifact"]["sha256"],
            hashlib.sha256(stored.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(saved["replayRequest"]["agent"]["prompt"].split("\n", 1)[1], stored)
        self.assertEqual(
            saved["acpInstruction"],
            {"provider": "codex", "acp": True, "instruction": "added"},
        )
        self.assertEqual(
            receipt["acpInstruction"],
            {"provider": "codex", "acp": True, "instruction": "added"},
        )
        # The receipt still names the provider the launch used.
        self.assertEqual(receipt["agent"]["id"], "codex")

    def test_a_seat_whose_provider_is_not_acp_carries_no_instruction(self) -> None:
        self.write_providers({"codex": NATIVE_PROVIDER})

        receipt, saved = self.launched_watched(self.request("worker"))

        self.assertEqual(self.stored_message(receipt), self.prompt)
        self.assertNotIn("WAITING FOR SUBAGENTS", saved["replayRequest"]["agent"]["prompt"])
        self.assertEqual(
            receipt["acpInstruction"],
            {
                "provider": "codex",
                "acp": False,
                "instruction": "skipped",
                "reason": "provider-not-acp",
            },
        )

    # -- rule 1: no answer from the file adds nothing, and the receipt says why --------------

    def test_a_missing_provider_file_adds_nothing_and_the_receipt_says_so(self) -> None:
        receipt = self.launched(self.request("worker"))

        self.assertEqual(self.stored_message(receipt), self.prompt)
        self.assertEqual(
            receipt["acpInstruction"],
            {
                "provider": "codex",
                "acp": False,
                "instruction": "skipped",
                "reason": "acp-providers-unreadable",
            },
        )

    def test_an_unreadable_provider_file_adds_nothing_and_the_receipt_says_so(self) -> None:
        path = acp_providers_path(self.paseo_home())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"schema": "ar-acp-providers/v1", "providers": ', encoding="utf-8")

        receipt = self.launched(self.request("worker"))

        self.assertEqual(self.stored_message(receipt), self.prompt)
        self.assertEqual(receipt["acpInstruction"]["reason"], "acp-providers-unreadable")

    def test_a_provider_the_file_does_not_name_adds_nothing_and_the_receipt_says_so(self) -> None:
        self.write_providers({"hermes": ACP_PROVIDER})

        receipt = self.launched(self.request("worker"))

        self.assertEqual(self.stored_message(receipt), self.prompt)
        self.assertEqual(
            receipt["acpInstruction"],
            {
                "provider": "codex",
                "acp": False,
                "instruction": "skipped",
                "reason": "provider-not-named",
            },
        )

    def test_no_configured_runtime_reads_as_no_file(self) -> None:
        """A configuration without a host has no home to read: the same skipped answer."""

        unconfigured = launch.runtime_config(self.root / "no-runtime", configured=False)

        self.assertIsNone(unconfigured.paseo_runtime)
        self.assertEqual(
            launch.role_launch_routes._acp_launch_instruction(unconfigured, "codex"),
            (
                None,
                {
                    "provider": "codex",
                    "acp": False,
                    "instruction": "skipped",
                    "reason": "acp-providers-unreadable",
                },
            ),
        )

    # -- the retry check of the saved first message -----------------------------------------

    def test_the_saved_first_message_of_an_acp_seat_passes_the_retry_check(self) -> None:
        self.write_providers({"codex": ACP_PROVIDER})
        request = self.request("worker")
        receipt_path = self.receipt_path(request)
        _receipt, saved = self.launched_watched(request)
        message = saved["replayRequest"]["agent"]["prompt"]
        artifact_path = Path(saved["handoverArtifact"]["path"])
        artifact_path.unlink()

        canonical, body = checked_handover_artifact_target(
            saved["handoverArtifact"],
            message,
            receipt=receipt_path,
            handover_file=saved["handoverFile"],
        )
        self.assertEqual(canonical, Path(saved["handoverArtifact"]["canonicalPath"]))
        self.assertEqual(body.decode("utf-8"), f"{self.prompt}\n\n{ACP_WAIT_INSTRUCTION}")

        # The restore a retry runs writes the saved message back from the receipt alone.
        role_launch_receipts._restore_launch_handover(receipt_path, saved, saved["replayRequest"])

        self.assertEqual(artifact_path.read_text(encoding="utf-8"), body.decode("utf-8"))

    # -- the capsule is not part of this ----------------------------------------------------

    def test_the_capsule_and_its_digest_are_the_same_with_and_without_the_instruction(self) -> None:
        plain = self.launched(self.request("worker"))
        plain_bytes = self.stored_message(plain).encode("utf-8")

        self.write_providers({"codex": ACP_PROVIDER})
        acp = self.launched(self.request_on_leaf("master/02_leaf.json"))
        acp_bytes = self.stored_message(acp).encode("utf-8")

        # The same seat, the same capsule: its digest, its operation and its composition are
        # untouched, and the message differs by exactly the one appended paragraph.
        self.assertEqual(acp["acpInstruction"]["instruction"], "added")
        self.assertEqual(plain["acpInstruction"]["instruction"], "skipped")
        self.assertEqual(acp["capsuleDigest"], plain["capsuleDigest"])
        self.assertEqual(acp["capsuleOperation"], plain["capsuleOperation"])
        self.assertEqual(acp["instructionComposition"], plain["instructionComposition"])
        self.assertEqual(acp_bytes, plain_bytes + b"\n\n" + ACP_WAIT_INSTRUCTION.encode("utf-8"))
        self.assertNotEqual(plain["handoverArtifact"]["sha256"], acp["handoverArtifact"]["sha256"])


class ReadAcpProvidersTests(unittest.TestCase):
    """The reader: the plugin's document, or nothing at all."""

    def reader(self, text: str | None) -> Any:
        with TemporaryDirectory() as temporary:
            path = acp_providers_path(Path(temporary) / PASEO_HOME)
            if text is not None:
                path.parent.mkdir(parents=True)
                path.write_text(text, encoding="utf-8")
            return read_acp_providers(path)

    def test_a_document_the_plugin_writes_is_read(self) -> None:
        document = self.reader(json.dumps(providers_document({"hermes": ACP_PROVIDER})))

        self.assertIsNotNone(document)
        self.assertEqual(document.schema_, ACP_PROVIDERS_SCHEMA)
        self.assertEqual(document.paseoVersion, "0.12.0")
        self.assertTrue(document.providers["hermes"].acp)
        self.assertEqual(document.providers["hermes"].client_class, "GenericACPAgentClient")
        self.assertEqual(document.providers["hermes"].reason, "extends-acp")

    def test_a_document_without_the_optional_fields_is_read(self) -> None:
        document = self.reader(
            json.dumps(
                {
                    "schema": ACP_PROVIDERS_SCHEMA,
                    "providers": {"hermes": {"acp": True}},
                }
            )
        )

        self.assertIsNotNone(document)
        self.assertEqual(document.files, ())
        self.assertEqual(document.providers["hermes"].client_class, None)

    def test_a_missing_file_is_nothing(self) -> None:
        self.assertIsNone(self.reader(None))

    def test_another_schema_is_nothing(self) -> None:
        document = {**providers_document({"hermes": ACP_PROVIDER}), "schema": "ar-acp-providers/v2"}

        self.assertIsNone(self.reader(json.dumps(document)))

    def test_an_extra_key_is_nothing(self) -> None:
        extra_at_the_top = {
            **providers_document({"hermes": ACP_PROVIDER}),
            "pluginVersion": "1.2.3",
        }
        extra_in_an_entry = providers_document({"hermes": {**ACP_PROVIDER, "emitsHelpers": False}})

        self.assertIsNone(self.reader(json.dumps(extra_at_the_top)))
        self.assertIsNone(self.reader(json.dumps(extra_in_an_entry)))

    def test_bytes_that_are_not_json_are_nothing(self) -> None:
        self.assertIsNone(self.reader("not json at all\n"))
        self.assertIsNone(self.reader(""))
