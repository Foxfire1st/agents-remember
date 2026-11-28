; Declarative tree-sitter tags.scm for C# anchor extraction.

(namespace_declaration
  name: [
    (identifier)
    (qualified_name)
  ] @name) @definition.module

(file_scoped_namespace_declaration
  name: [
    (identifier)
    (qualified_name)
  ] @name) @definition.module

(class_declaration
  name: (identifier) @name) @definition.class

(interface_declaration
  name: (identifier) @name) @definition.interface

(struct_declaration
  name: (identifier) @name) @definition.class

(record_declaration
  name: (identifier) @name) @definition.class

(enum_declaration
  name: (identifier) @name) @definition.enum

(delegate_declaration
  name: (identifier) @name) @definition.type

(method_declaration
  name: (identifier) @name) @definition.method

(constructor_declaration
  name: (identifier) @name) @definition.method

(property_declaration
  name: (identifier) @name) @definition.property

(field_declaration
  (variable_declaration
    (variable_declarator
      name: (identifier) @name))) @definition.field
