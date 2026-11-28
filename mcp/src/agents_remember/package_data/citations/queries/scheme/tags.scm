; Declarative tags.scm for Scheme (language_pack; .scm is ambiguous with tree-sitter queries).
(list
  .
  (symbol) @_kw
  .
  (list
    .
    (symbol) @name)
  (#any-of? @_kw "define" "define-syntax")) @definition.function

(list
  .
  (symbol) @_kw
  .
  (symbol) @name
  (#any-of? @_kw "define" "define-syntax")) @definition.var
