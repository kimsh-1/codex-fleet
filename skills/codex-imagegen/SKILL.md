---
name: codex-imagegen
version: "1.0.0"
description: codex CLI(`codex exec`)를 백그라운드로 N개 병렬 스폰해서 gpt-image-2 이미지를 대량 생성하고, 떨어진 PNG를 레이스 없이 회수하는 CLI 양산 스킬. 단일 1장은 codex 직접, 이 스킬은 수십~수백 장을 스폰 수(PARALLEL)로 나눠 뽑을 때. 트리거 — "코덱스로 이미지 대량생성", "codex exec 병렬 스폰", "이미지 배치 러너", "스폰 수 제어해서 이미지 뽑아", "$imagegen 배치", "프롬프트 jsonl로 이미지 N장", "codex 이미지 회수/move". 후속 — "동시성 올려/내려", "실패분만 재시도", "거부된 컷 완화", "resume" 도 이 스킬.
---

# Codex Image-Gen — CLI 병렬 스폰 오케스트레이터

`codex exec`(비대화형 codex CLI)를 **백그라운드 프로세스로 N개 동시에 띄워서** 이미지를 뽑고, codex가 자기 세션 폴더에 떨군 PNG를 **레이스 없이 목적지로 회수**하는 패턴. 스폰 개수(`PARALLEL`) 하나로 처리량을 제어한다.

## §0. 무엇인가

`codex` CLI를 **그대로 여러 개 띄워** 이미지를 양산하는 방식이다. 단일 이미지 한 장은 그냥 `codex`를 직접 쓰면 되고, 이 스킬은 **수십~수백 장을 나눠 뽑을 때** 쓴다.

- 동시성 = **OS 프로세스 N개**를 띄움 (`ThreadPool` / `xargs -P`). `PARALLEL` 하나로 처리량 제어.
- 인증 = `codex` CLI 로그인 상태를 그대로 사용 (토큰 주입 불필요).
- 출력 = codex가 `~/.codex/generated_images/`에 떨군 PNG를 목적지로 회수.

> ⚠️ **한도는 ChatGPT 계정 단위**다. 스폰을 늘려도 계정 한도(250 IPM)는 복제되지 않는다. 과한 자동 대량 호출은 세션 무효화(`refresh_token_invalidated`, 복구=`codex login` 재로그인) 위험 — `codex exec`는 공식 경로라 상대적으로 안전하지만 무한 스폰은 금물.

## §1. 핵심 메커니즘 — codex exec 한 번 = 이미지 한 장

비대화형 단일 호출:

```bash
codex exec \
  --skip-git-repo-check \
  --dangerously-bypass-approvals-and-sandbox \
  "Use \$imagegen to generate ONE image.
Aspect ratio: 2:3
Size: 1024x1536
Prompt: <프롬프트>
After generation, do NOT run any shell commands. Just generate and end your turn."
```

- `--skip-git-repo-check` : git repo 아닌 데서도 실행 허용
- `--dangerously-bypass-approvals-and-sandbox` : 승인 프롬프트 없이 무인 실행 (배치 필수)
- instruction 끝에 **"shell 실행하지 말고 턴 종료"** 를 꼭 박는다 — 안 그러면 codex가 파일을 직접 옮기려다 엉킨다.
- codex 빌트인 `$imagegen`이 이미지를 만들어 **`~/.codex/generated_images/{session-uuid}/ig_{40자SHA1}.png`** 에 저장한다.
- 평균 ~30~45초/장(1024 기준), 디테일 헤비/2048은 더 걸림. 단일 호출 타임아웃은 **240s** 권장.

**연결 점검(1장 테스트):**
```bash
codex exec 'Use $imagegen to generate a 1024x1024 red square test image. End turn immediately after.'
ls -lt ~/.codex/generated_images/**/ig_*.png | head -3
```

## §2. 스폰 수 — 자동 스케일링 (기본)

