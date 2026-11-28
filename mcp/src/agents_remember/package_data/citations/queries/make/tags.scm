; Declarative tags.scm for GNU Make (language_pack, best_effort targets & vars).
(variable_assignment
  name: (word) @name) @definition.var

(rule
  (targets
    (word) @name)) @definition.function
