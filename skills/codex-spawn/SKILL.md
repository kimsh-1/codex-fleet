---
name: codex-spawn
version: "1.0.0"
description: 깡코덱스(`codex exec`) 비대화형 프로세스를 백그라운드로 N개 병렬 스폰해서 임의 작업(코드 수정·분석·생성·리뷰)을 대량 처리하고, 결과를 파일로 회수하는 오케스트레이션 스킬. 이미지 전용이 아니라 범용 — 스폰 수(PARALLEL)로 처리량을 제어하고, 파일 충돌은 작업별 디렉토리/worktree 격리로 막는다. 트리거 — "codex exec 병렬 스폰", "깡코덱스 N개 띄워", "코덱스로 대량 작업 분산", "헤드리스 코덱스 배치", "codex 워커 풀", "스폰 수 제어해서 코덱스 돌려", "여러 파일에 코덱스 동시 적용". 후속 — "동시성 올려/내려", "실패분 재시도", "resume", "구조화 출력으로 받아" 도 이 스킬. ※ 이미지 생성이면 [codex-imagegen] 스킬을 쓸 것(저긴 $imagegen + 세션폴더 회수 특화). 단일 codex 대화/일반 코딩은 그냥 codex를 직접 쓰면 됨 — 이 스킬은 "여러 개를 동시에 띄워 나눠 시킬 때".
---

# Codex Spawn — 깡코덱스 병렬 스폰 오케스트레이터

`codex exec`(비대화형 codex) 프로세스를 **백그라운드로 N개 동시에 띄워** 임의 작업을 나눠 처리하고, 각 결과를 파일로 회수하는 패턴. 스폰 개수(`PARALLEL`) 하나로 처리량을 제어한다. 이미지 전용인 [codex-imagegen]의 범용 형제 스킬.

## §0. 언제 이 스킬인가

| 상황 | 도구 |
|---|---|
| 단일 작업, 대화형 | 그냥 `codex` 직접 |
| 이미지 N장 대량생성 | **codex-imagegen** 스킬 (`$imagegen` + 세션폴더 회수) |
| **임의 작업 N개를 동시에 나눠 시킴** | **이 스킬** (코드수정·분석·요약·리뷰·생성…) |

핵심 차이: imagegen은 출력이 `~/.codex/generated_images/`에 떨어져 회수 레이스가 있지만, 범용 작업은 **`-o`(output-last-message)로 작업별 파일에 직접 받으므로 회수 레이스가 없다.**

> ⚠️ **한도는 ChatGPT 계정 단위.** 스폰을 늘려도 계정 rate limit(분당 토큰/요청)은 복제되지 않는다. 과한 자동 대량 호출은 세션 무효화(`refresh_token_invalidated`) 위험 — `codex exec`는 공식 경로라 상대적으로 안전하지만 무한 스폰은 금물.

## §1. 핵심 메커니즘 — codex exec 한 번 = 작업 한 개

```bash
codex exec \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  -C /path/to/workdir \
  -o /path/to/result.md \
  "여기에 작업 지시문"
```

필수/유용 플래그(`codex-cli 0.139.0` 기준):

| 플래그 | 용도 |
|---|---|
| `-o, --output-last-message <FILE>` | **에이전트 최종 메시지를 파일로 저장** — 배치 회수의 정석(stdout 파싱 불필요) |
| `--json` | 이벤트를 JSONL로 stdout 출력(추론·메시지 스트림 파싱용) |
| `--output-schema <FILE>` | 최종 응답을 **JSON Schema 형태로 강제** — 구조화 결과 수집 |
| `-C, --cd <DIR>` | 작업 루트 지정 — **병렬 격리의 핵심** |
| `--add-dir <DIR>` | 추가 쓰기 허용 디렉토리 |
| `-s, --sandbox <mode>` | `read-only` / `workspace-write` / `danger-full-access` |
| `--dangerously-bypass-approvals-and-sandbox` | 승인·샌드박스 전부 생략(무인 배치) |
| `--skip-git-repo-check` | git repo 밖에서도 실행 |
| `--ephemeral` | **세션파일 안 남김** — 병렬 다발 실행 시 `~/.codex` 비대화 방지 |
| `-m, --model <MODEL>` | 모델 지정 |
| `-i, --image <FILE>` | 이미지 첨부 |
| `resume` | 이전 세션 이어가기(`codex exec resume --last`) |

- 결과는 `-o` 파일에 들어간다. 안전 배치 패턴: **`read-only`로 분석/조사**, **`workspace-write` + 격리 디렉토리로 수정**.
- 무인 실행은 `--dangerously-bypass-approvals-and-sandbox` 필요(승인 프롬프트 제거). 다만 이건 샌드박스도 끄므로, 안전하게 가려면 대신 `-s workspace-write -a never`(승인 안 물음 + 워크스페이스 쓰기 한정) 조합을 검토.

**연결 점검(1개 테스트):**
```bash
codex exec --skip-git-repo-check -s read-only -o /tmp/t.md "이 디렉토리 구조를 3줄로 요약해" -C .
cat /tmp/t.md
```

## §2. 스폰 수 — 자동 스케일링 + 파일 충돌 격리

`PARALLEL = 동시에 살아있는 codex 프로세스 수`. **기본 `auto`** — 작업 수에 맞춰 워커를 산정하고 헬시하면 상한까지 알아서 램프업한다.

