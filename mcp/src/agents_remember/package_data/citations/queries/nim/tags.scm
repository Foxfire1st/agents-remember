; Declarative tags.scm for Nim (language_pack).
(type_declaration
  (type_symbol_declaration
    name: (identifier) @name)) @definition.type

(proc_declaration
  name: (identifier) @name) @definition.function

(func_declaration
  name: (identifier) @name) @definition.function

(method_declaration
  name: (identifier) @name) @definition.method

(iterator_declaration
  name: (identifier) @name) @definition.function

(template_declaration
  name: (identifier) @name) @definition.macro

(macro_declaration
  name: (identifier) @name) @definition.macro