`PARALLEL = 동시에 살아있는 codex 프로세스 수`. **기본값 `auto`** — 작업 수에 맞춰 워커를 알아서 산정하고, 돌아가는 게 헬시하면 상한까지 **자동으로 늘린다**. 숫자 박을 필요 없음.

- **`PARALLEL=auto`(기본)**: 목표 워커 = `min(작업수, MAX)`. `MAX` 미지정 시 `min(16, CPU-1)`(CLI 스폰은 프로세스가 무거워 CPU가 병목 → 코어 기반 캡).
- **램프업**: `START`(기본 3) 워커로 시작 → 성공 `RAMP_EVERY`(기본 5)건마다 +1 → 목표치까지. **429/rate limit이 stderr에 보이면 성장 일시정지**(개별 호출은 codex가 백오프). 즉 작아도 안전하게 출발해 헬시하면 알아서 커진다.
- **수동 고정**: `PARALLEL=8`처럼 숫자를 주면 그 값으로 고정(오토 끔).
- 튜닝: `MAX`(자동 상한), `START`(시작), `RAMP_EVERY`(증가 속도).

| 계정 | 권장 | 근거 |
|---|---|---|
| ChatGPT Plus | `PARALLEL=1` | UI 동시생성 제한, 가장 안전 |
| ChatGPT Pro | `auto`(MAX 6~8) | start 3에서 헬시하면 램프업 |
| API Key 직접 | `auto`(MAX≈CPU) | 250 IPM ÷ ~15s/장 여유 |

- **하드캡 250 IPM**(gpt-image-2 공식). 초과 시 **429** → 스케일러가 성장 멈춤.
- 고동시성을 무작정 올리면 토큰 한도 전에 **머신이 먼저 죽는다** → 그래서 CPU 기반 캡 + 점진 램프.

## §3. 회수(move) — 여기에 레이스 함정이 있다

codex는 PNG를 자기 세션 폴더에 떨구므로 목적지로 옮겨야 한다. 기존 `move_outputs.py`는 **"내 시작시각 이후 전역 최신 ig_*.png 1장"** 을 집어온다:

```
~/.codex/generated_images/{session}/ig_*.png  중  mtime > after_ts  인 최신 1개
```

> 🐛 **레이스 버그(중요):** 이 로직은 세션 uuid로 필터링하지 **않는다**(원본 주석의 "session uuid 기반 → race 안전"은 사실이 아님). 병렬 스폰이면 워커 A의 move가 비슷한 시각에 끝난 **워커 B의 이미지를 채갈 수 있다**. PARALLEL=1이면 안전, 2 이상이면 어긋남.

**해법 — `claimed` 집합 + 락으로 1:1 보장.** 한 번 회수한 파일은 다시 못 집게 한다(아래 §4 러너에 반영됨). 대안: `codex exec` stdout에서 세션 id를 파싱(버전 의존적이라 비권장).

회수 실패 = `~/.codex/generated_images/`에 신규파일 없음 → 대개 **모더레이션 거부**(§5).

## §4. 레퍼런스 러너 (레이스 수정판)

`batch_runner_parallel.py`를 레이스-세이프하게 고친 버전. 입력은 `{id, prompt, ar, size, output_path}` JSONL.

