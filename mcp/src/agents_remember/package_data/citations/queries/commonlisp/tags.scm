; Declarative tags.scm for Common Lisp (language_pack).
(defun
  (defun_header
    function_name: (sym_lit) @name)) @definition.function

(list_lit
  .
  (sym_lit) @_kw
  .
  (sym_lit) @name
  (#any-of? @_kw "defvar" "defparameter" "defconstant" "defstruct" "defclass")) @definition.var
