"""
Builds a downloadable PDF of the final festival plan.

Standard PDF fonts are used (no font files needed), so text is cleaned to
the characters those fonts can draw: the rupee sign becomes "INR" and
unsupported symbols are dropped.
"""

import io
import unicodedata
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

GREEN = colors.HexColor("#344B35")
LIGHT = colors.HexColor("#F4F7F3")
LINE = colors.HexColor("#DFE5DA")
GREY = colors.HexColor("#66705F")

TONES = {
    "good": (colors.HexColor("#E5EDDD"), colors.HexColor("#2F4A26")),
    "warn": (colors.HexColor("#FBF3DD"), colors.HexColor("#6A5312")),
    "bad": (colors.HexColor("#FBE9E6"), colors.HexColor("#8A2A1D")),
}

DECISIONS = {
    "READY_FOR_HUMAN_REVIEW": (
        "Ready for human review", "good",
        "All automated checks passed. A qualified person must still approve this plan.",
    ),
    "HUMAN_REVIEW_REQUIRED": (
        "Human review required", "warn",
        "Automated checks passed, but the items below need real-world confirmation.",
    ),
    "REVISION_REQUIRED": (
        "Revision required", "bad",
        "This plan did not pass every automated check. Resolve the items below before use.",
    ),
}

CHAR_MAP = {
    "\u20b9": "INR ",
    "\u2010": "-", "\u2011": "-", "\u2212": "-",
    "\u2192": "->", "\u2190": "<-",
    "\u2713": "", "\u2714": "",
}


def clean(value) -> str:
    """Make text safe for the built-in PDF fonts."""
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    for old, new in CHAR_MAP.items():
        text = text.replace(old, new)
    return text.encode("cp1252", "ignore").decode("cp1252")


def esc(value) -> str:
    return escape(clean(value))


def humanize(key) -> str:
    text = str(key).replace("_", " ").strip()
    return text[:1].upper() + text[1:]


def title_case(key) -> str:
    return " ".join(w.capitalize() for w in str(key).replace("_", " ").split())


def inr(value) -> str:
    """Indian digit grouping, e.g. 500000 -> 5,00,000."""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return str(value)

    sign = "-" if number < 0 else ""
    digits = str(abs(number))
    if len(digits) <= 3:
        return f"{sign}{digits}"

    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}{','.join(groups)},{tail}"


def fmt_scalar(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)


def is_scalar(value) -> bool:
    return not isinstance(value, (dict, list))


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --------------------------------------------------------------- styles

_base = getSampleStyleSheet()

BODY = ParagraphStyle(
    "Body", parent=_base["BodyText"], fontName="Helvetica",
    fontSize=9.5, leading=13.5, textColor=colors.HexColor("#30372E"),
)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8, leading=11, textColor=GREY)
TITLE = ParagraphStyle(
    "Title", parent=BODY, fontName="Helvetica-Bold",
    fontSize=22, leading=26, textColor=GREEN, spaceAfter=4,
)
SUBTITLE = ParagraphStyle("Subtitle", parent=BODY, fontSize=12, leading=16, textColor=GREY)
H2 = ParagraphStyle(
    "H2", parent=BODY, fontName="Helvetica-Bold", fontSize=13, leading=17,
    textColor=GREEN, spaceBefore=14, spaceAfter=6, keepWithNext=True,
)
H3 = ParagraphStyle(
    "H3", parent=BODY, fontName="Helvetica-Bold", fontSize=10.5, leading=14,
    spaceBefore=6, spaceAfter=3, keepWithNext=True,
)
CELL = ParagraphStyle("Cell", parent=BODY, fontSize=9, leading=12)
CELL_RIGHT = ParagraphStyle("CellRight", parent=CELL, alignment=TA_RIGHT)
CELL_HEAD = ParagraphStyle(
    "CellHead", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white,
)
CELL_HEAD_RIGHT = ParagraphStyle("CellHeadRight", parent=CELL_HEAD, alignment=TA_RIGHT)


def indent_style(level: int) -> ParagraphStyle:
    return ParagraphStyle(
        f"Indent{level}", parent=BODY,
        leftIndent=10 + level * 14, bulletIndent=level * 14 + 1,
        spaceAfter=2.5,
    )


