; Standard tree-sitter tags.scm captures for JavaScript and JSX anchor extraction.
; Engine-side policy handles export widening (export_statement),
; declaration line tracking, bound-side destructuring, and has_error filtering.

(class_declaration
  name: (_) @name) @definition.class

(function_declaration
  name: (_) @name) @definition.function

(generator_function_declaration
  name: (_) @name) @definition.function

(method_definition
  name: (_) @name) @definition.method

(variable_declarator
  name: [
    (identifier)
    (array_pattern)
    (object_pattern)
  ] @name) @definition.var
