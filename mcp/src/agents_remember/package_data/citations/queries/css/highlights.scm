; Provider-matched highlights query for tree-sitter-css (language_pack).

(comment) @comment

(tag_name) @tag
(class_name) @type
(id_name) @type
(namespace_name) @type
(pseudo_class_selector (class_name) @function)
(pseudo_element_selector (tag_name) @function)
(attribute_name) @attribute

(property_name) @property
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
  "@charset"
  "@namespace"
  "@supports"
  "@keyframes"
  "@scope"
  "and"
  "or"
  "not"
  "only"
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
