#!/usr/bin/env python3
# codex_spawn_runner.py — 깡코덱스 codex exec를 PARALLEL개 병렬 스폰, 결과를 파일로 회수
# 실행: PARALLEL=4 TASKS=tasks.jsonl OUTDIR=./out SANDBOX=read-only python3 codex_spawn_runner.py
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TASKS    = Path(os.environ.get("TASKS", "tasks.jsonl"))
OUTDIR   = Path(os.environ.get("OUTDIR", "./out"))
PARALLEL = int(os.environ.get("PARALLEL", "4"))        # ← 스폰 수 손잡이
TIMEOUT  = int(os.environ.get("TIMEOUT", "600"))       # 단일 작업 상한(초)
SANDBOX  = os.environ.get("SANDBOX", "read-only")      # read-only|workspace-write|danger-full-access
MODEL    = os.environ.get("MODEL", "")                 # 비우면 codex 기본
BYPASS   = os.environ.get("BYPASS", "0") == "1"        # 1이면 승인·샌드박스 전부 생략

def run_one(item):
    tid = str(item["id"])
    schema = item.get("schema")
    out = OUTDIR / (f"{tid}.json" if schema else f"{tid}.md")
    if out.exists():                                   # resume
        return (tid, "skip", 0)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral",
           "-o", str(out)]
    if BYPASS:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    else:
        cmd += ["-s", SANDBOX]
    if MODEL:
        cmd += ["-m", MODEL]
    if item.get("cwd"):
        cmd += ["-C", item["cwd"]]                     # 격리 디렉토리
    if schema:
        sp = OUTDIR / f".{tid}.schema.json"
        sp.write_text(json.dumps(schema)); cmd += ["--output-schema", str(sp)]
    cmd.append(item["prompt"])
    t0 = time.time()
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           timeout=TIMEOUT, text=True)
    except subprocess.TimeoutExpired:
        return (tid, "timeout", time.time()-t0)
    if out.exists() and out.stat().st_size > 0:
        return (tid, "ok", time.time()-t0)
    err = (r.stderr or "").strip().splitlines()[-1:] or [""]
    return (tid, f"fail({err[0][:100]})", time.time()-t0)

def main():
    items = [json.loads(l) for l in TASKS.read_text().splitlines() if l.strip()]
    items = [it for it in items
             if not (OUTDIR/(f"{it['id']}.json" if it.get('schema') else f"{it['id']}.md")).exists()]
    print(f"[spawn] todo={len(items)} PARALLEL={PARALLEL} sandbox={'bypass' if BYPASS else SANDBOX}", flush=True)
    ok=fail=0; t0=time.time()
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        futs={ex.submit(run_one,it):it["id"] for it in items}
        for f in as_completed(futs):
            tid,status,el=f.result()
            if status=="ok":
                ok+=1; print(f"[ok] {tid} ({el:.0f}s)", flush=True)
            elif status!="skip":
                fail+=1; print(f"[fail#{fail}] {tid} ({el:.0f}s) {status}", flush=True)
    print(f"\n=== done: {ok} ok / {fail} fail / {(time.time()-t0)/60:.1f}min ===")
    return 0 if fail==0 else 1

if __name__=="__main__":
    sys.exit(main())