- **`PARALLEL=auto`(기본)**: 목표 = `min(작업수, MAX)`, `MAX` 미지정 시 `min(16, CPU-1)`. `START`(기본 3)에서 시작해 성공 `RAMP_EVERY`(기본 5)건마다 +1, 429 보이면 성장 정지. 수동은 `PARALLEL=4`로 고정.
- **읽기 전용 작업**(분석·리뷰·요약·조사)은 충돌이 없으니 같은 repo에 그냥 auto로 띄워도 된다(`-s read-only`).
- **파일 수정 작업**은 여러 codex가 같은 repo를 동시에 고치면 **충돌**난다. 격리 필수:
  - **작업별 디렉토리**: 각 작업이 독립 파일/폴더만 건드리면 jsonl `cwd`(→`-C`)로 분리
  - **git worktree**: 같은 repo를 병렬 수정해야 하면 작업마다 `git worktree add`로 별도 워크트리 → 각 codex가 자기 worktree에서 작업 → 끝나고 머지
- CLI 스폰은 프로세스당 풀 codex라 **메모리/CPU가 진짜 병목**이다. 그래서 auto가 CPU 기반으로 캡하고 점진 램프한다. 정상상태에서 rate 측정(버스트 구간 측정은 과소평가).

## §3. 레퍼런스 러너 (범용, 결과는 파일로 회수)

입력 JSONL: 한 줄당 `{id, prompt, cwd?, schema?}`. 결과는 `OUTDIR/<id>.md`(또는 schema 시 `.json`).

```python
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
    print(f"
=== done: {ok} ok / {fail} fail / {(time.time()-t0)/60:.1f}min · peak 워커 {scaler.live()}/{target} ===")
    return 0 if fail==0 else 1

if __name__=="__main__":
    sys.exit(main())
```

**최소 bash 변형** (`xargs -P`가 스폰 수, 결과는 `-o`로 회수):
```bash
# tasks.jsonl: {"id","prompt","cwd"}
mkdir -p out
jq -c '.' tasks.jsonl | xargs -P 4 -I{} bash -c '
  j={}; id=$(jq -r .id <<<"$j"); cwd=$(jq -r ".cwd // \".\"" <<<"$j")
  out="out/$id.md"; [ -s "$out" ] && exit 0           # resume
  codex exec --skip-git-repo-check --ephemeral -s read-only -C "$cwd" \
    -o "$out" "$(jq -r .prompt <<<"$j")" </dev/null >/dev/null 2>&1 \
    && echo "OK $id" || echo "FAIL $id"
'
```

## §4. 구조화 출력 (`--output-schema`)

결과를 일정한 JSON으로 모아야 할 때(집계·후처리), 작업마다 스키마를 준다:
```jsonc
// tasks.jsonl 한 줄
{"id":"f001","cwd":"./repo","prompt":"이 파일의 보안 이슈를 찾아라",
 "schema":{"type":"object","properties":{
    "findings":{"type":"array","items":{"type":"object","properties":{
      "severity":{"type":"string"},"file":{"type":"string"},"note":{"type":"string"}}}}},
  "required":["findings"]}}
```
→ codex가 마지막 메시지를 이 스키마에 맞춰 내고, `-o out/f001.json`에 떨어진다. 러너가 자동으로 `.json` 확장자로 저장.

## §5. resume · 재시도 · 정리 · 안전

- **resume**: 결과 파일 있으면 자동 스킵. 중단 후 그냥 다시 실행 → 남은 것만.
- **개별 실패는 런 안 무한재시도 금지** — 다음 패스 resume에서 재시도. 1패스 완료를 종료로 간주.
- **자살 버그 주의**: `pkill -f codex_spawn_runner`는 그 명령 실행 셸 자신을 죽인다. 프로세스 한정:
  ```bash
  pkill -9 -f "python3.*codex_spawn_runner"   # 러너
  pkill -9 -f "codex exec"                      # 떠있는 워커들
  ```
- **세션파일**: `--ephemeral` 안 쓰면 `~/.codex/sessions`에 쌓인다. 병렬 다발이면 `--ephemeral` 권장(resume 필요 작업은 빼고).
- **격리 안전**: 파일 수정 작업을 격리 없이 같은 repo에 병렬로 돌리지 말 것. 모르면 일단 `-s read-only`.
- **무인 위험**: `--dangerously-bypass-approvals-and-sandbox`는 샌드박스도 끈다. 신뢰 못 하는 프롬프트엔 쓰지 말고 `-s workspace-write` 격리로.

## §6. 체크리스트

1. `codex exec -s read-only -o /tmp/t.md "..."` 1개 테스트 → 결과 파일 생기는지
2. `tasks.jsonl` 형식: `{id, prompt, cwd?, schema?}` 한 줄씩
3. 작업 성격 판단: **읽기전용이면 격리 불필요 / 수정이면 작업별 dir 또는 worktree 격리**
4. `PARALLEL=auto`(기본)로 두면 작업 수·CPU에 맞춰 자동 스케일 — 수동은 `PARALLEL=N`
5. 파이썬 러너 실행(결과 `-o` 파일 회수라 레이스 없음)
6. 실패분은 resume 재실행, 종료 시 `pkill -9 -f "codex exec"`로 잔여 정리
