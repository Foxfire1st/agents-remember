; Declarative tree-sitter tags.scm for C anchor extraction.

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)) @definition.function

(function_definition
  declarator: (pointer_declarator
    declarator: (function_declarator
      declarator: (identifier) @name))) @definition.function

(function_definition
  declarator: (pointer_declarator
    declarator: (pointer_declarator
      declarator: (function_declarator
        declarator: (identifier) @name)))) @definition.function

(struct_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @definition.class

(union_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @definition.class

(enum_specifier
  name: (type_identifier) @name
  body: (enumerator_list)) @definition.enum

(type_definition
  declarator: (type_identifier) @name) @definition.type

(type_definition
  declarator: (pointer_declarator
    declarator: (type_identifier) @name)) @definition.type

(preproc_def
  name: (identifier) @name) @definition.macro

(preproc_function_def
  name: (identifier) @name) @definition.macro

(translation_unit
  (declaration
    declarator: (init_declarator
      declarator: (identifier) @name)) @definition.var)

(translation_unit
  (declaration
    declarator: (init_declarator
      declarator: (pointer_declarator
        declarator: (identifier) @name))) @definition.var)
