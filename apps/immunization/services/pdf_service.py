from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


@dataclass(frozen=True)
class DoseEntry:
    """One dose on the card. `given_on` is None while the dose is outstanding."""
    vaccine: str
    given_on: date | None = None

    @property
    def given(self) -> bool:
        return self.given_on is not None


@dataclass(frozen=True)
class ImmunizationCardRow:
    due_date: date
    vaccines: list[str]
    # Per-dose state. When omitted the card renders every dose as outstanding,
    # which is the pre-tick behaviour and keeps older callers working.
    doses: list[DoseEntry] | None = None

    def entries(self) -> list[DoseEntry]:
        return self.doses if self.doses is not None else [DoseEntry(v) for v in self.vaccines]


TICK_GREEN = colors.HexColor("#198754")
BOX_GREY = colors.HexColor("#6c757d")


class StatusMark(Flowable):
    """A tick box drawn as vector paths, with a text label beside it.

    Drawn rather than typeset because no built-in PDF font carries a reliable
    check-mark glyph: ZapfDingbats renders as an empty square in several
    viewers that do not embed it. Vector strokes look the same everywhere, and
    the label keeps the card legible when printed in greyscale.
    """

    SIZE = 11

    def __init__(self, given: bool):
        super().__init__()
        self.given = given
        self.width = 70
        self.height = self.SIZE + 2

    def draw(self):
        c, n = self.canv, self.SIZE
        c.setLineWidth(0.9)
        c.setStrokeColor(TICK_GREEN if self.given else BOX_GREY)
        c.rect(0, 1, n, n, stroke=1, fill=0)
        if self.given:
            c.setLineWidth(2)
            c.setLineCap(1)
            p = c.beginPath()
            p.moveTo(2, 1 + n * 0.5)
            p.lineTo(n * 0.42, 1 + 2.2)
            p.lineTo(n - 1.5, 1 + n - 1.5)
            c.drawPath(p, stroke=1, fill=0)
        c.setFillColor(TICK_GREEN if self.given else BOX_GREY)
        c.setFont("Helvetica-Bold" if self.given else "Helvetica", 9)
        c.drawString(n + 5, 3, "Given" if self.given else "Pending")


def build_immunization_card_pdf(
    *,
    child_name: str,
    child_dob: date,
    child_gender: str,
    caregiver_name: str,
    caregiver_phone: str,
    rows: list[ImmunizationCardRow],
    updated_at: datetime | None = None,
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

    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=10, leading=13)
    head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)

    table_data = [[Paragraph(h, head) for h in ("Due date", "Vaccine", "Status", "Date given")]]
    for r in rows:
        for entry in r.entries():
            table_data.append([
                Paragraph(r.due_date.isoformat(), cell),
                Paragraph(entry.vaccine, cell),
                StatusMark(entry.given),
                Paragraph(entry.given_on.isoformat() if entry.given else "-", cell),
            ])

    given_count = sum(1 for r in rows for e in r.entries() if e.given)
    total_count = sum(len(r.entries()) for r in rows)
    story.append(Paragraph(f"<b>Progress:</b> {given_count} of {total_count} doses given", styles["Normal"]))
    story.append(Spacer(1, 8))

    table = Table(table_data, colWidths=[90, 190, 90, 110], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d6efd")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.lightgrey]),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)

    story.append(Spacer(1, 14))
    story.append(
        Paragraph(
            "Keep this card for your records. If you miss a visit, please go as soon as possible."
            + (f" Card last updated {updated_at:%Y-%m-%d %H:%M}." if updated_at else ""),
            styles["Italic"],
        )
    )

    doc.build(story)
    return buffer.getvalue()

