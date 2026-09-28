#!/usr/bin/env python3
"""
Regenerates the visual assets referenced by README.md.

Outputs (all committed so the README renders on GitHub):
  assets/logo.png          - small repo logo mark
  assets/banner.png        - hero banner (AI art background + crisp text overlay)
  assets/pipeline.gif      - ANIMATED end-to-end production pipeline (12 stages)
  assets/telegram-demo.gif - ANIMATED Telegram agent chat simulation

Deterministic: re-running this script rebuilds identical-layout assets.
Requires: pip install pillow
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def f(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    path = (MONO_BOLD if bold else MONO) if mono else (FONT_BOLD if bold else FONT)
    return ImageFont.truetype(path, size)


# Palette ---------------------------------------------------------------
BG = (11, 16, 32)            # deep navy
PANEL = (20, 27, 51)
PANEL_LIGHT = (30, 40, 74)
TEXT = (234, 240, 255)
SUB = (147, 160, 196)
VIOLET = (124, 77, 255)
CYAN = (0, 184, 217)
PINK = (255, 92, 168)
GREEN = (43, 217, 124)
AMBER = (255, 176, 32)
BLUE = (77, 159, 255)
PALETTE = [VIOLET, CYAN, PINK, GREEN, AMBER, BLUE]


def rr(draw: ImageDraw.ImageDraw, box, r, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def text_w(draw, s, font):
    l, t, r, b = draw.textbbox((0, 0), s, font=font)
    return r - l


def glow_text(img: Image.Image, xy, s, font, fill, glow, passes=6):
    """Draw text with a soft glow halo."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    x, y = xy
    for i in range(passes, 0, -1):
        a = int(26 * (passes - i + 1) / passes)
        col = (*glow, a)
        for dx in (-i, 0, i):
            for dy in (-i, 0, i):
                d.text((x + dx, y + dy), s, font=font, fill=col)
    d.text(xy, s, font=font, fill=(*fill, 255))
    img.alpha_composite(overlay)


