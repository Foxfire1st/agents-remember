; Provider-matched highlights query for tree-sitter-hcl.

(comment) @comment

(numeric_lit) @number
(bool_lit) @constant.builtin
(null_lit) @constant.builtin

(string_lit) @string
(heredoc_template) @string

(block (identifier) @keyword)
(function_call (identifier) @function)

[
  "{"
  "}"
  "["
  "]"
  "("
  ")"
] @punctuation.bracket

[
  ","
  "."
] @punctuation.delimiter
