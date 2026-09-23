#!/usr/bin/env bash
# The candidate fingerprint recipe for leaf 260921-ICR-L31, stated so anyone can recompute it.
#
# Three readings, in this order, and nothing else:
#   1. `git diff | sha256sum` — the tracked delta. It is BLIND to untracked files (the L21 lesson), so
#      it is never read alone.
#   2. `git status --porcelain` — the entry count and the LC_ALL=C-sorted path list. This is what makes
#      a new untracked file visible, and it names every path including this leaf's evidence.
#   3. one `sha256sum` per **deliverable** changed/untracked file, rendered `<sha256>  <path>`,
#      LC_ALL=C sorted by path, joined by newlines and hashed whole. Exclusions: `temp/` (this leaf's
#      evidence, which is not part of the deliverable and is named by reading 2 anyway) and
#      `__pycache__/` + `*.pyc` (build artifacts). Nothing else is excluded.
#
# Readings 1 and 3 are the reproducibility claim: two runs with no writes between them print the same
# values. Reading 2 is the candidate-moved witness.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "== 1. tracked delta =="
git diff | sha256sum

echo "== 2. status entries =="
git status --porcelain | LC_ALL=C sort
printf 'status_entry_count=%s\n' "$(git status --porcelain | wc -l)"

echo "== 3. deliverable per-file digests, sorted by path =="
{
  git status --porcelain \
    | awk '{ $1=""; sub(/^ +/, ""); print }' \
    | grep -v -e '^temp/' -e '__pycache__' -e '\.pyc$' \
    | LC_ALL=C sort \
    | while IFS= read -r path; do
        [ -f "$path" ] && sha256sum -- "$path"
      done
} > /tmp/l31-per-file-digests.txt
cat /tmp/l31-per-file-digests.txt
sha256sum < /tmp/l31-per-file-digests.txt
