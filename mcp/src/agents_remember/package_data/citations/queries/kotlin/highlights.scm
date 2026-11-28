; Provider-matched highlights query for tree-sitter-kotlin.

(line_comment) @comment
(block_comment) @comment

(string_literal) @string
(multiline_string_literal) @string
(character_literal) @string
(number_literal) @number
(float_literal) @number

(user_type (identifier) @type)
(function_declaration name: (identifier) @function)
(class_declaration name: (identifier) @type)
(object_declaration name: (identifier) @type)

[
  "as"
  "by"
  "catch"
  "class"
  "companion"
  "data"
  "do"
  "else"
  "enum"
  "finally"
  "for"
  "fun"
  "if"
  "import"
  "in"
  "init"
  "interface"
  "is"
  "object"
  "override"
  "package"
  "private"
  "protected"
  "public"
  "return"
  "sealed"
  "super"
  "suspend"
  "this"
  "throw"
  "try"
  "typealias"
  "val"
  "var"
  "when"
  "where"
  "while"
] @keyword

[
  "("
  ")"
  "{"
  "}"
  "["
  "]"
] @punctuation.bracket

[
  ","
  "."
  ":"
  ";"
] @punctuation.delimiter
