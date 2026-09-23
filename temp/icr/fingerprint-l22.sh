#!/bin/sh
# The L22 candidate fingerprint, literally. Run from the code worktree root.
#   component 1: sha256 of `git diff`                      -> "<hex><space><space>-"
#   component 2: `git status --porcelain`, filtered, LC_ALL=C sorted -> "<XY><space><path>" per line
#   component 3: for each path in that same filtered list whose XY is "??", its own sha256 record as
#                `sha256sum` prints it ("<hex><space><space><path>"), in that same sorted order
#   combined   : sha256 of the newline-joined concatenation of 1 then 2 then 3. No labels, no blank
#                lines, no trailing separator added by hand; each component already ends in a newline.
# Excluded from components 2 and 3: this leaf's own report (temp/icr/report-l22.md) and the verifier's
# verdict documents (temp/icr/verify-l22*.md) -- documents about the candidate, not candidate bytes.
EXCLUDE='temp/icr/report-l22.md|temp/icr/verify-l22.*\.md'
{
  git diff | sha256sum
  git status --porcelain | grep -Ev "$EXCLUDE" | LC_ALL=C sort
  git status --porcelain | awk '$1=="??"{print $2}' | grep -Ev "$EXCLUDE" | LC_ALL=C sort | xargs -r sha256sum
} | sha256sum
