; Declarative tree-sitter tags.scm for HCL / Terraform anchor extraction.

(block
  (identifier) @_kind
  .
  (string_lit
    (template_literal) @container)
  .
  (string_lit
    (template_literal) @name)
  (#any-of? @_kind "resource" "data")) @definition.class

(block
  (identifier) @container
  .
  (string_lit
    (template_literal) @name)
  .
  (block_start)
  (#any-of? @container "variable" "output" "module" "provider")) @definition.var

(block
  (identifier) @container
  .
  (block_start)
  (body
    (attribute
      (identifier) @name) @definition.var)
  (#eq? @container "locals"))
