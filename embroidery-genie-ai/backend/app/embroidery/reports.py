"""Production paperwork: thread chart and run sheet.

Both are the documents a machine operator actually needs taped to the head:
what colour goes in what needle, in what order, and what the machine setup is.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image as RLImage,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .pattern import EmbroideryPattern

BRAND = colors.HexColor("#7C3AED")
INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")


def _styles():
    sheet = getSampleStyleSheet()
    sheet.add(ParagraphStyle("GenieTitle", parent=sheet["Title"], textColor=INK, fontSize=20,
                             spaceAfter=4, alignment=0))
    sheet.add(ParagraphStyle("GenieSub", parent=sheet["Normal"], textColor=MUTED, fontSize=9,
                             spaceAfter=14))
    sheet.add(ParagraphStyle("GenieH2", parent=sheet["Heading2"], textColor=BRAND, fontSize=12,
                             spaceBefore=14, spaceAfter=6))
    sheet.add(ParagraphStyle("GenieBody", parent=sheet["Normal"], textColor=INK, fontSize=9.5,
                             leading=14))
    return sheet


def _header(story, sheet, title: str, subtitle: str) -> None:
    story.append(Paragraph(title, sheet["GenieTitle"]))
    story.append(Paragraph(subtitle, sheet["GenieSub"]))


def _table(data, col_widths, header_bg=BRAND):
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#E5E7EB")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def build_thread_chart(
    pattern: EmbroideryPattern,
    design_name: str,
    preview_png: bytes | None = None,
) -> bytes:
    """Colour-by-colour sew sequence with swatches."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title=f"{design_name} - Thread Chart", author="Embroidery Genie AI",
    )
    sheet = _styles()
    story = []
    summary = pattern.summary()

    _header(
        story, sheet, f"{design_name} — Thread Chart",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"Embroidery Genie AI",
    )

    if preview_png:
        try:
            story.append(RLImage(io.BytesIO(preview_png), width=3.2 * inch, height=3.2 * inch))
            story.append(Spacer(1, 12))
        except Exception:
            pass

    rows = [["#", "Swatch", "Thread", "Code", "Hex", "Stitches", "Technique"]]
    for color in summary["colors"]:
        rows.append([
            str(color["index"] + 1), "", color["name"], color["code"] or "—",
            color["hex"], f"{color['stitches']:,}", color["technique"],
        ])

    table = _table(rows, [0.4 * inch, 0.7 * inch, 1.7 * inch, 0.9 * inch,
                          0.8 * inch, 0.9 * inch, 0.9 * inch])
    swatch_styles = []
    for i, color in enumerate(summary["colors"], start=1):
        swatch_styles.append(("BACKGROUND", (1, i), (1, i), colors.HexColor(color["hex"])))
    table.setStyle(TableStyle(swatch_styles))
    story.append(table)

    story.append(Paragraph("Design totals", sheet["GenieH2"]))
    totals = [
        ["Stitches", f"{summary['stitch_count']:,}"],
        ["Colours", str(summary["color_count"])],
        ["Size", f"{summary['width_mm']} × {summary['height_mm']} mm"],
        ["Trims", f"{summary['trim_count']:,}"],
        ["Thread used (est.)", f"{summary['thread_length_m']} m"],
    ]
    story.append(_table([["Metric", "Value"]] + totals, [2.5 * inch, 2.5 * inch]))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "Thread colours are matched perceptually against the selected catalogue. "
        "Confirm against a physical colour card before a production run — screen "
        "colour and thread sheen never match exactly.",
        sheet["GenieBody"],
    ))

    doc.build(story)
    return buffer.getvalue()


