; Declarative tree-sitter tags.scm for Zig anchor extraction.
; Excludes @import bindings so imports are never falsely anchored as definitions.

(function_declaration
  name: (identifier) @name) @definition.function

(variable_declaration
  (identifier) @name
  [
    (struct_declaration)
    (enum_declaration)
    (union_declaration)
    (opaque_declaration)
    (error_set_declaration)
  ]) @definition.class

(source_file
  (variable_declaration
    (identifier) @name
    .
    [
      (integer)
      (float)
      (string)
      (character)
      (boolean)
      (identifier)
      (binary_expression)
      (unary_expression)
      (initializer_list)
      (struct_initializer)
      (field_expression)
    ]) @definition.constant)

(source_file
  (variable_declaration
    (identifier) @name
    type: (_)
    [
      (integer)
      (float)
      (string)
      (character)
      (boolean)
      (identifier)
      (binary_expression)
      (unary_expression)
      (initializer_list)
      (struct_initializer)
      (field_expression)
    ]) @definition.constant)
