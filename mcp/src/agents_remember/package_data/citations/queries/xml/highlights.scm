; Provider-matched highlights query for tree-sitter-xml (language_pack).

(Comment) @comment
(STag (Name) @tag)
(ETag (Name) @tag)
(EmptyElemTag (Name) @tag)
(Attribute (Name) @attribute)
(AttValue) @string
(EntityRef) @constant.builtin
(CharRef) @constant.builtin
(CDSect) @string
(PI) @keyword

[
  "<"
  ">"
  "</"
  "/>"
] @punctuation.bracket

"=" @operator
