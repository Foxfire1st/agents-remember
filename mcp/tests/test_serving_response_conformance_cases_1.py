from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
from test_serving_response_conformance import (
    ServingResponseConformanceTests,
    declared_model,
    field_name_form,
    validate_wire,
)


class ServingResponseConformance1(ServingResponseConformanceTests):
    def test_the_fifth_onboarding_shape_conforms(self) -> None:
        # ``GET /api/files/onboarding`` declares a five-member union.
        # ``OnboardingPartnerNone`` is the member no repo with a memory root can produce, so
        # it needs the second, memory-less repo -- without it this declaration was
        # unfalsifiable and the model could have been made unsatisfiable unnoticed.
        with self._client() as client:
            body = self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "N", "path": "pkg/mod.py.md", "direction": "reverse"},
            )
        self.assertEqual(body["kind"], "none")

    def test_the_harness_paste_legs_conform(self) -> None:
        # ``/paste`` fans out on ``entry.kind``: a plain pane answers ``TerminalPaneDelivery``
        # (driven below), a protocol harness answers ``TerminalHarnessDelivery``, and the two
        # 409 refusals are ``TerminalHarnessRefusal``. Only the pane leg was ever driven, so
        # both harness models sat in a declared union that nothing could falsify.
        with self._client() as client:
            harness = self._check(
                client,
                "POST",
                "/api/terminal/live/paste",
                status=200,
                route="/api/terminal/{session}/paste",
                json={"text": "hello", "submit": True},
            )
            # No bridge is listening on the seeded endpoint, so the control client certifies
            # that nothing was written and the route answers its ``unconfirmed`` leg -- still
            # a real ``TerminalHarnessDelivery``, and the only one reachable without a bridge.
            self.assertEqual(harness["status"], "unconfirmed")
            draft = self._check(
                client,
                "POST",
                "/api/terminal/live/paste",
                status=409,
                route="/api/terminal/{session}/paste",
                json={"text": "hello"},
            )
            self.assertEqual(draft["status"], "draft-not-submitted")
            legacy = self._check(
                client,
                "POST",
                "/api/terminal/legacy/paste",
                status=409,
                route="/api/terminal/{session}/paste",
                json={"text": "hello", "submit": True},
            )
        self.assertEqual(legacy["status"], "unsupported")

    def test_the_terminal_open_legs_conform(self) -> None:
        # ``POST /api/terminal/{session}`` declared ``TerminalOpened`` and
        # ``TerminalLaunchConflict`` and the suite only ever drove its 400.
        with self._client() as client:
            opened = self._check(
                client,
                "POST",
                "/api/terminal/plain",
                status=200,
                route="/api/terminal/{session}",
                json={"kind": "terminal"},
            )
            self.assertEqual(opened["status"], "running")
            # The same request against a seat that is already running a *harness* is the
            # launch-selection conflict: same id, different launch identity.
            conflict = self._check(
                client,
                "POST",
                "/api/terminal/live",
                status=409,
                route="/api/terminal/{session}",
                json={"kind": "terminal"},
            )
        self.assertEqual(conflict["status"], "launch-selection-conflict")

    def test_the_conversation_authorization_refusal_conforms(self) -> None:
        # ``CONTROL_RESPONSES[403]`` is declared on all 17 conversation-control routes and no
        # test could reach it, because every client this suite builds is deliberately a
        # loopback peer. Authorization is loopback-ONLY, so a non-loopback peer is the input
        # -- and it refuses at the first gate, before any session lookup.
        with self._client(peer=("10.0.0.5", 5000)) as client:
            body = self._check(
                client,
                "GET",
                "/api/terminal/live/conversation",
                status=403,
                route="/api/terminal/{ar_session_id}/conversation",
                params={"expectedBridgeEpoch": "epoch-1"},
            )
        self.assertEqual(body["status"], "authorization-failed")

    def test_the_terminal_control_refusal_legs_conform(self) -> None:
        # Both members of ``SESSION_CONTROL_RESPONSES``' refusal surface, on every route that
        # shares the table: the 404 for a seat that is not there, and the 409
        # ``UnsupportedSeatRefusal`` for a live harness seat with no control endpoint. Only the
        # success half was driven, under a patched bridge -- which is the half a patched bridge
        # is *able* to reach, and exactly why the other half went unexercised.
        payloads = [
            ("POST", "/set-model", {"model": "opus"}),
            ("POST", "/set-effort", {"effort": "high"}),
            ("POST", "/submission-status", {"expectedBridgeEpoch": "e", "requestIds": ["r1"]}),
            ("POST", "/withdraw", {"expectedBridgeEpoch": "e", "requestId": "r1"}),
            ("POST", "/submit", {"requestId": "r1", "text": "go", "expectedBridgeEpoch": "e"}),
            ("POST", "/reconcile", {"requestId": "r1", "expectedBridgeEpoch": "e"}),
            (
                "POST",
                "/interaction-response",
                {"interactionId": "q1", "expectedBridgeEpoch": "e", "response": "allow"},
            ),
        ]
        with self._client() as client:
            for session, status in (("ghost", 404), ("legacy", 409)):
                self._check(
                    client,
                    "GET",
                    f"/api/terminal/{session}/submission-authority",
                    status=status,
                    route="/api/terminal/{session}/submission-authority",
                )
                self._check(
                    client,
                    "GET",
                    f"/api/terminal/{session}/capabilities",
                    status=status,
                    route="/api/terminal/{session}/capabilities",
                )
                for method, suffix, payload in payloads:
                    self._check(
                        client,
                        method,
                        f"/api/terminal/{session}{suffix}",
                        status=status,
                        route=f"/api/terminal/{{session}}{suffix}",
                        json=payload,
                    )

    def test_the_remaining_terminal_refusal_legs_conform(self) -> None:
        with self._client() as client:
            # ``/image`` 413: the cap is enforced on the read, not only on Content-Length.
            self._check(
                client,
                "POST",
                "/api/terminal/live/image",
                status=413,
                route="/api/terminal/{session}/image",
                files={"file": ("big.png", b"\x89PNG" + b"0" * (5 * 1024 * 1024), "image/png")},
            )
            # ``/attach-task`` 400: a hand-opened harness carries no structural role.
            refused = self._check(
                client,
                "POST",
                "/api/terminal/legacy/attach-task",
                status=400,
                route="/api/terminal/{session}/attach-task",
                json={"taskDocumentRef": {"repository": "R", "path": "t/leaf-1.json"}},
            )
            self.assertEqual(refused["status"], "role-required")
            # ``/attach-task`` 409: the same document+role seat already has a live occupant.
            self._check(
                client,
                "POST",
                "/api/terminal/live/attach-task",
                status=200,
                route="/api/terminal/{session}/attach-task",
                json={
                    "taskDocumentRef": {"repository": "R", "path": "t/leaf-1.json"},
                    "role": "worker",
                },
            )
            taken = self._check(
                client,
                "POST",
                "/api/terminal/legacy/attach-task",
                status=409,
                route="/api/terminal/{session}/attach-task",
                json={
                    "taskDocumentRef": {"repository": "R", "path": "t/leaf-1.json"},
                    "role": "worker",
                },
            )
        self.assertEqual(taken["status"], "seat-taken")

    def test_the_scoped_read_refusal_legs_conform(self) -> None:
        # The files / notes / change-set family shares one two-status refusal idiom, and half
        # of it was declared on routes no test ever refused.
        with self._client() as client:
            self._check(
                client,
                "GET",
                "/api/files/read",
                status=400,
                params={"repo": "R", "path": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=400,
                params={"repo": "R", "path": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=404,
                params={"repo": "ghost", "path": "x.py"},
            )
            self._check(client, "GET", "/api/notes/list", status=404, params={"repo": "ghost"})
            self._check(
                client,
                "GET",
                "/api/notes/list",
                status=400,
                params={"repo": "R", "master": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/notes/read",
                status=400,
                params={"repo": "R", "master": "t", "path": "../escape"},
            )
            self._check(
                client,
                "GET",
                "/api/changeset/task",
                status=404,
                params={"repo": "R", "master": "t", "leaf": "ghost", "mode": "working"},
            )
            self._check(
                client,
                "GET",
                "/api/changeset/file-diff",
                status=404,
                params={
                    "repo": "ghost",
                    "master": "t",
                    "leaf": "leaf-1",
                    "mode": "working",
                    "kind": "code",
                    "path": "f.py",
                },
            )
            self._check(
                client,
                "GET",
                "/api/changeset/file-diff",
                status=400,
                params={
                    "repo": "R",
                    "master": "t",
                    "leaf": "leaf-1",
                    "mode": "working",
                    "kind": "code",
                    "path": "../escape",
                },
            )
            self._check(
                client,
                "POST",
                "/api/operator-inbox",
                status=400,
                json={"ask": "Continue?", "response": "Yes"},
            )

    def test_a_field_name_body_fails_the_declared_contract(self) -> None:
        """The camelCase wire is what this whole contract exists to hold.

        Nothing about a ``response_model`` pins it. ``populate_by_name=True`` -- set by both
        ``WireResponse`` and ``WireModel`` -- makes validation accept the field name as
        readily as the alias, so a handler that dumped ``by_alias=False`` would send the
        cockpit ``identity_digest`` where it reads ``identityDigest``, break every consumer,
        and validate cleanly against its own declaration.

        This drives a real route, rewrites its body into field-name form, and shows the two
        halves of that: the old check passes it, and :func:`validate_wire` does not.
        """

        with self._client() as client:
            body = self._check(client, "GET", "/api/terminal/sessions", status=200)
        model = declared_model(self.routes[("GET", "/api/terminal/sessions")], 200)
        renamed = field_name_form(body)
        # The route really does answer in alias form -- otherwise the rest proves nothing.
        self.assertNotEqual(renamed, body)
        self.assertIn("tmuxName", body["sessions"][0])
        self.assertIn("tmux_name", renamed["sessions"][0])
        # The blindness, demonstrated: the plain validation this suite used to do accepts it.
        TypeAdapter(model).validate_python(renamed)
        # And the check the suite does now does not.
        with self.assertRaises(ValidationError):
            validate_wire(model, renamed)

    def test_the_conversation_wire_is_pinned_to_camel_case_too(self) -> None:
        """The same axis on the surface that dumps ``by_alias=True`` by hand.

        The 25 conversation routes serialize with an explicit
        ``model_dump(mode="json", by_alias=True)``; flipping any one of those flags is a
        one-character change with no compiler and no model to stop it. Driving a refusal body
        is enough to pin the idiom -- ``_error``/``_envelope`` write the same camel keys the
        success dumps do.
        """

        with self._client() as client:
            body = self._check(
                client,
                "POST",
                "/api/terminal/ghost/conversation/interrupt",
                status=404,
                route="/api/terminal/{ar_session_id}/conversation/interrupt",
                params={"expectedBridgeEpoch": "epoch-1"},
                json={"turnId": "t1", "requestId": "r1"},
            )
        self.assertEqual(sorted(body), ["detail", "status"])

    def test_projection_and_document_routes_conform(self) -> None:
        with self._client() as client:
            self._check(client, "GET", "/api/state", status=200)
            self._check(
                client,
                "GET",
                "/api/task-document",
                status=200,
                params={"path": "R/t/leaf-1.json"},
            )
            self._check(client, "GET", "/api/task-document", status=404, params={"path": "no.json"})

    def test_files_routes_conform(self) -> None:
        with self._client() as client:
            self._check(client, "GET", "/api/files/repos", status=200)
            self._check(client, "GET", "/api/files/list", status=200, params={"repo": "R"})
            self._check(
                client,
                "GET",
                "/api/files/read",
                status=200,
                params={"repo": "R", "path": "pkg/mod.py"},
            )
            # forward pairing: a paired file, and one with no sidecar
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "pkg/mod.py"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "README.md"},
            )
            # reverse pairing: a sidecar, and a partnerless overview
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "pkg/mod.py.md", "direction": "reverse"},
            )
            self._check(
                client,
                "GET",
                "/api/files/onboarding",
                status=200,
                params={"repo": "R", "path": "overview.md", "direction": "reverse"},
            )
            # and the refusal idiom the whole family shares
            self._check(client, "GET", "/api/files/list", status=404, params={"repo": "ghost"})
            self._check(
                client,
                "GET",
                "/api/files/read",
                status=404,
                params={"repo": "R", "path": "nope.py"},
            )
            self._check(
                client,
                "GET",
                "/api/files/list",
                status=400,
                params={"repo": "R", "path": "../escape"},
            )
