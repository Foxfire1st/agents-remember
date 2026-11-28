; Declarative tree-sitter tags.scm for C++ anchor extraction.
; Engine-side policy widens template_declaration wrappers and resolves :: qualifiers.

(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier
      scope: (_) @container
      name: [
        (identifier)
        (destructor_name)
      ] @name))) @definition.method

(function_definition
  declarator: (pointer_declarator
    declarator: (function_declarator
      declarator: (qualified_identifier
        scope: (_) @container
        name: [
          (identifier)
          (destructor_name)
        ] @name)))) @definition.method

(function_definition
  declarator: (function_declarator
    declarator: [
      (identifier)
      (field_identifier)
      (destructor_name)
    ] @name)) @definition.function

(function_definition
  declarator: (pointer_declarator
    declarator: (function_declarator
      declarator: [
        (identifier)
        (field_identifier)
      ] @name))) @definition.function

(namespace_definition
  name: (namespace_identifier) @name) @definition.module

(class_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @definition.class

(struct_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @definition.class

(union_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @definition.class

(enum_specifier
  name: (type_identifier) @name
  body: (enumerator_list)) @definition.enum

(alias_declaration
  name: (type_identifier) @name) @definition.type

(type_definition
  declarator: (type_identifier) @name) @definition.type

(preproc_def
  name: (identifier) @name) @definition.macro

(preproc_function_def
  name: (identifier) @name) @definition.macro

(translation_unit
  (declaration
    declarator: (init_declarator
      declarator: (identifier) @name)) @definition.var)

(namespace_definition
  body: (declaration_list
    (declaration
      declarator: (init_declarator
        declarator: (identifier) @name)) @definition.var))