# ------------------------------------------------------ generic renderer

def render_value(value, level: int = 0) -> list:
    """Turn any JSON-like value into readable flowables."""
    out = []
    style = indent_style(level)

    if isinstance(value, dict):
        out.extend(render_dict(value, level))
    elif isinstance(value, list):
        for item in value:
            if is_scalar(item):
                out.append(Paragraph(esc(fmt_scalar(item)), style, bulletText="-"))
            elif isinstance(item, dict):
                out.extend(render_dict(item, level, bullet=True))
                out.append(Spacer(1, 3))
            else:  # nested list
                out.extend(render_value(item, level + 1))
    else:
        out.append(Paragraph(esc(fmt_scalar(value)), style))

    return out


def render_dict(data: dict, level: int, bullet: bool = False) -> list:
    out = []
    style = indent_style(level)
    first = True

    # A list item made only of simple fields fits on one line.
    if bullet and data and all(is_scalar(v) for v in data.values()):
        parts = [
            f"<b>{esc(humanize(k))}:</b> {esc(fmt_scalar(v))}"
            for k, v in data.items()
        ]
        return [Paragraph("  |  ".join(parts), style, bulletText="-")]

    for key, value in data.items():
        label = f"<b>{esc(humanize(key))}</b>"
        bullet_text = "-" if (bullet and first) else None
        first = False

        if is_scalar(value):
            out.append(Paragraph(
                f"{label}: {esc(fmt_scalar(value))}", style, bulletText=bullet_text
            ))
        elif not value:
            out.append(Paragraph(f"{label}: -", style, bulletText=bullet_text))
        else:
            out.append(Paragraph(label, style, bulletText=bullet_text))
            out.extend(render_value(value, level + 1))

    return out


# ------------------------------------------------------------ components

def make_table(rows, col_widths, header=True, zebra=True, extra=None):
    table = Table(rows, colWidths=col_widths, repeatRows=1 if header else 0)
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        commands.append(("BACKGROUND", (0, 0), (-1, 0), GREEN))
    if zebra:
        for r in range(1 if header else 0, len(rows)):
            if r % 2 == 0:
                commands.append(("BACKGROUND", (0, r), (-1, r), LIGHT))
    table.setStyle(TableStyle(commands + (extra or [])))
    return table


def banner(text_lines, tone):
    background, foreground = TONES[tone]
    style = ParagraphStyle("Banner", parent=BODY, textColor=foreground)
    cells = [[[Paragraph(line, style) for line in text_lines]]]
    table = Table(cells, colWidths=[174 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), background),
        ("BOX", (0, 0), (-1, -1), 0.6, foreground),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def extract_budget(budget: dict):
    """Return (breakdown, declared_total, other_keys) or None if unusable."""
    if not isinstance(budget, dict):
        return None

    if isinstance(budget.get("breakdown"), dict):
        breakdown = budget["breakdown"]
        used = {"breakdown"}
    else:
        used = {"total_budget", "total_budget_INR", "currency", "notes"}
        breakdown = {k: v for k, v in budget.items() if k not in used}

    if not breakdown or not all(is_number(v) for v in breakdown.values()):
        return None

    declared = budget.get("total_budget", budget.get("total_budget_INR"))
    used = used | {"total_budget", "total_budget_INR"}
    others = {k: v for k, v in budget.items() if k not in used and k not in breakdown}
    return breakdown, declared, others


