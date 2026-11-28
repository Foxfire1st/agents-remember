; Declarative tree-sitter tags.scm for Swift anchor extraction.
; Extension blocks bind @container so Type.method resolves without duplicating Type itself.

(class_declaration
  name: (user_type
    (type_identifier) @container)
  body: (class_body
    (function_declaration
      name: (simple_identifier) @name) @definition.method))

(class_declaration
  name: (user_type
    (type_identifier) @container)
  body: (class_body
    (property_declaration
      name: (pattern
        bound_identifier: (simple_identifier) @name)) @definition.property))

(class_declaration
  name: (type_identifier) @name) @definition.class

(protocol_declaration
  name: (type_identifier) @name) @definition.interface

(typealias_declaration
  name: (type_identifier) @name) @definition.type

(function_declaration
  name: (simple_identifier) @name) @definition.function

(protocol_function_declaration
  name: (simple_identifier) @name) @definition.method

(source_file
  (property_declaration
    name: (pattern
      bound_identifier: (simple_identifier) @name)) @definition.var)

(class_body
  (property_declaration
    name: (pattern
      bound_identifier: (simple_identifier) @name)) @definition.property)
