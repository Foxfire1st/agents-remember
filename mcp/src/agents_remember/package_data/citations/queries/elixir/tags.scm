; Declarative tree-sitter tags.scm for Elixir anchor extraction.

(call
  target: (identifier) @_kw
  (arguments
    (alias) @name)
  (#any-of? @_kw "defmodule" "defprotocol")) @definition.module

(call
  target: (identifier) @_kw
  (arguments
    [
      (identifier) @name
      (call
        target: (identifier) @name)
      (binary_operator
        left: (call
          target: (identifier) @name)
        operator: "when")
    ])
  (#any-of? @_kw "def" "defp" "defdelegate" "defguard" "defguardp" "defmacro" "defmacrop" "defn" "defnp")) @definition.function
