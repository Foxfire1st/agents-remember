; Declarative tags.scm for Gleam (language_pack).
(type_definition
  (type_name
    name: (type_identifier) @name)) @definition.type

(type_alias
  (type_name
    name: (type_identifier) @name)) @definition.type

(constant
  name: (identifier) @name) @definition.constant

(function
  name: (identifier) @name) @definition.function
