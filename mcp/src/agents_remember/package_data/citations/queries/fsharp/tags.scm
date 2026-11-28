; Declarative tags.scm for F# scripts and signatures (language_pack; .fs is ambiguous).
(named_module
  name: (long_identifier) @name) @definition.module

(union_type_defn
  (type_name
    type_name: (identifier) @name)) @definition.type

(record_type_defn
  (type_name
    type_name: (identifier) @name)) @definition.type

(function_or_value_defn
  (function_declaration_left
    (identifier) @name)) @definition.function
