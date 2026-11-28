; Declarative tags.scm for Elm (language_pack).
(type_declaration
  name: (upper_case_identifier) @name) @definition.type

(type_alias_declaration
  name: (upper_case_identifier) @name) @definition.type

(value_declaration
  functionDeclarationLeft: (function_declaration_left
    (lower_case_identifier) @name)) @definition.function
