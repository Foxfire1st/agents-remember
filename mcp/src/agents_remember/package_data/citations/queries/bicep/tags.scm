; Declarative tags.scm for Bicep (language_pack).
(parameter_declaration
  (identifier) @name) @definition.var

(variable_declaration
  (identifier) @name) @definition.var

(resource_declaration
  (identifier) @name) @definition.class

(module_declaration
  (identifier) @name) @definition.module

(output_declaration
  (identifier) @name) @definition.var
