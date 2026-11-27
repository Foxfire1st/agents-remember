; Standard tree-sitter tags.scm captures for TypeScript and TSX anchor extraction.
; Engine-side policy handles export/ambient widening (export_statement, ambient_declaration),
; declaration line tracking, bound-side destructuring, and has_error filtering.

(abstract_class_declaration
  name: (_) @name) @definition.class

(class_declaration
  name: (_) @name) @definition.class

(interface_declaration
  name: (_) @name) @definition.interface

(type_alias_declaration
  name: (_) @name) @definition.type

(enum_declaration
  name: (_) @name) @definition.enum

(internal_module
  name: (_) @name) @definition.module

(function_declaration
  name: (_) @name) @definition.function

(function_signature
  name: (_) @name) @definition.function

(generator_function_declaration
  name: (_) @name) @definition.function

(method_definition
  name: (_) @name) @definition.method

(method_signature
  name: (_) @name) @definition.method

(abstract_method_signature
  name: (_) @name) @definition.method

(property_signature
  name: (_) @name) @definition.property

(public_field_definition
  name: (_) @name) @definition.field

(variable_declarator
  name: [
    (identifier)
    (array_pattern)
    (object_pattern)
  ] @name) @definition.var
