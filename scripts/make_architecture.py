"""Render assets/architecture.png — the diagram served by /api/model_architecture.

Module names here MUST stay identical to app/agent/prompts.py and the `steps`
trace returned by /api/execute.

Usage:  python scripts/make_architecture.py   (requires Pillow: pip install pillow)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "architecture.png"

W, H = 1680, 1010
BG = (14, 17, 23)
PANEL = (28, 35, 48)
LINE = (58, 70, 90)
TEXT = (230, 237, 243)
MUTED = (150, 162, 180)
ACCENT = (240, 163, 94)
BLUE = (110, 168, 254)
GREEN = (110, 231, 168)
PURPLE = (183, 148, 246)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    order = FONT_CANDIDATES if bold else FONT_CANDIDATES[1:] + FONT_CANDIDATES[:1]
    for path in order:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def wrap(draw, text, fnt, max_width):
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=fnt) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def box(draw, xy, title, subtitle="", *, accent=BLUE, fill=PANEL, title_size=26, sub_size=17):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=14, fill=fill, outline=accent, width=3)
    title_font, sub_font = font(title_size, bold=True), font(sub_size)
    lines = wrap(draw, subtitle, sub_font, (x1 - x0) - 28) if subtitle else []
    block_h = title_size + (6 + len(lines) * (sub_size + 5) if lines else 0)
    y = y0 + ((y1 - y0) - block_h) / 2
    draw.text(((x0 + x1) / 2, y), title, font=title_font, fill=TEXT, anchor="ma")
    y += title_size + 6
    for line in lines:
        draw.text(((x0 + x1) / 2, y), line, font=sub_font, fill=MUTED, anchor="ma")
        y += sub_size + 5


def arrow(draw, start, end, *, color=ACCENT, width=3, dashed=False, label="", label_offset=(0, -20)):
    x0, y0 = start
    x1, y1 = end
    if dashed:
        total = max(abs(x1 - x0), abs(y1 - y0), 1)
        steps = int(total / 14)
        for i in range(steps):
            t0, t1 = i / steps, (i + 0.55) / steps
            draw.line(
                [x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0, x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1],
                fill=color, width=width,
            )
    else:
        draw.line([x0, y0, x1, y1], fill=color, width=width)

    head = 11
    if abs(x1 - x0) >= abs(y1 - y0):
        sign = 1 if x1 >= x0 else -1
        draw.polygon(
            [(x1, y1), (x1 - sign * head, y1 - head * 0.7), (x1 - sign * head, y1 + head * 0.7)],
            fill=color,
        )
    else:
        sign = 1 if y1 >= y0 else -1
        draw.polygon(
            [(x1, y1), (x1 - head * 0.7, y1 - sign * head), (x1 + head * 0.7, y1 - sign * head)],
            fill=color,
        )

    if label:
        draw.text(
            ((x0 + x1) / 2 + label_offset[0], (y0 + y1) / 2 + label_offset[1]),
            label, font=font(16), fill=MUTED, anchor="mm",
        )


def main() -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    draw.text((60, 44), "The Distributor", font=font(40, bold=True), fill=TEXT)
    draw.text(
        (60, 96),
        "Plan-and-Execute agent architecture  ·  Planner creates tasks → Executor uses tools → Replanner revises when needed",
        font=font(19), fill=MUTED,
    )
    draw.line([60, 138, W - 60, 138], fill=LINE, width=2)

    # Top row: input → Planner → Executor container → Replanner → output
    box(draw, (60, 180, 300, 268), "User Prompt", "Film description", accent=LINE, title_size=22, sub_size=15)
    box(draw, (360, 180, 640, 268), "Planner", "Builds the ordered task plan", accent=ACCENT, title_size=24)

    exec_box = (60, 330, 1120, 700)
    draw.rounded_rectangle(exec_box, radius=18, fill=(20, 25, 34), outline=ACCENT, width=3)
    draw.text((84, 348), "Executor", font=font(26, bold=True), fill=ACCENT)
    draw.text((84, 382), "Runs planned tasks through the tool modules", font=font(16), fill=MUTED)

    tools = [
        ("FilmAnalyzer", "Genre, themes, premiere status, audience", BLUE),
        ("FestivalSearch", "Semantic retrieval over the festival index", GREEN),
        ("CompanyMemory", "Prior submissions, acceptances, awards", GREEN),
        ("MatchScorer", "LLM rates 5 dimensions 0-5 → weighted 0-100 in code", BLUE),
        ("RiskChecker", "Premiere, eligibility and deadline risk", BLUE),
        ("RoadmapBuilder", "Bucketed strategy: submit / delay / avoid", BLUE),
    ]
    left, top, bw, bh, gx, gy = 92, 420, 316, 118, 22, 22
    for index, (name, desc, color) in enumerate(tools):
        col, row = index % 3, index // 3
        x0 = left + col * (bw + gx)
        y0 = top + row * (bh + gy)
        box(draw, (x0, y0, x0 + bw, y0 + bh), name, desc, accent=color, title_size=21, sub_size=15)

    box(draw, (1220, 420, 1620, 520), "Pinecone — Festival Index", "Festival identity embeddings (focus, themes, past lineups, winners)", accent=GREEN, title_size=20, sub_size=14)
    box(draw, (1220, 560, 1620, 660), "Supabase", "Festival facts, company memory, run logs", accent=GREEN, title_size=20, sub_size=14)
    box(draw, (760, 180, 1120, 268), "Replanner", "Revise tasks or stop", accent=PURPLE, title_size=24)
    box(draw, (1220, 180, 1620, 300), "Festival Strategy Roadmap", "Ranked plan · match scores · reasoning · risks · calendar", accent=ACCENT, title_size=21, sub_size=15)

    arrow(draw, (300, 224), (352, 224))
    arrow(draw, (500, 268), (500, 322), label="plan", label_offset=(38, 0))
    arrow(draw, (940, 322), (940, 268), label="results", label_offset=(52, 0))
    arrow(draw, (1120, 224), (1212, 224), label="ready", label_offset=(0, -20))
    arrow(draw, (756, 224), (648, 224), color=PURPLE, dashed=True, label="replan", label_offset=(0, -20))
    arrow(draw, (1128, 470), (1212, 470), color=GREEN, dashed=True)
    arrow(draw, (1128, 610), (1212, 610), color=GREEN, dashed=True)

    draw.text(
        (60, 740),
        "Data access  ·  FestivalSearch queries the Pinecone festival index and reads festival facts from Supabase  ·  CompanyMemory reads the distribution company's history from Supabase",
        font=font(17), fill=MUTED,
    )
    draw.text(
        (60, 776),
        "Scoring  ·  LLM-rated: thematic fit 25  ·  genre fit 15  ·  past lineup / winner similarity 20  ·  company relationship 15  ·  strategic value 15    Computed in code: deadline urgency 10  ·  premiere risk = penalty",
        font=font(17), fill=MUTED,
    )
    draw.text(
        (60, 812),
        "Roadmap buckets  ·  Submit First  ·  Prioritize Next  ·  Leverage  ·  Hold / Avoid",
        font=font(17), fill=MUTED,
    )
    draw.line([60, 850, W - 60, 850], fill=LINE, width=2)
    draw.text(
        (60, 874),
        "Every module above appears by the same name in the `steps` trace returned by POST /api/execute.  ·  A world-premiere requirement counts as an opportunity while the film still has its premiere available.",
        font=font(17), fill=MUTED,
    )

    legend = [("LLM reasoning module", BLUE), ("Data / retrieval tool", GREEN), ("Control flow", ACCENT), ("Replanning loop", PURPLE)]
    x = 60
    for label, color in legend:
        draw.rounded_rectangle((x, 926, x + 22, 948), radius=5, fill=color)
        draw.text((x + 34, 927), label, font=font(17), fill=MUTED)
        x += int(draw.textlength(label, font=font(17))) + 90

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, "PNG")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