def build_production_notes(
    pattern: EmbroideryPattern,
    design_name: str,
    fabric: dict,
    machine: dict | None = None,
    issues: list[dict] | None = None,
    runtime_minutes: float | None = None,
    order: dict | None = None,
) -> bytes:
    """The operator run sheet: setup, sequence, warnings."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=LETTER,
        leftMargin=0.7 * inch, rightMargin=0.7 * inch,
        topMargin=0.7 * inch, bottomMargin=0.7 * inch,
        title=f"{design_name} - Production Notes", author="Embroidery Genie AI",
    )
    sheet = _styles()
    story = []
    summary = pattern.summary()

    _header(
        story, sheet, f"{design_name} — Production Notes",
        f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        "Embroidery Genie AI",
    )

    if order:
        story.append(Paragraph("Order", sheet["GenieH2"]))
        story.append(_table(
            [["Field", "Value"]] + [
                ["Order", str(order.get("number", "—"))],
                ["Customer", str(order.get("customer", "—"))],
                ["Quantity", str(order.get("quantity", "—"))],
                ["Placement", str(order.get("placement", "—"))],
                ["Due", str(order.get("due_date", "—"))],
            ],
            [2.0 * inch, 4.0 * inch],
        ))

    story.append(Paragraph("Machine setup", sheet["GenieH2"]))
    setup_rows = [
        ["Fabric", fabric.get("name", "—")],
        ["Stabiliser", fabric.get("stabilizer", "—")],
        ["Needle", fabric.get("needle", "—")],
        ["Topping", fabric.get("topping", "none")],
        ["Speed", f"{int(fabric.get('speed_factor', 1.0) * 100)}% of machine max"],
    ]
    if machine:
        setup_rows.extend([
            ["Machine", f"{machine.get('brand', '')} {machine.get('model', '')}".strip() or "—"],
            ["Hoop", machine.get("hoop", "—")],
            ["Needles", str(machine.get("needle_count", "—"))],
        ])
    story.append(_table([["Setting", "Value"]] + setup_rows, [2.0 * inch, 4.0 * inch]))

    story.append(Paragraph("Run data", sheet["GenieH2"]))
    run_rows = [
        ["Stitch count", f"{summary['stitch_count']:,}"],
        ["Colour changes", str(max(0, summary["color_count"] - 1))],
        ["Trims", f"{summary['trim_count']:,}"],
        ["Finished size", f"{summary['width_mm']} × {summary['height_mm']} mm"],
        ["Thread (est.)", f"{summary['thread_length_m']} m"],
    ]
    if runtime_minutes is not None:
        run_rows.append(["Est. run time", f"{runtime_minutes:.1f} min per piece"])
    story.append(_table([["Metric", "Value"]] + run_rows, [2.0 * inch, 4.0 * inch]))

    story.append(Paragraph("Sew sequence", sheet["GenieH2"]))
    seq_rows = [["Step", "Colour", "Thread", "Stitches", "Notes"]]
    for color in summary["colors"]:
        seq_rows.append([
            str(color["index"] + 1), color["hex"], color["name"],
            f"{color['stitches']:,}", color["label"] or color["technique"],
        ])
    seq_table = _table(seq_rows, [0.5 * inch, 0.9 * inch, 1.7 * inch, 0.9 * inch, 2.0 * inch])
    seq_table.setStyle(TableStyle([
        ("BACKGROUND", (1, i), (1, i), colors.HexColor(c["hex"]))
        for i, c in enumerate(summary["colors"], start=1)
    ]))
    seq_table.setStyle(TableStyle([("TEXTCOLOR", (1, 1), (1, -1), colors.transparent)]))
    story.append(seq_table)

    if fabric.get("notes"):
        story.append(Paragraph("Fabric guidance", sheet["GenieH2"]))
        story.append(Paragraph(fabric["notes"], sheet["GenieBody"]))

    if issues:
        story.append(Paragraph("Pre-flight checks", sheet["GenieH2"]))
        issue_rows = [["Level", "Check", "Detail"]]
        for issue in issues:
            issue_rows.append([
                issue.get("level", "info").upper(),
                issue.get("code", ""),
                issue.get("message", ""),
            ])
        issue_table = _table(issue_rows, [0.8 * inch, 1.2 * inch, 4.0 * inch],
                             header_bg=colors.HexColor("#B45309"))
        issue_table.setStyle(TableStyle([("FONTSIZE", (0, 1), (-1, -1), 8)]))
        story.append(issue_table)

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Always sew a test piece on the same blank and stabiliser before "
        "running production quantities.",
        sheet["GenieBody"],
    ))

    doc.build(story)
    return buffer.getvalue()