# ----------------------------------------------------------------------
# logo.png
# ----------------------------------------------------------------------
def make_logo():
    S = 256
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # circular gradient badge
    for y in range(S):
        t = y / S
        col = (
            int(VIOLET[0] * (1 - t) + CYAN[0] * t),
            int(VIOLET[1] * (1 - t) + CYAN[1] * t),
            int(VIOLET[2] * (1 - t) + CYAN[2] * t),
        )
        d.line([(0, y), (S, y)], fill=col)
    # clip to circle
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse((4, 4, S - 4, S - 4), fill=255)
    img.putalpha(mask)
    d = ImageDraw.Draw(img)
    # play triangle
    d.polygon([(96, 78), (96, 178), (186, 128)], fill=(255, 255, 255, 245))
    # waveform bars under
    bars = [10, 22, 34, 20, 40, 26, 14]
    x0 = 78
    for i, h in enumerate(bars):
        x = x0 + i * 15
        d.rounded_rectangle((x, 196 - h // 2, x + 8, 196 + h // 2), radius=4,
                            fill=(255, 255, 255, 210))
    img.save(ASSETS / "logo.png")
    return img


# ----------------------------------------------------------------------
# banner.png
# ----------------------------------------------------------------------
def make_banner():
    art = Image.open(ASSETS / "_banner_art.png").convert("RGBA")
    W, H = art.size  # 1600x500
    img = art.copy()
    # subtle dark veil in the center for legibility
    veil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    vd.ellipse((W * 0.18, H * 0.05, W * 0.82, H * 0.98), fill=(5, 8, 18, 150))
    veil = veil.filter(ImageFilter.GaussianBlur(60))
    img.alpha_composite(veil)

    d = ImageDraw.Draw(img)
    title_f = f(92, bold=True)
    sub_f = f(30)
    chip_f = f(20, bold=True)

    title = "AuraStream 2.0"
    sub = "Fully-Automated YouTube AI Studio  -  from trending topic to upload, on autopilot"

    tw = text_w(d, title, title_f)
    glow_text(img, ((W - tw) // 2, 130), title, title_f, TEXT, VIOLET)
    d = ImageDraw.Draw(img)
    sw = text_w(d, sub, sub_f)
    d.text(((W - sw) // 2, 262), sub, font=sub_f, fill=(210, 220, 245, 255))

    # chips
    chips = [("1080p FULL HD", CYAN), ("200+ LANGUAGES", VIOLET),
             ("TELEGRAM AUTOPILOT", PINK), ("HF SUPERPOWERS", GREEN)]
    total = 0
    widths = []
    for label, col in chips:
        w = text_w(d, label, chip_f) + 44
        widths.append(w)
        total += w
    gap = 24
    total += gap * (len(chips) - 1)
    x = (W - total) // 2
    y = 330
    for (label, col), w in zip(chips, widths):
        rr(d, (x, y, x + w, y + 46), 23, fill=(*col, 40), outline=col, width=2)
        d.text((x + 22, y + 10), label, font=chip_f, fill=(240, 246, 255, 255))
        x += w + gap

    img.convert("RGB").save(ASSETS / "banner.png", quality=92)


# ----------------------------------------------------------------------
# pipeline.gif
# ----------------------------------------------------------------------
STAGES = [
    ("Trends & Topic", "pytrends + breaking news"),
    ("Script + SEO", "Gemini 2.5 Flash -> Mistral-7B"),
    ("HF Analysis", "zero-shot tags / NLLB-200"),
    ("Voiceover", "edge-tts + MMS-TTS"),
    ("Stock Footage", "Pexels -> Pixabay / 1080p"),
    ("1080p Assembly", "MoviePy 2 + Whisper .srt"),
    ("Thumbnail", "SDXL -> Pollinations + text"),
    ("Music", "MusicGen -> Pixabay"),
    ("YouTube Upload", "OAuth + CC captions"),
    ("Comment AI", "sentiment-aware replies"),
    ("Telegram Agent", "memory + voice notes"),
    ("Autopilot", "schedule / digest / alerts"),
]


def pipeline_layout():
    W, H = 1000, 700
    NW, NH = 290, 92
    xs = [20, 355, 690]
    ys = [104, 262, 420, 578]
    # snake order
    grid = [
        (0, 0), (0, 1), (0, 2),
        (1, 2), (1, 1), (1, 0),
        (2, 0), (2, 1), (2, 2),
        (3, 2), (3, 1), (3, 0),
    ]
    boxes = [(xs[c], ys[r], xs[c] + NW, ys[r] + NH) for (r, c) in grid]
    return W, H, boxes


def draw_pipeline_frame(active: int, spark=None, spark_seg=None, pulse=0.5):
    """active: highest stage index lit. spark_seg: (seg_index, t) between nodes.
    pulse in [0,1] drives a subtle halo shimmer so hold frames stay unique."""
    W, H, boxes = pipeline_layout()
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    tf = f(30, bold=True)
    sf = f(17)
    title = "AUTOMATED PRODUCTION PIPELINE"
    d.text(((W - text_w(d, title, tf)) // 2, 26), title, font=tf, fill=TEXT)
    sub = "topic in  ->  video + thumbnail + captions + upload out"
    d.text(((W - text_w(d, sub, sf)) // 2, 68), sub, font=sf, fill=SUB)

    centers = [((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for b in boxes]

    # connectors
    for i in range(len(boxes) - 1):
        a, b = centers[i], centers[i + 1]
        lit = i < active
        col = PALETTE[i % 6] if lit else (45, 56, 92)
        if a[1] == b[1]:  # horizontal
            x1, x2 = sorted((a[0], b[0]))
            d.line((x1, a[1], x2, a[1]), fill=col, width=3 if lit else 2)
            head = (x2 - 8, a[1]) if b[0] > a[0] else (x2 + 8, a[1])
            d.polygon([(head[0], a[1] - 6), (head[0], a[1] + 6),
                       (head[0] + (8 if b[0] > a[0] else -8), a[1])], fill=col)
        else:  # vertical
            y1, y2 = sorted((a[1], b[1]))
            d.line((a[0], y1, a[0], y2), fill=col, width=3 if lit else 2)
            d.polygon([(a[0] - 6, y2 - 8), (a[0] + 6, y2 - 8), (a[0], y2)], fill=col)

    # nodes
    nf = f(21, bold=True)
    sf2 = f(15)
    for i, b in enumerate(boxes):
        col = PALETTE[i % 6]
        on = i <= active
        if on:  # halo (RGB image -> blend instead of alpha), shimmer on active
            boost = 0.25 * pulse if i == active else 0.0
            halo = tuple(int(min(255, c * (0.55 + boost)) + BG[j] * (0.45 - boost))
                         for j, c in enumerate(col))
            rr(d, (b[0] - 5, b[1] - 5, b[2] + 5, b[3] + 5), 20, outline=halo, width=2)
        rr(d, b, 16, fill=PANEL_LIGHT if on else PANEL,
           outline=col if on else (45, 56, 92), width=2 if on else 1)
        # number chip
        cx, cy = b[0] + 34, (b[1] + b[3]) // 2
        d.ellipse((cx - 17, cy - 17, cx + 17, cy + 17),
                  fill=col if on else (45, 56, 92))
        numf = f(18, bold=True)
        ns = str(i + 1)
        nw = text_w(d, ns, numf)
        d.text((cx - nw // 2, cy - 11), ns, font=numf, fill=(10, 12, 24))
        d.text((b[0] + 62, b[1] + 18), STAGES[i][0], font=nf, fill=TEXT if on else SUB)
        d.text((b[0] + 62, b[1] + 50), STAGES[i][1], font=sf2, fill=SUB)

    # spark
    if spark_seg is not None:
        seg, t = spark_seg
        a, b = centers[seg], centers[seg + 1]
        px = a[0] + (b[0] - a[0]) * t
        py = a[1] + (b[1] - a[1]) * t
        col = PALETTE[seg % 6]
        d.ellipse((px - 10, py - 10, px + 10, py + 10), fill=(*col,))
        d.ellipse((px - 4, py - 4, px + 4, py + 4), fill=(255, 255, 255))
    return img


def make_pipeline_gif():
    import math
    frames = []
    HOLD, MOVE = 6, 3
    for i in range(len(STAGES)):
        for k in range(HOLD):
            pulse = 0.5 + 0.5 * math.sin(math.pi * k / (HOLD - 1))
            frames.append(draw_pipeline_frame(active=i, pulse=pulse))
        if i < len(STAGES) - 1:
            for k in range(1, MOVE + 1):
                t = k / (MOVE + 1)
                frames.append(draw_pipeline_frame(active=i, spark_seg=(i, t), pulse=1.0))
    for k in range(14):
        pulse = 0.5 + 0.5 * math.sin(2 * math.pi * k / 13)
        frames.append(draw_pipeline_frame(active=len(STAGES) - 1, pulse=pulse))
    frames[0].save(
        ASSETS / "pipeline.gif", save_all=True, append_images=frames[1:],
        duration=90, loop=0, optimize=True)
    return len(frames)


# ----------------------------------------------------------------------
# telegram-demo.gif
# ----------------------------------------------------------------------
CHAT_EVENTS = [
    ("user", ["/suggest"]),
    ("bot", [
        "Trending in your niche (tech):",
        " [92] How AI is changing video editing",
        " [88] Why 1080p still matters on YouTube",
        " [81] I automated my channel for 30 days",
        "",
        "  [ PRODUCE ]     [ SCRIPT ]",
    ]),
    ("user", ["[PRODUCE] How AI is changing video editing"]),
    ("bot", [
        "script ok   (Gemini 2.5 Flash -> Mistral-7B)",
        "voice ok    (edge-tts)   footage ok (Pexels 1080p)",
        "render 1920x1080@30 ..... done",
        "thumbnail ok (SDXL)   music ok (MusicGen)",
    ]),
    ("bot", [
        "uploaded to YouTube ok  + closed captions ok",
        "autopilot: next run daily 18:00 (Asia/Dhaka)",
    ]),
]


def make_telegram_gif():
    W = 680
    PAD = 18
    HEADER = 64
    bf = f(17, mono=True)
    line_h = 26

    # pre-compute full layout to fix height
    meas = ImageDraw.Draw(Image.new("RGB", (2, 2)))
    blocks = []
    y = HEADER + 16
    for side, lines in CHAT_EVENTS:
        bw = max(text_w(meas, ln, bf) for ln in lines) + 40
        bh = len(lines) * line_h + 20
        blocks.append((side, lines, y, bw, bh))
        y += bh + 12
    H = y + 16

    def render(visible_blocks, typing=False, cursor_gray=None):
        img = Image.new("RGB", (W, H), (8, 11, 22))
        d = ImageDraw.Draw(img)
        # header
        d.rectangle((0, 0, W, HEADER), fill=(16, 22, 42))
        d.ellipse((16, 14, 44, 42), fill=VIOLET)
        hf = f(17, bold=True)
        d.text((26, 20), "A", font=hf, fill=(255, 255, 255))
        d.text((56, 14), "AuraStream Bot", font=hf, fill=TEXT)
        d.ellipse((56, 40, 64, 48), fill=GREEN)
        d.text((70, 36), "online - remembers your channel", font=f(13, mono=True), fill=SUB)
        d.line((0, HEADER, W, HEADER), fill=(40, 50, 86), width=2)

        for (side, lines, by, bw, bh) in visible_blocks:
            if side == "user":
                x1 = W - PAD - bw
                x0 = W - PAD
                fill = (74, 63, 214)
            else:
                x1 = PAD
                x0 = PAD + bw
                fill = (24, 32, 58)
            rr(d, (x1, by, x0, by + bh), 14, fill=fill)
            tx = x1 + 20
            for j, ln in enumerate(lines):
                col = TEXT
                if side == "bot" and ln.strip().startswith("["):
                    col = CYAN
                d.text((tx, by + 10 + j * line_h), ln, font=bf, fill=col)
        # soft blinking cursor on the newest bubble
        if cursor_gray is not None and visible_blocks:
            side, lines, by, bw, bh = visible_blocks[-1]
            last = [ln for ln in lines if ln] or [""]
            j = len(lines) - 1
            lx = (W - PAD - bw if side == "user" else PAD) + 20 + text_w(d, last[-1], bf) + 4
            d.rectangle((lx, by + 12 + j * line_h, lx + 9, by + 12 + j * line_h + 18),
                        fill=(cursor_gray, cursor_gray, cursor_gray))

        if typing:
            by = visible_blocks[-1][2] if visible_blocks else HEADER + 16
            ty = (visible_blocks[-1][2] + visible_blocks[-1][4] + 12) if visible_blocks else HEADER + 16
            rr(d, (PAD, ty, PAD + 64, ty + 34), 14, fill=(24, 32, 58))
            for k in range(3):
                a = 90 + 120 * ((k + int(typing_frame[0])) % 3) // 2
                d.ellipse((PAD + 14 + k * 16, ty + 12, PAD + 22 + k * 16, ty + 20),
                          fill=(min(255, a + 80),) * 3)
        return img

    import math
    frames = []
    frames.append(render([]))  # intro: header only
    visible = []
    for idx, (side, lines) in enumerate(CHAT_EVENTS):
        block = blocks[idx]
        if side == "bot":
            for k in range(4):  # typing dots
                typing_frame[0] = k
                frames.append(render(visible, typing=True))
        visible.append(block)
        hold = 7 if side == "user" else 9
        for k in range(hold):
            g = 90 + int(140 * abs(math.sin(0.9 * k + idx)))
            frames.append(render(visible, cursor_gray=g))
    for k in range(16):
        g = 90 + int(140 * abs(math.sin(0.9 * k + 9)))
        frames.append(render(visible, cursor_gray=g))

    frames[0].save(
        ASSETS / "telegram-demo.gif", save_all=True, append_images=frames[1:],
        duration=100, loop=0, optimize=True)
    return len(frames), H


typing_frame = [0]


if __name__ == "__main__":
    make_logo()
    print("logo.png written")
    make_banner()
    print("banner.png written")
    n = make_pipeline_gif()
    print(f"pipeline.gif written ({n} frames)")
    n2, h = make_telegram_gif()
    print(f"telegram-demo.gif written ({n2} frames, height {h})")
