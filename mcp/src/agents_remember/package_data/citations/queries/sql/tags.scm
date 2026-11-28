; Declarative tree-sitter tags.scm for SQL DDL anchor extraction.
; Engine-side policy widens statement wrappers and resolves schema.object qualifiers.

(create_table
  (object_reference
    schema: (identifier) @container
    name: (identifier) @name)) @definition.class

(create_table
  (object_reference
    !schema
    name: (identifier) @name)) @definition.class

(create_view
  (object_reference
    schema: (identifier) @container
    name: (identifier) @name)) @definition.class

(create_view
  (object_reference
    !schema
    name: (identifier) @name)) @definition.class

(create_materialized_view
  (object_reference
    schema: (identifier) @container
    name: (identifier) @name)) @definition.class

(create_materialized_view
  (object_reference
    !schema
    name: (identifier) @name)) @definition.class

(create_index
  column: (identifier) @name) @definition.var

(create_function
  (object_reference
    schema: (identifier) @container
    name: (identifier) @name)) @definition.function

(create_function
  (object_reference
    !schema
    name: (identifier) @name)) @definition.function

(create_trigger
  (object_reference
    schema: (identifier) @container
    name: (identifier) @name)) @definition.function

(create_trigger
  (object_reference
    !schema
    name: (identifier) @name)) @definition.function

(create_type
  (object_reference
    schema: (identifier) @container
    name: (identifier) @name)) @definition.type

(create_type
  (object_reference
    !schema
    name: (identifier) @name)) @definition.type

(create_schema
  (identifier) @name) @definition.module
