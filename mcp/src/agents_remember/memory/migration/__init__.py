"""Staged legacy migration: the scope inventory, the parser, the mappings and the census.

This package reads the Markdown-era onboarding corpus and describes it. It **writes nothing to the
corpus**: an artifact is read, parsed into claims and dispositions, and reported against a frozen
baseline. The one write path it uses is the shipped candidate batch operation, through the census
record group in :mod:`agents_remember.memory.knowledge.census_records`.

It is a subpackage of ``memory`` rather than a new top-level package because it ranks with the thing
it measures: it reads the memory repository's onboarding tree, and ``layers.toml`` puts
``memory_quality`` below ``memory`` because reading and judging memory content is what both do.

Nothing here interprets. The parser reports what it read; a mapping names where a read artifact goes;
a reference resolves to one of three recorded states; a mismatch is reported as the mechanical fact
it is. Semantic categories, verdicts and mismatch classes are authored by a curator, in the record
kinds other leaves own, and this package only reads them.
"""
