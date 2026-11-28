; Declarative tree-sitter tags.scm for PHP anchor extraction.

(namespace_definition
  name: (namespace_name) @name) @definition.module

(class_declaration
  name: (name) @name) @definition.class

(interface_declaration
  name: (name) @name) @definition.interface

(trait_declaration
  name: (name) @name) @definition.interface

(enum_declaration
  name: (name) @name) @definition.enum

(function_definition
  name: (name) @name) @definition.function

(method_declaration
  name: (name) @name) @definition.method

(const_declaration
  (const_element
    (name) @name)) @definition.constant
