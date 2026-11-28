; Declarative tree-sitter tags.scm for Rust anchor extraction.
; Engine-side policy widens preceding attribute_item siblings and resolves :: qualifiers.

(impl_item
  type: (type_identifier) @container
  body: (declaration_list
    (function_item
      name: (identifier) @name) @definition.method))

(impl_item
  type: (generic_type
    type: (type_identifier) @container)
  body: (declaration_list
    (function_item
      name: (identifier) @name) @definition.method))

(impl_item
  type: (scoped_type_identifier
    name: (type_identifier) @container)
  body: (declaration_list
    (function_item
      name: (identifier) @name) @definition.method))

(impl_item
  type: (type_identifier) @container
  body: (declaration_list
    (const_item
      name: (identifier) @name) @definition.constant))

(impl_item
  type: (type_identifier) @container
  body: (declaration_list
    (type_item
      name: (type_identifier) @name) @definition.type))

(function_item
  name: (identifier) @name) @definition.function

(function_signature_item
  name: (identifier) @name) @definition.method

(struct_item
  name: (type_identifier) @name) @definition.class

(enum_item
  name: (type_identifier) @name) @definition.enum

(union_item
  name: (type_identifier) @name) @definition.class

(trait_item
  name: (type_identifier) @name) @definition.interface

(type_item
  name: (type_identifier) @name) @definition.type

(mod_item
  name: (identifier) @name) @definition.module

(const_item
  name: (identifier) @name) @definition.constant

(static_item
  name: (identifier) @name) @definition.var

(macro_definition
  name: (identifier) @name) @definition.macro
