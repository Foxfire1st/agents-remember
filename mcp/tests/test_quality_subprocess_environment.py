from __future__ import annotations

from agents_remember_test_support.code_quality import quality_subprocess_environment


def test_outer_retry_controls_do_not_leak_into_candidate_tests() -> None:
    environment = {
        "AR_QUALITY_NO_RETRY": "1",
        "AR_QUALITY_PROGRESS_REPORT": "/reports/outer-progress.json",
        "AR_QUALITY_RETRY_CACHE": "/cache/outer",
        "AR_QUALITY_RETRY_CONTEXT_VARIANT": "outer-variant",
        "AR_QUALITY_RETRY_EVIDENCE_KEY": "outer-evidence",
        "AR_QUALITY_INVOCATION": "ci",
        "AR_DAGGER_TEST_ATTESTATION": "a" * 32,
        "AR_QUALITY_ATTEMPT_NONCE": "a" * 32,
        "AR_QUALITY_MEMORY_CAP": "4294967296",
        "PATH": "/usr/bin",
    }

    child = quality_subprocess_environment.child_environment(environment)

    assert not quality_subprocess_environment.OUTER_INVOCATION_ONLY & child.keys()
    assert child == {
        "AR_QUALITY_INVOCATION": "ci",
        "AR_DAGGER_TEST_ATTESTATION": "a" * 32,
        "AR_QUALITY_ATTEMPT_NONCE": "a" * 32,
        "AR_QUALITY_MEMORY_CAP": "4294967296",
        "PATH": "/usr/bin",
    }
