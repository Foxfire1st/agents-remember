; Standard tree-sitter tags.scm captures for Python anchor extraction.
; Engine-side policy handles decorator widening (decorated_definition),
; declaration line tracking, bound-side tuple/list unpacking, and has_error filtering.

(function_definition
  name: (identifier) @name) @definition.function

(class_definition
  name: (identifier) @name) @definition.class

(assignment
  left: [
    (identifier)
    (pattern_list)
    (tuple_pattern)
    (list_pattern)
  ] @name) @definition.var

(augmented_assignment
  left: [
    (identifier)
    (pattern_list)
    (tuple_pattern)
    (list_pattern)
  ] @name) @definition.var
