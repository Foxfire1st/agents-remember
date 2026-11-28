; Provider-matched highlights query for tree-sitter-markdown (language_pack).
; Fenced code block bodies use @none so the precedence sweep suppresses the
; enclosing @text.literal span inside code fence content.

(atx_heading) @text.title
(setext_heading) @text.title

(atx_h1_marker) @punctuation.special
(atx_h2_marker) @punctuation.special
(atx_h3_marker) @punctuation.special
(atx_h4_marker) @punctuation.special
(atx_h5_marker) @punctuation.special
(atx_h6_marker) @punctuation.special
(setext_h1_underline) @punctuation.special
(setext_h2_underline) @punctuation.special

(fenced_code_block) @text.literal
(code_fence_content) @none
(fenced_code_block_delimiter) @punctuation.delimiter
(info_string (language) @type)
(indented_code_block) @text.literal

(link_label) @text.reference
(link_destination) @text.uri
(link_title) @string

(block_quote_marker) @punctuation.special
(list_marker_plus) @punctuation.special
(list_marker_minus) @punctuation.special
(list_marker_star) @punctuation.special
(list_marker_dot) @punctuation.special
(list_marker_parenthesis) @punctuation.special
(thematic_break) @punctuation.delimiter
(html_block) @comment
