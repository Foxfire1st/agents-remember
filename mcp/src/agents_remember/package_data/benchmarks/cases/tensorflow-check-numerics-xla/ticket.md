# Source Ticket: TensorFlow #117319

This benchmark is based on TensorFlow issue #117319:
`tf.debugging.check_numerics` silently passes NaN/Inf under
`jit_compile=True`.

Issue URL: https://github.com/tensorflow/tensorflow/issues/117319

## Ticket Summary

The reporter describes a numerical-safety regression:

- `tf.debugging.check_numerics` raises `InvalidArgumentError` for NaN/Inf in
  eager mode.
- The same check raises under `tf.function(jit_compile=False)`.
- Under `tf.function(jit_compile=True)`, the NaN/Inf tensor passes through
  without an error.
- The reported impact is silent loss of numerical guards when users enable XLA
  compilation for inference or optimization.

The ticket was still open when this benchmark was authored. That is not ideal
for long-term benchmark design, because completed issues are easier to verify
against the landed fix. This case is acceptable as a draft because a later issue
comment identifies a concrete suspected root cause that can be checked against
the pinned source tree, while the fix does not appear to have landed in the
pinned checkout.

## Benchmark Design Note

For future SWE-style benchmarks, prefer issues that have already been solved,
with a merged fix that provides a clear verifier. Prefer recent solved issues
when possible, so the benchmark is less likely to measure training-data recall.
Unresolved tickets can still be useful as draft benchmarks when comments,
reproducers, or maintainer analysis provide independently checkable verifier
evidence.

## Verifier Evidence

The benchmark prompts do not include the following exact implementation
pointers. They are kept here for author-side verification and grading.

A commenter on the issue reported that `TF_CheckNumericsOp`, the MLIR operation
corresponding to `tf.debugging.check_numerics`, is legalized as a pass-through
operation before HLO execution. They pointed to two TableGen legalization
patterns and the nearby TODO that CheckNumerics is not supported in HLO.

At the pinned TensorFlow commit
`2020b5919c5b66b8672438bed85d0ca88d434438`, the evidence is present at:

- `tensorflow/compiler/mlir/tf2xla/transforms/legalize_tf_patterns.td`
- `tensorflow/compiler/mlir/stablehlo/transforms/legalize_tf_patterns.td`

Both contain a TODO for CheckNumerics HLO support and include
`TF_CheckNumericsOp` in a pattern that replaces selected operations with their
input operand. A strong benchmark answer should discover this or an equivalent
source-level explanation without being given these paths in the prompt.

## Expected High-Level Answer

A strong run should:

- Route the symptom from `tf.function(jit_compile=True)` toward XLA / TF-to-HLO
  lowering rather than eager execution.
- Explain why eager mode and non-XLA graph mode can preserve runtime validation
  while XLA compilation can remove or lower the check differently.
- Identify that the CheckNumerics operation is effectively legalized away in the
  MLIR TF-to-HLO path.
- Cite source evidence from the pinned checkout.
- Propose a next investigation or fix direction, such as implementing HLO-side
  CheckNumerics semantics or preserving an assertion-like runtime check during
  legalization, with tests covering NaN and Inf under `jit_compile=True`.
