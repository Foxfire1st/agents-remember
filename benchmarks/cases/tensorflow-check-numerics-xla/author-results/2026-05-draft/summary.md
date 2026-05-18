# Draft Author Result Notes

This draft case captures a TensorFlow issue-triage benchmark based on GitHub
issue #117319, where `tf.debugging.check_numerics` silently passes NaN/Inf under
`tf.function(jit_compile=True)`.

The prompt intentionally withholds exact implementation file pointers. The case
is meant to measure whether Agents Remember memory helps a run orient through a
large codebase from ticket symptoms and broad subsystem clues.

Expected answer shape:

- Recognize that `jit_compile=True` routes the behavior through XLA-related
  TensorFlow lowering rather than ordinary eager execution.
- Discover source evidence that the CheckNumerics operation is legalized away in
  TF-to-HLO lowering.
- Explain why that would remove runtime NaN/Inf validation.
- Propose an investigation or fix direction and tests for NaN and Inf under
  `jit_compile=True`.

This draft does not yet include raw JSONL-backed official result tables. Add
those under this result set when the case is promoted from draft to published.
