; Declarative tree-sitter tags.scm for Go anchor extraction.
; Captures receiver types on method_declaration so Type.Method qualifiers resolve cleanly.

(method_declaration
  receiver: (parameter_list
    (parameter_declaration
      type: [
        (type_identifier) @container
        (pointer_type
          (type_identifier) @container)
        (generic_type
          type: (type_identifier) @container)
        (pointer_type
          (generic_type
            type: (type_identifier) @container))
      ]))
  name: (field_identifier) @name) @definition.method

(function_declaration
  name: (identifier) @name) @definition.function

(type_spec
  name: (type_identifier) @name) @definition.type

(type_alias
  name: (type_identifier) @name) @definition.type

(interface_type
  (method_elem
    name: (field_identifier) @name) @definition.method)

(source_file
  (const_declaration
    (const_spec
      name: (identifier) @name) @definition.constant))

(source_file
  (var_declaration
    (var_spec
      name: (identifier) @name) @definition.var))
