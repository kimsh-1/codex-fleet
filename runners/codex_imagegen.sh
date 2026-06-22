#!/usr/bin/env bash
set -euo pipefail
# prompts.jsonl: 한 줄당 {"id","prompt","size","output_path"}
jq -c '.' prompts.jsonl | xargs -P 3 -I{} bash -c '
  j={}; id=$(jq -r .id <<<"$j"); out=$(jq -r .output_path <<<"$j")
  [ -f "$out" ] && exit 0                       # resume
  before=$(date +%s)
  codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
    "Use \$imagegen to generate ONE image. Size: $(jq -r .size <<<"$j"). Prompt: $(jq -r .prompt <<<"$j"). Do not run shell. End turn." \
    </dev/null >/dev/null 2>&1
  png=$(find ~/.codex/generated_images -name "ig_*.png" -newermt "@$before" | head -1)
  [ -n "$png" ] && { mkdir -p "$(dirname "$out")"; mv "$png" "$out"; echo "OK $id"; } || echo "FAIL $id"
'
# ⚠️ 이 bash판은 §3 레이스를 막지 못함 — PARALLEL>1이면 파이썬 러너(claimed 락) 권장
