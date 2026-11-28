; Declarative tree-sitter tags.scm for Bash/Shell anchor extraction.

(function_definition
  name: (word) @name) @definition.function

(program
  (variable_assignment
    name: (variable_name) @name) @definition.var)

(program
  (declaration_command
    (variable_assignment
      name: (variable_name) @name)) @definition.var)
