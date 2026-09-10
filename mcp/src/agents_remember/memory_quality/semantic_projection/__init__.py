"""MCAR-R08 semantic projection and content binding.

Canonical deterministic projections that bind every governed onboarding artifact
to exact semantic content.  The projection replaces only closeout-authorized
mechanical fields (lastVerifiedCommitHash, lastVerifiedCommitDate,
fingerprint) with typed sentinels; all other bytes remain digest-bound.

Projection v1 is frozen from the current canonical writers.  Adding a normalized
field or inline adapter is a new projection version and invalidates incompatible
prior acceptance.
"""
