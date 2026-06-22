#!/usr/bin/env bash
set -euo pipefail
# tasks.jsonl: {"id","prompt","cwd"}
mkdir -p out
jq -c '.' tasks.jsonl | xargs -P 4 -I{} bash -c '
  j={}; id=$(jq -r .id <<<"$j"); cwd=$(jq -r ".cwd // \".\"" <<<"$j")
  out="out/$id.md"; [ -s "$out" ] && exit 0           # resume
  codex exec --skip-git-repo-check --ephemeral -s read-only -C "$cwd" \
    -o "$out" "$(jq -r .prompt <<<"$j")" </dev/null >/dev/null 2>&1 \
    && echo "OK $id" || echo "FAIL $id"
'
