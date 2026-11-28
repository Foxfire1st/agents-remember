; Declarative tree-sitter tags.scm for Ruby anchor extraction.
; Distinguishes instance methods (@definition.method -> #) from singleton methods (. qualifier).

(module
  name: [
    (constant)
    (scope_resolution)
  ] @name) @definition.module

(class
  name: [
    (constant)
    (scope_resolution)
  ] @name) @definition.class

(method
  name: [
    (identifier)
    (operator)
    (setter)
  ] @name) @definition.method

(singleton_method
  name: [
    (identifier)
    (operator)
    (setter)
  ] @name) @definition.singleton_method

(assignment
  left: (constant) @name) @definition.constant
