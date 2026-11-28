; Declarative tags.scm for GLSL (language_pack).
(struct_specifier
  name: (type_identifier) @name
  body: (field_declaration_list)) @definition.class

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @name)) @definition.function
