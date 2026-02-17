import os
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.platypus import Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT


def generate_pdf(
    text: str,
    output_path: str,
    sponsor: dict | None = None,
) -> str:
    """
    Genera un PDF dalla trascrizione.
    sponsor: {"name": str, "footer_text": str, "logo_url": str (opzionale)}
    Restituisce il percorso del file creato.
    """
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=2.5 * cm,
        rightMargin=2.5 * cm,
        topMargin=2.5 * cm,
        bottomMargin=3.0 * cm,
    )

    styles = getSampleStyleSheet()

    style_title = ParagraphStyle(
        "Title",
        parent=styles["Heading1"],
        fontSize=18,
        spaceAfter=4,
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_LEFT,
    )
    style_subtitle = ParagraphStyle(
        "Subtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#555555"),
        spaceAfter=12,
        alignment=TA_LEFT,
    )
    style_body = ParagraphStyle(
        "Body",
        parent=styles["Normal"],
        fontSize=10,
        leading=16,
        textColor=colors.HexColor("#1a1a1a"),
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    style_timestamp = ParagraphStyle(
        "Timestamp",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#888888"),
        spaceBefore=8,
        spaceAfter=2,
        fontName="Helvetica-Bold",
    )
    style_footer = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=8,
        textColor=colors.HexColor("#777777"),
        alignment=TA_CENTER,
    )

    story = []

    # --- Header ---
    story.append(Paragraph("AudioLecture", style_title))
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    story.append(Paragraph(f"Trascrizione Audio  ·  {now}", style_subtitle))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd")))
    story.append(Spacer(1, 0.4 * cm))

    # --- Corpo trascrizione ---
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("[") and "]" in line:
            bracket_end = line.index("]")
            timestamp = line[: bracket_end + 1]
            content = line[bracket_end + 1 :].strip()
            story.append(Paragraph(timestamp, style_timestamp))
            if content:
                story.append(Paragraph(_escape(content), style_body))
        else:
            story.append(Paragraph(_escape(line), style_body))

    story.append(Spacer(1, 0.6 * cm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#dddddd")))
    story.append(Spacer(1, 0.3 * cm))

    # --- Footer sponsor ---
    if sponsor and sponsor.get("footer_text"):
        story.append(Paragraph(sponsor["footer_text"], style_footer))
    else:
        story.append(Paragraph("Trascritto con AudioLecture · @AudioLectureBot", style_footer))

    doc.build(story)
    return output_path


def _escape(text: str) -> str:
    """Escape caratteri ReportLab XML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
