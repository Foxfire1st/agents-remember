; Declarative tags.scm for Racket (language_pack).
(list
  .
  (symbol) @_kw
  .
  (list
    .
    (symbol) @name)
  (#any-of? @_kw "define" "define/contract" "define-syntax")) @definition.function

(list
  .
  (symbol) @_kw
  .
  (symbol) @name
  (#any-of? @_kw "define" "struct" "define-struct")) @definition.var
