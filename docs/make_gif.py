#!/usr/bin/env python3
"""docs/orchestration.gif 생성 — codex-fleet 스폰 오케스트레이션 심리스 루프.
의존성: Pillow만. 모든 애니 요소를 (t mod 1) 주기 함수로 만들어 이음매 없이 반복.
"""
from PIL import Image, ImageDraw, ImageFont
import math

W, H = 900, 440
N = 4                      # 워커(레인) 수
FRAMES = 60
DUR_MS = 55               # 프레임 간격 → 약 3.3s 루프

BG     = (10, 14, 26)
PANEL  = (14, 22, 40)
LINE   = (30, 39, 64)
INK    = (232, 237, 247)
DIM    = (138, 151, 181)
CYAN   = (55, 224, 216)
GOLD   = (240, 198, 116)
ROSE   = (240, 122, 140)
PAL    = [CYAN, GOLD, ROSE, (126, 224, 168)]   # 워커별 색

FB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FM = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
f_brand = ImageFont.truetype(FB, 22)
f_lbl   = ImageFont.truetype(FM, 14)
f_sm    = ImageFont.truetype(FM, 12)
f_tin   = ImageFont.truetype(FM, 11)

orchX, orchY, orchW, orchH = 70, H // 2, 150, 96
workX = 470
resX  = 800

def lane_y(i):
    gap = 74
    top = H / 2 - (N - 1) * gap / 2
    return int(top + i * gap)

def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)

def lerp(a, b, t):
    return a + (b - a) * t

def mix(c, a):
    return (int(c[0]), int(c[1]), int(c[2]), int(255 * a))

def rrect(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

def text_c(d, xy, s, font, fill, anchor="mm"):
    d.text(xy, s, font=font, fill=fill, anchor=anchor)

frames = []
for fi in range(FRAMES):
    t = fi / FRAMES                                   # 0..1 글로벌 위상
    img = Image.new("RGB", (W, H), BG)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))      # 알파 합성용 오버레이
    d = ImageDraw.Draw(img)
    do = ImageDraw.Draw(ov)

    # subtle glow behind orchestrator
    do.ellipse([orchX-60, orchY-150, orchX+360, orchY+150], fill=(55, 224, 216, 10))

    # connection guides orchestrator -> workers
    for i in range(N):
        y = lane_y(i)
        d.line([(orchX + orchW, orchY), (workX, y)], fill=LINE, width=2)

    # results tray bg
    rrect(do, [resX - 18, 60, resX + 86, H - 60], 14, fill=(55, 224, 216, 10))
    text_c(d, (resX + 34, 46), "results/", f_sm, DIM)
    text_c(d, (workX + 50, 46), "codex exec x%d" % N, f_sm, DIM)

    # --- workers (periodic per-lane phase) ---
    for i in range(N):
        y = lane_y(i)
        ph = (t + i / N) % 1.0          # 레인별 위상 오프셋
        col = PAL[i % len(PAL)]
        # 처리 구간 0.20~0.70
        processing = 0.20 <= ph < 0.70
        prog = (ph - 0.20) / 0.50 if processing else (1.0 if ph >= 0.70 else 0.0)
        busy = 0.20 <= ph < 0.90

        rrect(d, [workX, y - 17, workX + 130, y + 17], 9, fill=PANEL)
        if processing:
            rrect(do, [workX + 4, y - 13, workX + 4 + int(122 * prog), y + 13], 6, fill=mix(col, 0.30))
        rrect(d, [workX, y - 17, workX + 130, y + 17], 9,
              outline=col if busy else LINE, width=2 if busy else 1)
        text_c(d, (workX + 22, y), "w%d" % (i + 1), f_sm, INK if busy else DIM, anchor="lm")
        # status dot
        puls = 0.5 + 0.5 * math.sin((t * 2 + i) * math.pi) if busy else 0.3
        dotc = col if busy else DIM
        do.ellipse([workX + 110, y - 4, workX + 118, y + 4], fill=mix(dotc, puls))

        # task square: orchestrator -> worker, ph 0.00~0.20
        if ph < 0.20:
            p = ease(ph / 0.20)
            tx = lerp(orchX + orchW / 2, workX, p)
            ty = lerp(orchY, y, p)
            do.rounded_rectangle([tx-11, ty-11, tx+11, ty+11], radius=6, fill=mix(col, 0.22))
            d.rounded_rectangle([tx-7, ty-7, tx+7, ty+7], radius=4, fill=col)

        # output square: worker -> results, ph 0.70~0.90
        if 0.70 <= ph < 0.90:
            p = ease((ph - 0.70) / 0.20)
            ox = lerp(workX + 130, resX, p)
            d.rounded_rectangle([ox-8, lane_y(i)-8, ox+8, lane_y(i)+8], radius=4, fill=col)

    # --- results tray: shimmering filled grid (periodic, seamless) ---
    cols, tile, gap = 2, 30, 10
    for k in range(8):
        cx = resX - 6 + (k % cols) * (tile + gap)
        cy = 74 + (k // cols) * (tile + gap)
        shimmer = 0.45 + 0.35 * math.sin((t * 2 + k * 0.5) * math.pi)
        c = PAL[k % len(PAL)]
        do.rounded_rectangle([cx, cy, cx + tile, cy + tile], radius=6, fill=mix(c, shimmer))

    # --- orchestrator node ---
    rrect(d, [orchX, orchY - orchH//2, orchX + orchW, orchY + orchH//2], 16, fill=PANEL)
    rrect(d, [orchX, orchY - orchH//2, orchX + orchW, orchY + orchH//2], 16, outline=CYAN, width=2)
    text_c(d, (orchX + orchW/2, orchY - 8), "codex-fleet", f_brand, INK)
    text_c(d, (orchX + orchW/2, orchY + 18), "orchestrator", f_sm, DIM)

    # composite overlay
    img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(img)

    # footer label
    text_c(d, (W/2, H - 22), "PARALLEL = %d   .   spawn N codex workers, control throughput" % N,
           f_tin, DIM)
    # window dots
    for j, c in enumerate([(240, 98, 108), (240, 198, 116), (55, 224, 216)]):
        d.ellipse([20 + j*18, 18, 30 + j*18, 28], fill=c)
    text_c(d, (88, 23), "codex-fleet  spawn orchestration", f_sm, DIM, anchor="lm")
    d.line([(0, 40), (W, 40)], fill=LINE, width=1)

    # palette-quantize for small GIF
    frames.append(img.convert("P", palette=Image.ADAPTIVE, colors=64))

out = "docs/orchestration.gif"
frames[0].save(out, save_all=True, append_images=frames[1:], duration=DUR_MS,
               loop=0, optimize=True, disposal=2)
print("wrote", out, "frames=", len(frames))
