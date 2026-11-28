; Declarative tree-sitter tags.scm for Kotlin anchor extraction.

(class_declaration
  name: (identifier) @name) @definition.class

(object_declaration
  name: (identifier) @name) @definition.class

(function_declaration
  name: (identifier) @name) @definition.function

(type_alias
  type: (identifier) @name) @definition.type

(source_file
  (property_declaration
    (variable_declaration
      (identifier) @name)) @definition.var)

(class_body
  (property_declaration
    (variable_declaration
      (identifier) @name)) @definition.field)
