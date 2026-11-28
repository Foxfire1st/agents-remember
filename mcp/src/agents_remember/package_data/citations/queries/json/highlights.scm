; Provider-matched highlights query for tree-sitter-json (language_pack).
; Property keys use @string.special.key after @string so the precedence sweep
; keeps keys in base ink while string values stay mint.

(string) @string
(escape_sequence) @string.escape
(pair key: (string) @string.special.key)

(number) @number
(true) @constant.builtin
(false) @constant.builtin
(null) @constant.builtin

(comment) @comment

[
  "{"
  "}"
  "["
  "]"
] @punctuation.bracket

[
  ","
  ":"
] @punctuation.delimiter
