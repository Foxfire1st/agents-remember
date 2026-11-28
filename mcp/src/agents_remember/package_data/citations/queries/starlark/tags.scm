; Declarative tags.scm for Starlark / Bazel (language_pack).
(function_definition
  name: (identifier) @name) @definition.function

(module
  (expression_statement
    (assignment
      left: (identifier) @name)) @definition.var)
