; Provider-matched highlights query for tree-sitter-scss (language_pack).

(comment) @comment
(js_comment) @comment

(tag_name) @tag
(class_name) @type
(id_name) @type
(attribute_name) @attribute
(property_name) @property
(variable) @variable

(string_value) @string
(color_value) @number
(integer_value) @number
(float_value) @number
(unit) @type
(plain_value) @constant
(important) @keyword

(call_expression (function_name) @function)

[
  "@media"
  "@import"
  "@mixin"
  "@include"
  "@extend"
  "@if"
  "@else"
  "@for"
  "@each"
  "@while"
  "@function"
  "@return"
  "@use"
  "@forward"
] @keyword

[
  "{"
  "}"
  "("
  ")"
  "["
  "]"
] @punctuation.bracket

[
  ":"
  ";"
  ","
] @punctuation.delimiter
