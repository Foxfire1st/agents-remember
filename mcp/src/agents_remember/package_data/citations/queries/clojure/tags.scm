; Declarative tags.scm for Clojure (language_pack).
(list_lit
  .
  (sym_lit
    name: (sym_name) @_kw)
  .
  (sym_lit
    name: (sym_name) @name)
  (#any-of? @_kw "defn" "defn-" "defmacro" "def" "defonce" "defprotocol" "defrecord" "deftype" "defmulti")) @definition.function
