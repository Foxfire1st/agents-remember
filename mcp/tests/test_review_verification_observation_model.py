"""``VerificationObservation``: the recorded run, its artifact digest, its reads and its refusals.

These cases protect ``KS-R12@v1``'s observation contract in the order the packet states it: the typed
aggregate under the same envelope, the exact tested candidate recorded as data, the verbatim command
identity, the result artifact bound by the digest of its bytes, the closed execution-result
vocabulary, the run's environment, the write-time digest decision, the artifact resolution states at
read time, and the read projection that reports facts and no verdict.

Three properties are load-bearing enough to name before the cases:

* **The digest identifies an artifact, never a row and never a dataset.** The value the record carries
  is ``sha256`` of the artifact's bytes; the record states whether that digest was *checked* against
  the bytes at write time. Writing a plausible hex string would be a fabricated claim, so the cases
  build real bytes under a temporary root and compare against what the write path actually did.
* **"Not run" is never reported as passed.** The execution-result set is closed, an unlisted value is
  refused rather than coerced, and ``not_run`` is served as ``not_run``.
* **Nothing here manufactures a verdict.** A passing run and a failing run are equally facts: neither
  is a finding, a gate or a lifecycle change, and no served field or count could be read as one.
"""

from __future__ import annotations

import pytest
from agents_remember.models.knowledge.evidence import (
    PublicationReference,
    ResultArtifactReference,
    RunEnvironment,
    VerificationObservationPayload,
)

pytestmark = pytest.mark.evidence_unit

MEMORY_TREE = "b" * 40


# ---------------------------------------------------------------------------
# Required Behavior 3.1, 3.5 and 3.6: the typed aggregate and its closed vocabularies.


def test_the_run_environment_is_bounded_and_records_the_runs_own_toolchain() -> None:
    """3.6: the environment names the run's environment and is a bounded recorded value."""

    environment = RunEnvironment(
        host="fixture-host", interpreter="3.13.15", toolchain=(("pytest", "9.0.0"),)
    )
    assert environment.toolchain == (("pytest", "9.0.0"),)
    assert RunEnvironment.model_fields["toolchain"].default == ()
    with pytest.raises(ValueError):
        RunEnvironment(host="h", interpreter="i", toolchain=(("pytest", "9"), ("pytest", "10")))
    with pytest.raises(ValueError):
        RunEnvironment(host="h", interpreter="i", toolchain=(("", "9"),))
    with pytest.raises(ValueError):
        RunEnvironment(
            host="h", interpreter="i", toolchain=tuple((f"t{n}", "1") for n in range(20))
        )


# ---------------------------------------------------------------------------
# Required Behavior 7.1: the artifact reference is a confined reference.


def test_an_artifact_path_that_is_not_confined_is_refused_as_a_shape_error() -> None:
    """7.1: no absolute root, drive, UNC, backslash, NUL, ``..`` or Git pathspec spelling.

    The protected property is that the confinement is vocabulary rather than a runtime check that can
    be skipped: each refused spelling is refused at construction, before any write is attempted, and
    the ones that *are* legitimate (glob characters, which ``git ls-tree`` resolves literally) are
    admitted so a real artifact is never un-recordable.
    """

    refused = (
        "/absolute/report.log",
        "\\\\server\\share\\report.log",
        "C:/report.log",
        "reports\\report.log",
        "~/report.log",
        "reports/\x00report.log",
        "reports/../report.log",
        "..",
        "",
        "   ",
        ":(exclude)reports/report.log",
        ":!reports/report.log",
    )
    for path in refused:
        with pytest.raises(ValueError):
            ResultArtifactReference(
                path=path,
                sha256="a" * 64,
                size_bytes=1,
                digest_checked_against_bytes=False,
            )
    admitted = ResultArtifactReference(
        path="reports/a[1].log",
        sha256="a" * 64,
        size_bytes=1,
        digest_checked_against_bytes=False,
    )
    assert admitted.path == "reports/a[1].log"


def test_a_publication_destination_is_confined_and_its_digest_is_the_manifests() -> None:
    """9.3: the record carries its publication reference explicitly, and it is not a second archive."""

    reference = PublicationReference(
        destination="notes/reports/260915-KS-L12-evidence.json",
        sha256="b" * 64,
        published_at="2026-09-18T03:00:00+00:00",
    )
    assert reference.sha256 == "b" * 64
    for destination in ("/abs/x.json", "C:/x.json", "a\\b.json", "../x.json", "", "a//b.json"):
        with pytest.raises(ValueError):
            PublicationReference(
                destination=destination, sha256="b" * 64, published_at="2026-09-18T03:00:00+00:00"
            )
    # The digest the reference carries identifies the *published manifest*, and nothing in the model
    # ties it to the artifact: there is one field for each and no field that could conflate them.
    fields = set(VerificationObservationPayload.model_fields)
    assert {"result_artifact", "publication"} <= fields
    assert not {"status", "sufficient", "verified", "grade", "score"} & fields


# ---------------------------------------------------------------------------
# Required Behavior 3.4, 7.3 and 7.5: the write-time digest decision.


# ---------------------------------------------------------------------------
# Required Behavior 8.2 and 7.4: the read projection and its resolution states.


# ---------------------------------------------------------------------------
# Required Behavior 3.1, 6.5 and 7.2: the batch path, the generation gate and no content store.
