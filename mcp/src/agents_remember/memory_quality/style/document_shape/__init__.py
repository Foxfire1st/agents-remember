"""Checks on the rendered shape of a memory document, as opposed to its content.

Two defects live here, both of which make a document render as something other than what
it says. A leaked ``+`` at column zero turns a heading into literal text or a wrapped
sentence into a list item; a table row with the wrong cell count silently drops or
misaligns a column. Neither is visible to a reader of the source, which is why both
survived every closeout until they were measured.

Both read the same tokenizer, ``inline_scan``, because both have to know where a code
span is before they can say what a character means.
"""