def budget_flowables(budget: dict, limit):
    parsed = extract_budget(budget)
    if parsed is None:
        return render_value(budget)

    breakdown, declared, others = parsed
    total = sum(breakdown.values())

    rows = [[
        Paragraph("Item", CELL_HEAD),
        Paragraph("Amount (INR)", CELL_HEAD_RIGHT),
        Paragraph("Share", CELL_HEAD_RIGHT),
    ]]
    for item, amount in breakdown.items():
        share = f"{amount / total * 100:.1f}%" if total else "-"
        rows.append([
            Paragraph(esc(humanize(item)), CELL),
            Paragraph(inr(amount), CELL_RIGHT),
            Paragraph(share, CELL_RIGHT),
        ])

    bold = ParagraphStyle("BoldCell", parent=CELL, fontName="Helvetica-Bold")
    bold_right = ParagraphStyle("BoldRight", parent=bold, alignment=TA_RIGHT)
    rows.append([
        Paragraph("Total allocated", bold),
        Paragraph(inr(total), bold_right),
        Paragraph("100%" if total else "-", bold_right),
    ])
    last = len(rows) - 1

    flow = [make_table(
        rows, [100 * mm, 45 * mm, 29 * mm],
        extra=[("LINEABOVE", (0, last), (-1, last), 1, GREEN)],
    )]

    notes = []
    if is_number(limit):
        remaining = limit - total
        notes.append(
            f"Budget limit: INR {inr(limit)}. "
            + (f"Unallocated: INR {inr(remaining)}." if remaining >= 0
               else f"Over the limit by INR {inr(-remaining)}.")
        )
    if is_number(declared) and declared != total:
        notes.append(f"Declared total (INR {inr(declared)}) differs from the sum of items.")
    if notes:
        flow.append(Spacer(1, 4))
        flow.append(Paragraph(esc(" ".join(notes)), SMALL))
    if others:
        flow.append(Spacer(1, 4))
        flow.extend(render_value(others))
    return flow


def unique(items):
    seen, result = set(), []
    for item in items:
        text = str(item).strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


# ----------------------------------------------------------- page chrome

