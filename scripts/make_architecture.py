"""Render the simplified architecture diagram served by /api/model_architecture.

Canonical Quick Strategy module names must stay identical to
app/agent/prompts.py and the steps trace returned by /api/execute.

Usage: python scripts/make_architecture.py  (requires Pillow)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import prompts  # noqa: E402

OUT = ROOT / "assets" / "architecture.png"

W, H = 1800, 1050

# Neutral surfaces plus four semantic colors:
# LLM reasoning, deterministic code, retrieval/data, and campaign/state.
BG = (12, 16, 24)
SURFACE = (18, 24, 35)
CAMPAIGN_SURFACE = (22, 22, 38)
CARD = (29, 38, 52)
LINE = (65, 78, 98)
FLOW = (184, 198, 216)
TEXT = (237, 242, 248)
MUTED = (160, 174, 194)
LLM = (92, 160, 255)
CODE = (247, 166, 79)
DATA = (76, 214, 151)
STATE = (171, 126, 246)

BOLD_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
REGULAR_FONTS = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

EXPECTED_TASKS = [
    "FilmAnalyzer",
    "CompanyMemory",
    "FestivalSearch",
    "RiskChecker",
    "MatchScorer",
    "RoadmapBuilder",
]


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    for path in BOLD_FONTS if bold else REGULAR_FONTS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def panel(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], *, fill: tuple[int, int, int]) -> None:
    draw.rounded_rectangle(xy, radius=24, fill=fill, outline=LINE, width=2)


def node(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    subtitle: str,
    *,
    color: tuple[int, int, int],
    title_size: int = 24,
    subtitle_size: int = 17,
    hybrid_color: tuple[int, int, int] | None = None,
) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=15, fill=CARD, outline=color, width=3)
    if hybrid_color is not None:
        draw.line((x0 + 14, y1 - 5, x1 - 14, y1 - 5), fill=hybrid_color, width=5)

    title_font = font(title_size, bold=True)
    subtitle_font = font(subtitle_size)
    draw.text(((x0 + x1) / 2, y0 + 15), title, font=title_font, fill=TEXT, anchor="ma")
    draw.text(
        ((x0 + x1) / 2, y1 - 15),
        subtitle,
        font=subtitle_font,
        fill=MUTED,
        anchor="md",
    )


def arrow_head(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: tuple[int, int, int],
    size: int = 12,
) -> None:
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    back = angle + math.pi
    left = (
        end[0] + size * math.cos(back - 0.62),
        end[1] + size * math.sin(back - 0.62),
    )
    right = (
        end[0] + size * math.cos(back + 0.62),
        end[1] + size * math.sin(back + 0.62),
    )
    draw.polygon((end, left, right), fill=color)


def dashed_segment(
    draw: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    color: tuple[int, int, int],
    width: int,
) -> None:
    length = math.dist(start, end)
    if length == 0:
        return
    dash, gap = 12, 9
    ux = (end[0] - start[0]) / length
    uy = (end[1] - start[1]) / length
    position = 0.0
    while position < length:
        stop = min(position + dash, length)
        draw.line(
            (
                start[0] + ux * position,
                start[1] + uy * position,
                start[0] + ux * stop,
                start[1] + uy * stop,
            ),
            fill=color,
            width=width,
        )
        position += dash + gap


def arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    *,
    color: tuple[int, int, int] = FLOW,
    width: int = 4,
    dashed: bool = False,
) -> None:
    for start, end in zip(points, points[1:]):
        if dashed:
            dashed_segment(draw, start, end, color=color, width=width)
        else:
            draw.line((start, end), fill=color, width=width)
    arrow_head(draw, points[-2], points[-1], color=color)


def arrow_label(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    text: str,
    *,
    color: tuple[int, int, int] = MUTED,
) -> None:
    label_font = font(17, bold=True)
    left, top, right, bottom = draw.textbbox(center, text, font=label_font, anchor="mm")
    draw.rounded_rectangle(
        (left - 9, top - 5, right + 9, bottom + 5),
        radius=8,
        fill=BG,
    )
    draw.text(center, text, font=label_font, fill=color, anchor="mm")


def legend(draw: ImageDraw.ImageDraw) -> None:
    items = [
        ("LLM reasoning", LLM),
        ("Code / rules", CODE),
        ("Retrieval / data", DATA),
        ("Campaign / state", STATE),
    ]
    x = 1008
    y = 45
    label_font = font(17)
    for label, color in items:
        draw.rounded_rectangle((x, y, x + 17, y + 17), radius=5, fill=color)
        draw.text((x + 27, y - 1), label, font=label_font, fill=MUTED)
        x += int(draw.textlength(label, font=label_font)) + 72


def main() -> None:
    if prompts.TASK_CATALOG != EXPECTED_TASKS:
        raise RuntimeError(
            "Architecture module list is stale: "
            f"expected {EXPECTED_TASKS!r}, got {prompts.TASK_CATALOG!r}"
        )

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    draw.text(
        (48, 35),
        "The Distributor — Agent Architecture",
        font=font(42, bold=True),
        fill=TEXT,
    )
    draw.text(
        (50, 86),
        "Quick Strategy + stateful Campaign replanning",
        font=font(20),
        fill=MUTED,
    )
    legend(draw)

    # ---------------------------------------------------------------- Quick Strategy
    panel(draw, (30, 125, W - 30, 575), fill=SURFACE)
    draw.text((55, 145), "QUICK STRATEGY", font=font(24, bold=True), fill=TEXT)
    draw.text(
        (267, 148),
        "— Builds the initial evidence-grounded festival plan",
        font=font(18),
        fill=MUTED,
    )

    node(draw, (45, 315, 220, 400), "Film Brief", "user input", color=LINE)
    node(draw, (260, 315, 430, 400), "Planner", "work plan", color=CODE)

    executor = (470, 190, 1325, 545)
    draw.rounded_rectangle(executor, radius=20, fill=BG, outline=CODE, width=3)
    draw.text((495, 209), "Executor", font=font(27, bold=True), fill=CODE)
    draw.text((495, 244), "ordered execution", font=font(17), fill=MUTED)

    # External retrieval sources. Supabase also persists Campaign State below;
    # only its high-level data role is shown here.
    node(
        draw,
        (790, 205, 1020, 275),
        "Supabase",
        "festival + company data",
        color=DATA,
        title_size=22,
        subtitle_size=16,
    )
    node(
        draw,
        (1055, 205, 1295, 275),
        "Pinecone",
        "festival retrieval",
        color=DATA,
        title_size=22,
        subtitle_size=16,
    )

    top_nodes = [
        ((495, 305, 745, 380), "FilmAnalyzer", "film facts", LLM),
        ((770, 305, 1020, 380), "CompanyMemory", "past relationships", DATA),
        ((1045, 305, 1295, 380), "FestivalSearch", "festival retrieval", DATA),
    ]
    bottom_nodes = [
        ((495, 445, 745, 520), "RiskChecker", "eligibility & risk", CODE, None),
        ((770, 445, 1020, 520), "MatchScorer", "LLM + code", LLM, CODE),
        ((1045, 445, 1295, 520), "RoadmapBuilder", "submission roadmap", LLM, None),
    ]
    for xy, title, subtitle, color in top_nodes:
        node(draw, xy, title, subtitle, color=color)
    for xy, title, subtitle, color, hybrid in bottom_nodes:
        node(draw, xy, title, subtitle, color=color, hybrid_color=hybrid)

    node(draw, (1360, 445, 1545, 520), "Replanner", "validate / repair", color=CODE)
    node(
        draw,
        (1580, 445, 1755, 520),
        "Festival Strategy",
        "route + actions",
        color=LINE,
        title_size=22,
    )

    arrow(draw, [(220, 357), (260, 357)])
    arrow(draw, [(430, 357), (495, 342)])
    arrow(draw, [(745, 342), (770, 342)])
    arrow(draw, [(1020, 342), (1045, 342)])
    arrow(
        draw,
        [(1295, 342), (1320, 342), (1320, 412), (480, 412), (480, 482), (495, 482)],
    )
    arrow(draw, [(745, 482), (770, 482)])
    arrow(draw, [(1020, 482), (1045, 482)])
    arrow(draw, [(1295, 482), (1360, 482)])
    arrow(draw, [(1545, 482), (1580, 482)])

    arrow(draw, [(905, 275), (895, 305)], color=DATA, width=3)
    arrow(draw, [(1020, 240), (1035, 240), (1035, 290), (1120, 305)], color=DATA, width=3)
    arrow(draw, [(1175, 275), (1170, 305)], color=DATA, width=3)

    # ------------------------------------------------------------- Campaign Workspace
    panel(draw, (30, 595, W - 30, 1025), fill=CAMPAIGN_SURFACE)
    draw.text((55, 615), "CAMPAIGN WORKSPACE", font=font(24, bold=True), fill=TEXT)
    draw.text(
        (346, 618),
        "— Keeps the strategy current as real events happen",
        font=font(18),
        fill=MUTED,
    )

    node(
        draw,
        (45, 780, 280, 870),
        "Initial Strategy",
        "+ evidence from Quick Strategy",
        color=LINE,
        title_size=22,
        subtitle_size=16,
    )
    node(
        draw,
        (400, 780, 635, 870),
        "Campaign Workspace",
        "persistent workspace",
        color=STATE,
        title_size=22,
    )
    node(draw, (740, 780, 975, 870), "Campaign State", "saved decisions", color=STATE)
    node(
        draw,
        (1190, 780, 1430, 870),
        "CampaignPlanner",
        "next viable route",
        color=CODE,
    )
    node(
        draw,
        (1520, 780, 1760, 870),
        "Updated Strategy",
        "versioned route",
        color=LINE,
    )

    node(
        draw,
        (740, 680, 975, 750),
        "Human Event",
        "submit / reject / screen",
        color=STATE,
        title_size=22,
        subtitle_size=16,
    )
    node(
        draw,
        (905, 920, 1140, 995),
        "Premiere Ledger",
        "derived film history",
        color=STATE,
        title_size=22,
        subtitle_size=16,
    )

    arrow(draw, [(280, 825), (400, 825)], color=STATE)
    arrow_label(draw, (340, 798), "reuse evidence", color=STATE)
    arrow(draw, [(635, 825), (740, 825)], color=STATE)
    arrow(draw, [(857, 750), (857, 780)], color=STATE)
    arrow(draw, [(975, 825), (1190, 825)], color=STATE)
    arrow_label(draw, (1082, 800), "replan", color=CODE)
    arrow(draw, [(1430, 825), (1520, 825)], color=FLOW)
    arrow(draw, [(857, 870), (940, 920)], color=STATE)
    arrow(
        draw,
        [(1140, 957), (1165, 957), (1165, 855), (1190, 855)],
        color=STATE,
        width=3,
    )

    node(
        draw,
        (45, 925, 280, 1000),
        "What-if Scenario",
        "hypothetical event",
        color=LINE,
        title_size=22,
        subtitle_size=16,
    )
    node(
        draw,
        (400, 925, 690, 1000),
        "CampaignScenarioEngine",
        "in-memory copy",
        color=STATE,
        title_size=21,
        subtitle_size=16,
    )
    node(
        draw,
        (1520, 925, 1760, 1000),
        "Scenario Result",
        "no saved change",
        color=LINE,
        title_size=22,
        subtitle_size=16,
    )

    arrow(draw, [(280, 962), (400, 962)], color=STATE, dashed=True)
    arrow(
        draw,
        [(690, 962), (710, 962), (710, 1010), (1310, 1010), (1310, 870)],
        color=STATE,
        dashed=True,
        width=3,
    )
    arrow_label(draw, (1005, 1010), "same planner", color=STATE)
    arrow(
        draw,
        [(1430, 850), (1470, 850), (1470, 962), (1520, 962)],
        color=STATE,
        dashed=True,
        width=3,
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, "PNG", optimize=True)
    print(f"wrote {OUT} ({W}x{H}, {OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
