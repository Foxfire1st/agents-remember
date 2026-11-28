; Provider-matched highlights query for tree-sitter-toml (language_pack).

(comment) @comment

(table (bare_key) @type)
(table (dotted_key) @type)
(table (quoted_key) @type)
(table_array_element (bare_key) @type)
(table_array_element (dotted_key) @type)
(table_array_element (quoted_key) @type)

(pair (bare_key) @property)
(pair (dotted_key) @property)
(pair (quoted_key) @property)

(string) @string
(escape_sequence) @string.escape
(integer) @number
(float) @number
(boolean) @constant.builtin
(offset_date_time) @number
(local_date_time) @number
(local_date) @number
(local_time) @number

[
  "["
  "]"
  "[["
  "]]"
  "{"
  "}"
] @punctuation.bracket

[
  "."
  ","
] @punctuation.delimiter

"=" @operator