```python
#!/usr/bin/env python3
# codex_imagegen_runner.py — codex exec를 자동 스케일링 병렬 스폰 → 레이스-세이프 회수
# 실행: PROMPTS=prompts.jsonl OUTDIR=./out python3 codex_imagegen_runner.py
#   PARALLEL=auto(기본) : 작업 수에 맞춰 워커 자동 산정 + 헬시하면 상한까지 램프업
#   PARALLEL=8          : 수동 고정.   MAX=12 : 자동 상한 override.   START=3 : 시작 워커.
import json, os, subprocess, sys, time, threading, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PROMPTS  = Path(os.environ.get("PROMPTS", "prompts.jsonl"))
OUTDIR   = Path(os.environ.get("OUTDIR", "./out"))
PARALLEL = os.environ.get("PARALLEL", "auto")
TIMEOUT  = int(os.environ.get("TIMEOUT", "240"))
RAMP_EVERY = int(os.environ.get("RAMP_EVERY", "5"))   # 성공 N건마다 워커 +1
CODEX_IMG = Path.home() / ".codex" / "generated_images"

def auto_target(todo):
    """작업 수 → 목표 워커. CPU 코어로 캡(CLI 스폰은 프로세스가 무거움). MAX로 override."""
    cap = int(os.environ.get("MAX", "0"))
    if not cap:
        cpu = os.cpu_count() or 4
        cap = min(16, max(2, cpu - 1))
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
                    self.ok = 0; self.permits += 1; self.sem.release()  # 천장 +1
    def throttle(self):
        with self.lock:
            self.ok = 0  # 429 보이면 성장 일시정지(개별 호출은 codex가 백오프)

_lock = threading.Lock()
_claimed = set()   # 이미 회수한 png 경로 — 레이스 방지 핵심
def newest_unclaimed(after_ts):
    with _lock:
        best = None
        if CODEX_IMG.exists():
            for sess in CODEX_IMG.iterdir():
                if not sess.is_dir(): continue
                for png in sess.glob("ig_*.png"):
                    p = str(png)
                    if p in _claimed: continue
                    try: m = png.stat().st_mtime
                    except OSError: continue
                    if m > after_ts and (best is None or m > best[1]): best = (png, m)
        if best:
            _claimed.add(str(best[0])); return best[0]
        return None

def _is_throttle(err):
    e = (err or "").lower()
    return "429" in e or "rate limit" in e or "too many requests" in e

def run_one(item, scaler):
    pid = item["id"]; out = OUTDIR / item["output_path"]
    if out.exists(): return (pid, "skip", 0)   # resume
    instr = (f"Use $imagegen to generate ONE image.\n"
             f"Aspect ratio: {item.get('ar','1:1')}\n"
             f"Size: {item.get('size','1024x1536')}\n"
             f"Prompt: {item['prompt']}\n"
             f"After generation, do NOT run any shell commands. Just generate and end your turn.")
    with scaler:                               # 동시 실행 슬롯(천장은 스케일러가 조절)
        before = time.time() - 1
        try:
            r = subprocess.run(
                ["codex","exec","--skip-git-repo-check","--dangerously-bypass-approvals-and-sandbox", instr],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                timeout=TIMEOUT, text=True)
        except subprocess.TimeoutExpired:
            return (pid, "timeout", time.time()-before)
        if _is_throttle(r.stderr): scaler.throttle()
        deadline = time.time() + 30; src = None
        while time.time() < deadline:
            src = newest_unclaimed(before)
            if src: break
            time.sleep(1)
    if not src:
        return (pid, "rejected/no-image", time.time()-before)   # 대개 모더레이션 거부
    out.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(src), str(out))
    scaler.success()
    return (pid, "ok", time.time()-before)

def main():
    items = [json.loads(l) for l in PROMPTS.read_text().splitlines() if l.strip()]
    items = [it for it in items if not (OUTDIR/it["output_path"]).exists()]   # resume
    todo = len(items)
    if PARALLEL.isdigit():
        target = max(1, int(PARALLEL)); mode = f"manual={target}"
    else:
        target = auto_target(todo); mode = f"auto→{target}(cpu={os.cpu_count()})"
    if not todo: print("[done] 처리할 작업 없음."); return 0
    scaler = AutoScaler(target)
    print(f"[spawn] todo={todo} workers={mode} start={scaler.live()} ramp=+1/{RAMP_EVERY}ok", flush=True)
    ok=fail=0; t0=time.time()
    with ThreadPoolExecutor(max_workers=target) as ex:
        futs={ex.submit(run_one,it,scaler):it["id"] for it in items}
        for f in as_completed(futs):
            pid,status,el=f.result()
            if status=="ok":
                ok+=1
                if ok%10==0:
                    rate=ok/max(time.time()-t0,1)*60
                    print(f"[progress] {ok}/{todo} ok · {rate:.1f}/min · 워커 {scaler.live()}/{target}", flush=True)
            elif status!="skip":
                fail+=1; print(f"[fail#{fail}] {pid} ({el:.0f}s) {status}", flush=True)
    print(f"\n=== done: {ok} ok / {fail} fail / {(time.time()-t0)/60:.1f}min · peak 워커 {scaler.live()}/{target} ===")
    return 0 if fail==0 else 1

if __name__=="__main__":
    sys.exit(main())
```

