; Declarative tags.scm for Lua (language_pack).
(function_declaration
  name: [
    (identifier) @name
    (dot_index_expression
      table: (_) @container
      field: (identifier) @name)
    (method_index_expression
      table: (_) @container
      method: (identifier) @name)
  ]) @definition.function