def draw_footer(canvas, doc):
    canvas.saveState()
    width, _ = A4
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, width - 18 * mm, 14 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GREY)
    canvas.drawString(
        18 * mm, 9.5 * mm,
        "FestivaAI - AI-generated plan. Requires review and approval by qualified people.",
    )
    canvas.drawRightString(width - 18 * mm, 9.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


# ------------------------------------------------------------- main API

def build_plan_pdf(result: dict) -> bytes:
    """Create the PDF for a pipeline result and return its bytes."""
    plan = result.get("plan") or {}
    request = result.get("request") or {}
    evaluation = result.get("evaluation") or {}
    simulation = result.get("simulation") or None
    environment = result.get("environment") or {}

    name = request.get("festival_name") or plan.get("festival_name") or "Festival"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=20 * mm,
        title=clean(f"{name} - Festival Operations Plan"),
        author="FestivaAI",
    )

    story = []

    # ---- title and summary
    story.append(Paragraph("Festival Operations Plan", TITLE))
    story.append(Paragraph(esc(name), SUBTITLE))
    story.append(Spacer(1, 8))

    info = []
    if request:
        info = [
            ("Expected attendance", f"{inr(request.get('attendees'))} people"),
            ("Budget limit", f"INR {inr(request.get('budget'))}"),
            ("Venue", request.get("venue", "-")),
            ("Duration", request.get("duration", "-")),
        ]
    info.append(("Generated", datetime.now().strftime("%d %b %Y, %H:%M")))
    if result.get("attempts"):
        info.append(("Planning rounds", str(result["attempts"])))

    rows = [
        [Paragraph(f"<b>{esc(k)}</b>", CELL), Paragraph(esc(v), CELL)]
        for k, v in info
    ]
    story.append(make_table(rows, [48 * mm, 126 * mm], header=False))
    story.append(Spacer(1, 10))

    # ---- decision banner
    label, tone, text = DECISIONS.get(
        environment.get("decision"),
        ("Not available", "warn", "No environment decision was recorded."),
    )
    lines = [f"<b>{esc(label)}</b>", esc(text)]
    failed = [
        c.get("name") or c.get("check")
        for c in environment.get("environment_checks", [])
        if not c.get("passed")
    ]
    if failed:
        lines.append("Failed checks: " + esc(", ".join(failed)) + ".")
    story.append(banner(lines, tone))

    # ---- confirmations before execution
    confirm = unique(
        list(environment.get("external_verification_pending", []))
        + list(environment.get("unresolved_information", []))
    )
    if confirm:
        story.append(Paragraph("Confirm before the event", H2))
        story.append(Paragraph(
            "These details could not be known by the planner. Each needs a person "
            "to confirm it in the real world.", SMALL,
        ))
        story.append(Spacer(1, 4))
        rows = [[Paragraph("Done", CELL_HEAD), Paragraph("Item", CELL_HEAD)]]
        for item in confirm:
            rows.append([Paragraph("[   ]", CELL), Paragraph(esc(item), CELL)])
        story.append(make_table(rows, [16 * mm, 158 * mm]))

    # ---- the plan itself
    story.append(Paragraph("Plan", H2))

    for key, value in plan.items():
        if key == "festival_name":
            continue

        block = [Paragraph(esc(title_case(key)), H3)]

        if key == "budget_allocation":
            block.extend(budget_flowables(value, request.get("budget")))
        else:
            block.extend(render_value(value))

        # keep the heading with its first lines, but let long sections flow
        story.append(KeepTogether(block[:3]))
        story.extend(block[3:])
        story.append(Spacer(1, 4))

    # ---- quality review appendix
    if evaluation or simulation:
        story.append(Paragraph("Appendix: automated review", H2))
        story.append(Paragraph(
            "Scores come from AI models and are signals, not guarantees of safety.",
            SMALL,
        ))

    if evaluation:
        story.append(Paragraph("Judge evaluation", H3))
        story.append(Paragraph(
            f"Score: <b>{esc(evaluation.get('overall_score', '-'))}/100</b> - "
            f"verdict: <b>{esc(title_case(evaluation.get('verdict', 'unknown')))}</b>",
            BODY,
        ))

        rubric = evaluation.get("rubric_scores") or {}
        if rubric:
            story.append(Spacer(1, 4))
            rows = [[Paragraph("Dimension", CELL_HEAD), Paragraph("Score", CELL_HEAD_RIGHT)]]
            for dim, score in rubric.items():
                rows.append([
                    Paragraph(esc(title_case(dim)), CELL),
                    Paragraph(f"{esc(score)}/10", CELL_RIGHT),
                ])
            story.append(make_table(rows, [130 * mm, 44 * mm]))

        issues = [i for i in evaluation.get("issues", []) if isinstance(i, dict)]
        if issues:
            story.append(Paragraph("Judge issues", H3))
            for issue in issues:
                story.append(Paragraph(
                    f"<b>[{esc(str(issue.get('severity', '')).upper())}]</b> "
                    f"{esc(issue.get('issue', ''))}", indent_style(0), bulletText="-",
                ))
                if issue.get("explanation"):
                    story.append(Paragraph(esc(issue["explanation"]), indent_style(1)))

        recs = evaluation.get("recommendations") or []
        if recs:
            story.append(Paragraph("Judge recommendations", H3))
            story.extend(render_value(recs))

    if simulation:
        story.append(Paragraph("Simulated incidents", H3))
        story.append(Paragraph(
            f"Average resilience: <b>{esc(simulation.get('average_score', '-'))}/100</b>. "
            "An AI model played out each incident using only what the plan states.",
            BODY,
        ))
        story.append(Spacer(1, 4))

        rows = [[
            Paragraph("Scenario", CELL_HEAD),
            Paragraph("Result", CELL_HEAD),
            Paragraph("Score", CELL_HEAD_RIGHT),
        ]]
        for item in simulation.get("scenarios", []):
            rows.append([
                Paragraph(esc(item.get("name", "")), CELL),
                Paragraph("Held" if item.get("plan_held") else "Failed", CELL),
                Paragraph(f"{esc(item.get('resilience_score', '-'))}/100", CELL_RIGHT),
            ])
        story.append(make_table(rows, [100 * mm, 36 * mm, 38 * mm]))

        for item in simulation.get("scenarios", []):
            points = item.get("failure_points") or []
            fixes = item.get("recommended_fixes") or []
            if not points and not fixes:
                continue
            block = [Paragraph(esc(item.get("name", "")), H3)]
            if points:
                block.append(Paragraph("<b>Where it broke down</b>", indent_style(0)))
                block.extend(render_value(points, 1))
            if fixes:
                block.append(Paragraph("<b>Suggested fixes</b>", indent_style(0)))
                block.extend(render_value(fixes, 1))
            story.append(KeepTogether(block[:3]))
            story.extend(block[3:])

    if environment.get("notice"):
        story.append(Spacer(1, 10))
        story.append(Paragraph(esc(environment["notice"]), SMALL))

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return buffer.getvalue()