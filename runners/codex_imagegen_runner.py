#!/usr/bin/env python3
# codex_spawn_runner.py — codex exec를 PARALLEL개 백그라운드 스폰 → 레이스-세이프 회수
# 실행: PARALLEL=3 PROMPTS=prompts.jsonl OUTDIR=./out python3 codex_spawn_runner.py
import json, os, subprocess, sys, time, threading, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROMPTS  = Path(os.environ.get("PROMPTS", "prompts.jsonl"))
OUTDIR   = Path(os.environ.get("OUTDIR", "./out"))
PARALLEL = int(os.environ.get("PARALLEL", "3"))      # ← 스폰 수 손잡이
TIMEOUT  = int(os.environ.get("TIMEOUT", "240"))     # 단일 호출 상한(초)
CODEX_IMG = Path.home() / ".codex" / "generated_images"

_lock = threading.Lock()
_claimed: set[str] = set()   # 이미 회수한 png 경로 — 레이스 방지 핵심

def newest_unclaimed(after_ts: float):
    """after_ts 이후 mtime인 ig_*.png 중 아직 안 집은 최신 1개를 원자적으로 점유."""
    with _lock:
        best = None
        if CODEX_IMG.exists():
            for sess in CODEX_IMG.iterdir():
                if not sess.is_dir():
                    continue
                for png in sess.glob("ig_*.png"):
                    p = str(png)
                    if p in _claimed:
                        continue
                    try:
                        m = png.stat().st_mtime
                    except OSError:
                        continue
                    if m > after_ts and (best is None or m > best[1]):
                        best = (png, m)
        if best:
            _claimed.add(str(best[0]))
            return best[0]
        return None

def run_one(item):
    pid = item["id"]
    out = OUTDIR / item["output_path"]
    if out.exists():                      # resume: 이미 있으면 스킵
        return (pid, "skip", 0)
    instr = (f"Use $imagegen to generate ONE image.\n"
             f"Aspect ratio: {item.get('ar','1:1')}\n"
             f"Size: {item.get('size','1024x1536')}\n"
             f"Prompt: {item['prompt']}\n"
             f"After generation, do NOT run any shell commands. Just generate and end your turn.")
    before = time.time() - 1              # 약간의 시계 오차 마진
    try:
        subprocess.run(
            ["codex","exec","--skip-git-repo-check",
             "--dangerously-bypass-approvals-and-sandbox", instr],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return (pid, "timeout", time.time()-before)
    # 회수: 폴링하며 내 호출 이후 신규 png를 점유
    deadline = time.time() + 30
    src = None
    while time.time() < deadline:
        src = newest_unclaimed(before)
        if src:
            break
        time.sleep(1)
    if not src:
        return (pid, "rejected/no-image", time.time()-before)  # 대개 모더레이션 거부
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(out))
    return (pid, "ok", time.time()-before)

def main():
    items = [json.loads(l) for l in PROMPTS.read_text().splitlines() if l.strip()]
    items = [it for it in items if not (OUTDIR/it["output_path"]).exists()]  # resume
    print(f"[spawn] todo={len(items)} PARALLEL={PARALLEL} timeout={TIMEOUT}s", flush=True)
    ok=fail=0; t0=time.time()
    with ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        futs={ex.submit(run_one,it):it["id"] for it in items}
        for f in as_completed(futs):
            pid,status,el=f.result()
            if status=="ok":
                ok+=1
                if ok%10==0:
                    rate=ok/max(time.time()-t0,1)*60
                    print(f"[progress] {ok} ok · {rate:.1f}/min", flush=True)
            elif status!="skip":
                fail+=1
                print(f"[fail#{fail}] {pid} ({el:.0f}s) {status}", flush=True)
    print(f"\n=== done: {ok} ok / {fail} fail / {(time.time()-t0)/60:.1f}min ===")
    return 0 if fail==0 else 1

if __name__=="__main__":
    sys.exit(main())
