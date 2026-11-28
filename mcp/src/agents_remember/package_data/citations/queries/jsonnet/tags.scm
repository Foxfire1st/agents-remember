; Declarative tags.scm for Jsonnet (language_pack).
(bind
  function: (id) @name) @definition.function

(bind
  (id) @name) @definition.var

(field
  function: (fieldname
    (id) @name)) @definition.method