**최소 bash 변형** (의존성 없이 "진짜 백그라운드 스폰"만 보고 싶을 때 — `xargs -P`가 스폰 수):
```bash
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
```

## §5. 모더레이션 거부 처리

- 거부 시 codex가 `I'm unable to generate this image due to content policy restrictions.` 류를 출력하고 **신규 png를 안 만든다** → 러너에선 `rejected/no-image`.
- 우회(노골화) 금지. **표현을 광고/캠페인 언어로 톤다운**하면 통과하는 경우가 많다(예: "gravure/wet-look" → 깔끔한 캠페인 서술, "lingerie" → "loungewear" 프레이밍).
- 거부분만 추려 프롬프트 완화 후 재투입:
```bash
# 실패 id 목록으로 jsonl 필터링 → 프롬프트 수정 → resume 재실행
```

## §6. resume · 재시도 · 정리

- **resume**: 러너가 `output_path` 존재하면 자동 스킵. 중단 후 그냥 다시 실행하면 남은 것만 처리.
- **개별 실패는 런 안에서 무한재시도 금지** — 다음 패스 resume에서 자연히 재시도된다. 1패스 완료를 종료로 간주.
- **자살 버그 주의**: `pkill -f codex_spawn_runner` 는 그 명령을 실행한 셸 자신(명령줄에 문자열 포함)을 죽인다. 반드시 프로세스 한정:
  ```bash
  pkill -9 -f "python3.*codex_spawn_runner"   # 러너만
  pkill -9 -f "codex exec"                      # 떠있는 codex 워커들
  ```
- **디스크**: `~/.codex/generated_images/` 가 회수 안 된 잔여로 쌓인다. 주기적 정리, 배치 전 **2GB+ 여유** 확인.
- **백그라운드 장기 실행**: 러너 자체를 백그라운드로 돌리고 로그로 진행 추적 (harness의 `run_in_background` 또는 `nohup ... &`).

## §7. 프롬프트·사이즈 규칙 (gpt-image-2)

- **사이즈 고정세트만**: `1024x1024 / 1536x1024 / 1024x1536 / 1792x1024 / 1024x1792 / 2048x2048`. `512x512` 거부. 4:5·16:9 등은 가까운 값으로 매핑(16:9→1792x1024, 9:16→1024x1792, 2:3→1024x1536).
- **quality**: `low/medium/high`. 만화·텍스트·세밀선은 **high + 2048** 아니면 뭉갠다. 일반은 medium.
- **네거티브 프롬프트 안 씀**: "no ~" 부정문은 품질 저하. **포지티브 서술**로 대체.
- 텍스트 렌더가 필요하면 instruction에 "render text exactly" 명시. 한글 렌더는 강점.

## §8. 체크리스트

1. `codex exec`로 1장 테스트 → `~/.codex/generated_images/`에 png 떨어지는지 확인
2. `prompts.jsonl` 형식: `{id, prompt, ar, size, output_path}` 한 줄씩
3. PARALLEL을 계정에 맞게(Plus 1 / Pro 3~ 시작), 디스크 2GB+ 확인
4. 파이썬 러너(claimed 락) 실행 — **bash판은 PARALLEL>1에서 레이스**
5. 진행/실패 로그 확인 → 거부분 톤다운 후 resume 재투입
6. 종료 시 `pkill -9 -f "codex exec"`로 잔여 워커 정리, 잔여 png 청소
