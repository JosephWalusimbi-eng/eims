from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass(frozen=True)
class ImmunizationCardRow:
    due_date: date
    vaccines: list[str]


def build_immunization_card_pdf(
    *,
    child_name: str,
    child_dob: date,
    child_gender: str,
    caregiver_name: str,
    caregiver_phone: str,
    rows: list[ImmunizationCardRow],
) -> bytes:
    """
    Create a simple printable immunization card PDF.
    """
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title="Immunization Card",
        author="EIMS",
    )
    styles = getSampleStyleSheet()

    story = []
    story.append(Paragraph("Electronic Immunization Card (EIMS)", styles["Title"]))
    story.append(Spacer(1, 10))

    story.append(
        Paragraph(
            f"<b>Child:</b> {child_name} &nbsp;&nbsp; <b>DOB:</b> {child_dob.isoformat()} &nbsp;&nbsp; <b>Gender:</b> {child_gender}",
            styles["Normal"],
        )
    )
    story.append(
        Paragraph(
            f"<b>Caregiver:</b> {caregiver_name} &nbsp;&nbsp; <b>Phone:</b> {caregiver_phone}",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 14))

    table_data: list[list[str]] = [["Due date", "Vaccines"]]
    for r in rows:
        table_data.append([r.due_date.isoformat(), ", ".join(r.vaccines)])

    table = Table(table_data, colWidths=[120, 380])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 11),
                ("ALIGN", (0, 0), (-1, 0), "LEFT"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(table)

    story.append(Spacer(1, 14))
    story.append(
        Paragraph(
            "Keep this card for your records. If you miss a visit, please go as soon as possible.",
            styles["Italic"],
        )
    )

    doc.build(story)
    return buffer.getvalue()

