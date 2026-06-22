#!/usr/bin/env python3
# codex_spawn_runner.py — 깡코덱스 codex exec를 자동 스케일링 병렬 스폰, 결과를 파일로 회수
# 실행: TASKS=tasks.jsonl OUTDIR=./out SANDBOX=read-only python3 codex_spawn_runner.py
#   PARALLEL=auto(기본) : 작업 수에 맞춰 워커 자동 산정 + 헬시하면 상한까지 램프업
#   PARALLEL=4          : 수동 고정.   MAX=12 : 자동 상한 override.   START=3 : 시작 워커.
import json, os, subprocess, sys, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

TASKS    = Path(os.environ.get("TASKS", "tasks.jsonl"))
OUTDIR   = Path(os.environ.get("OUTDIR", "./out"))
PARALLEL = os.environ.get("PARALLEL", "auto")
TIMEOUT  = int(os.environ.get("TIMEOUT", "600"))       # 단일 작업 상한(초)
SANDBOX  = os.environ.get("SANDBOX", "read-only")      # read-only|workspace-write|danger-full-access
MODEL    = os.environ.get("MODEL", "")                 # 비우면 codex 기본
BYPASS   = os.environ.get("BYPASS", "0") == "1"        # 1이면 승인·샌드박스 전부 생략
RAMP_EVERY = int(os.environ.get("RAMP_EVERY", "5"))    # 성공 N건마다 워커 +1

def auto_target(todo):
    """작업 수 → 목표 워커. codex exec는 I/O 바운드(이미지 API 대기 ~30-45s)라 CPU가
    아니라 RAM·계정(250 IPM)이 한도 → 여유 RAM 기반 캡(HARD_CAP 천장 32). MAX로 직접 고정.
    예: 여유 6GB·0.4GB/proc → ~15. 큰 머신이면 HARD_CAP까지. 계정은 보통 헤드룸 큼."""
    cap = int(os.environ.get("MAX", "0"))
    if not cap:
        try:
            free_gb = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / 1e9
            per = float(os.environ.get("RAM_PER_PROC_GB", "0.4"))
            cap = max(2, int(free_gb / per))
        except (ValueError, OSError, AttributeError):
            cap = (os.cpu_count() or 4) * 2          # 비리눅스 폴백(I/O 바운드라 코어×2)
        cap = min(int(os.environ.get("HARD_CAP", "32")), cap)
    return max(1, min(todo, cap))

class AutoScaler:
    """start 워커로 시작해 헬시하면 RAMP_EVERY 성공마다 +1, target까지. 스로틀 시 성장 정지."""
    def __init__(self, target):
        self.target = target
        self.permits = max(1, min(int(os.environ.get("START", "3")), target))
        self.sem = threading.Semaphore(self.permits)
        self.lock = threading.Lock()
        self.ok = 0
    def __enter__(self): self.sem.acquire(); return self
    def __exit__(self, *a): self.sem.release()
    def live(self): return self.permits
    def success(self):
        with self.lock:
            if self.permits < self.target:
                self.ok += 1
                if self.ok >= RAMP_EVERY:
                    self.ok = 0; self.permits += 1; self.sem.release()
    def throttle(self):
        with self.lock:
            self.ok = 0

def _is_throttle(err):
    e = (err or "").lower()
    return "429" in e or "rate limit" in e or "too many requests" in e

def run_one(item, scaler):
    tid = str(item["id"])
    schema = item.get("schema")
    out = OUTDIR / (f"{tid}.json" if schema else f"{tid}.md")
    if out.exists():                                   # resume
        return (tid, "skip", 0)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-o", str(out)]
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
    with scaler:                                       # 동시 실행 슬롯(천장은 스케일러가 조절)
        t0 = time.time()
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               timeout=TIMEOUT, text=True)
        except subprocess.TimeoutExpired:
            return (tid, "timeout", time.time()-t0)
        if _is_throttle(r.stderr): scaler.throttle()
    if out.exists() and out.stat().st_size > 0:
        scaler.success()
        return (tid, "ok", time.time()-t0)
    err = (r.stderr or "").strip().splitlines()[-1:] or [""]
    return (tid, f"fail({err[0][:100]})", time.time()-t0)

def main():
    items = [json.loads(l) for l in TASKS.read_text().splitlines() if l.strip()]
    items = [it for it in items
             if not (OUTDIR/(f"{it['id']}.json" if it.get('schema') else f"{it['id']}.md")).exists()]
    todo = len(items)
    if PARALLEL.isdigit():
        target = max(1, int(PARALLEL)); mode = f"manual={target}"
    else:
        target = auto_target(todo); mode = f"auto→{target}(RAM기반)"
    if not todo: print("[done] 처리할 작업 없음."); return 0
    scaler = AutoScaler(target)
    print(f"[spawn] todo={todo} workers={mode} start={scaler.live()} sandbox={'bypass' if BYPASS else SANDBOX}", flush=True)
    ok=fail=0; t0=time.time()
    with ThreadPoolExecutor(max_workers=target) as ex:
        futs={ex.submit(run_one,it,scaler):it["id"] for it in items}
        for f in as_completed(futs):
            tid,status,el=f.result()
            if status=="ok":
                ok+=1; print(f"[ok] {tid} ({el:.0f}s) · 워커 {scaler.live()}/{target}", flush=True)
            elif status!="skip":
                fail+=1; print(f"[fail#{fail}] {tid} ({el:.0f}s) {status}", flush=True)
    print(f"\n=== done: {ok} ok / {fail} fail / {(time.time()-t0)/60:.1f}min · peak 워커 {scaler.live()}/{target} ===")
    return 0 if fail==0 else 1

if __name__=="__main__":
    sys.exit(main())
