"""PDF renderer (ReportLab Platypus). Pure Python, no Word or system binaries needed.

A Unicode TrueType font is used when one is found on the system (for full
accent/punctuation coverage); otherwise it falls back to Helvetica.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.models.document import AnyDocument, ContentBlock, SourceRange
from app.services.documents.common import (
    document_sources,
    labels,
    metadata_lines,
    range_label,
    source_lines,
    split_bold,
    time_links,
)

logger = logging.getLogger(__name__)

INK = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
ACCENT = colors.HexColor("#1D4ED8")
RULE = colors.HexColor("#E5E7EB")
QUOTE_BG = colors.HexColor("#F3F4F6")

_FONT_CANDIDATES = [
    # (regular, bold, italic, bold-italic)
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf"),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
    ),
    (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf",
    ),
]


@lru_cache
def _fonts() -> tuple[str, str, str]:
    """Return (regular, bold, italic) font names."""
    for regular, bold, italic, bold_italic in _FONT_CANDIDATES:
        if all(Path(p).exists() for p in (regular, bold, italic, bold_italic)):
            try:
                pdfmetrics.registerFont(TTFont("DocSans", regular))
                pdfmetrics.registerFont(TTFont("DocSans-Bold", bold))
                pdfmetrics.registerFont(TTFont("DocSans-Italic", italic))
                pdfmetrics.registerFont(TTFont("DocSans-BoldItalic", bold_italic))
                pdfmetrics.registerFontFamily(
                    "DocSans", normal="DocSans", bold="DocSans-Bold", italic="DocSans-Italic", boldItalic="DocSans-BoldItalic"
                )
                return "DocSans", "DocSans-Bold", "DocSans-Italic"
            except Exception as exc:  # corrupt font file etc.
                logger.warning("Could not register font %s: %s", regular, exc)
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"


@lru_cache
def _styles() -> dict[str, ParagraphStyle]:
    regular, bold, italic = _fonts()
    base = ParagraphStyle("base", fontName=regular, fontSize=10.5, leading=15, textColor=INK, alignment=TA_LEFT, spaceAfter=6)
    return {
        "title": ParagraphStyle("title", parent=base, fontName=bold, fontSize=22, leading=27, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=base, fontName=bold, fontSize=15.5, leading=20, spaceBefore=16, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base, fontName=bold, fontSize=12.5, leading=17, spaceBefore=10, spaceAfter=4),
        "body": base,
        "meta_label": ParagraphStyle(
            "meta_label", parent=base, fontName=bold, fontSize=8.5, leading=11, textColor=MUTED, spaceAfter=0
        ),
        "meta": ParagraphStyle("meta", parent=base, fontSize=8.5, leading=11, spaceAfter=0),
        "time": ParagraphStyle("time", parent=base, fontSize=8.5, leading=11, textColor=MUTED, spaceAfter=6),
        "quote": ParagraphStyle("quote", parent=base, fontName=italic, leftIndent=10, rightIndent=10, textColor=INK),
        "note": ParagraphStyle("note", parent=base, fontName=italic, fontSize=10, textColor=MUTED, leftIndent=10),
        "cell": ParagraphStyle("cell", parent=base, fontSize=9, leading=12, spaceAfter=0),
        "cell_head": ParagraphStyle("cell_head", parent=base, fontName=bold, fontSize=9, leading=12, spaceAfter=0),
    }


def _markup(text: str) -> str:
    """Escape text for ReportLab's mini-markup and convert **bold**."""
    return "".join(f"<b>{escape(t)}</b>" if b else escape(t) for t, b in split_bold(" ".join(text.split())))


def _links_markup(doc: AnyDocument, ranges: Sequence[SourceRange]) -> str:
    return " · ".join(
        f'<link href="{escape(t.url)}" color="#1D4ED8"><u>{escape(t.label)}</u></link>' for t in time_links(doc, ranges)
    )


def _block(block: ContentBlock, doc: AnyDocument, width: float) -> list:
    st = _styles()
    links = _links_markup(doc, block.source_ranges) if block.source_ranges else ""
    flow: list = []
    if block.type == "paragraph":
        text = _markup(block.text) + (f' <font size="8.5">{links}</font>' if links else "")
        flow.append(Paragraph(text, st["body"]))
    elif block.type in ("quote", "note"):
        style = st["quote"] if block.type == "quote" else st["note"]
        prefix = f"<b>{escape(labels(doc.output_language)['note'])}:</b> " if block.type == "note" else ""
        text = prefix + _markup(block.text) + (f' <font size="8.5">{links}</font>' if links else "")
        box = Table([[Paragraph(text, style)]], colWidths=[width])
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), QUOTE_BG),
                    ("LINEBEFORE", (0, 0), (0, -1), 2.5, ACCENT if block.type == "quote" else MUTED),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        flow += [box, Spacer(1, 6)]
    elif block.type in ("bullet_list", "numbered_list", "checklist"):
        items = [ListItem(Paragraph(_markup(i), st["body"]), leftIndent=16) for i in block.items]
        if block.type == "numbered_list":
            lst = ListFlowable(
                items, bulletType="1", bulletFormat="%s.", leftIndent=16, bulletFontName=_fonts()[0], bulletFontSize=10
            )
        elif block.type == "checklist":
            # Hollow box: U+2610-like glyph from the Unicode font, or ZapfDingbats "q" (❑) with Helvetica.
            unicode_font = _fonts()[0] == "DocSans"
            lst = ListFlowable(
                items,
                bulletType="bullet",
                start="□" if unicode_font else "q",
                bulletFontName=_fonts()[0] if unicode_font else "ZapfDingbats",
                bulletFontSize=11 if unicode_font else 9,
                leftIndent=16,
            )
        else:
            lst = ListFlowable(items, bulletType="bullet", start="•", bulletFontName=_fonts()[0], leftIndent=16)
        flow.append(lst)
        if links:
            flow.append(Paragraph(links, st["time"]))
    elif block.type == "table":
        ncols = max([len(block.table_headers)] + [len(r) for r in block.table_rows])
        rows = []
        if block.table_headers:
            rows.append(
                [Paragraph(_markup(h), st["cell_head"]) for h in block.table_headers] + [""] * (ncols - len(block.table_headers))
            )
        for r in block.table_rows:
            rows.append([Paragraph(_markup(c), st["cell"]) for c in r] + [""] * (ncols - len(r)))
        table = Table(rows, colWidths=[width / ncols] * ncols, repeatRows=1 if block.table_headers else 0)
        style = [
            ("GRID", (0, 0), (-1, -1), 0.5, RULE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        if block.table_headers:
            style.append(("BACKGROUND", (0, 0), (-1, 0), QUOTE_BG))
        table.setStyle(TableStyle(style))
        flow += [table, Spacer(1, 8)]
        if links:
            flow.append(Paragraph(links, st["time"]))
    return flow


def _ranges_para(doc: AnyDocument, ranges: Sequence[SourceRange]) -> list:
    if not ranges:
        return []
    label = escape(range_label(doc))
    return [Paragraph(f"<b>{label}:</b> {_links_markup(doc, ranges)}", _styles()["time"])]


def render_pdf(doc: AnyDocument, path: Path) -> None:
    st = _styles()
    regular = _fonts()[0]
    margin = 2.2 * cm
    width = A4[0] - 2 * margin

    story: list = [Paragraph(_markup(doc.title), st["title"])]
    meta_rows = []
    for m in metadata_lines(doc):
        value = f'<link href="{escape(m.url)}" color="#1D4ED8">{escape(m.value)}</link>' if m.url else escape(m.value)
        meta_rows.append([Paragraph(escape(m.label), st["meta_label"]), Paragraph(value, st["meta"])])
    meta = Table(meta_rows, colWidths=[4.2 * cm, width - 4.2 * cm])
    meta.setStyle(
        TableStyle(
            [
                ("LINEABOVE", (0, 0), (-1, 0), 0.8, RULE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.8, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story += [meta, Spacer(1, 14)]

    lab = labels(doc.output_language)
    sources = source_lines(doc)
    if sources:
        story.append(Paragraph(f"<b>{escape(lab['sources_used'])}:</b>", st["body"]))
        items = []
        for src in sources:
            channel = f" — {escape(src.channel)}" if src.channel else ""
            link = f'<link href="{escape(src.url)}" color="#1D4ED8">{escape(src.url)}</link>'
            items.append(ListItem(Paragraph(f"<b>{escape(src.title)}</b>{channel} — {link}", st["body"]), leftIndent=16))
        story += [ListFlowable(items, bulletType="1", bulletFormat="%s.", leftIndent=16, bulletFontName=regular), Spacer(1, 8)]
    if doc.summary.strip():
        story += [Paragraph(escape(lab["summary"]), st["h1"]), Paragraph(_markup(doc.summary), st["body"])]

    for section in doc.sections:
        head = []
        if section.title.strip():
            head.append(Paragraph(_markup(section.title), st["h1"]))
        head += _ranges_para(doc, section.source_ranges)
        blocks = [f for b in section.blocks for f in _block(b, doc, width)]
        # Keep headings with the first flowable so they never end a page alone.
        story.append(KeepTogether(head + blocks[:1]))
        story += blocks[1:]
        for sub in section.subsections:
            sub_head = [Paragraph(_markup(sub.title), st["h2"])] + _ranges_para(doc, sub.source_ranges)
            sub_blocks = [f for b in sub.blocks for f in _block(b, doc, width)]
            story.append(KeepTogether(sub_head + sub_blocks[:1]))
            story += sub_blocks[1:]

    if doc.key_points:
        story.append(Paragraph(escape(lab["key_points"]), st["h1"]))
        items = []
        for kp in doc.key_points:
            links = _links_markup(doc, kp.source_ranges)
            text = _markup(kp.text) + (f' <font size="8.5">{links}</font>' if links else "")
            items.append(ListItem(Paragraph(text, st["body"]), leftIndent=16))
        story.append(ListFlowable(items, bulletType="bullet", start="•", bulletFontName=regular, leftIndent=16))

    title_for_footer = doc.title[:90]

    def on_page(canvas, _doc) -> None:
        canvas.saveState()
        canvas.setFont(regular, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(margin, 1.2 * cm, title_for_footer)
        canvas.drawRightString(A4[0] - margin, 1.2 * cm, str(canvas.getPageNumber()))
        canvas.restoreState()

    pdf = SimpleDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title=doc.title,
        author=", ".join(dict.fromkeys(s.channel for s in document_sources(doc) if s.channel)),
    )
    pdf.build(story, onFirstPage=on_page, onLaterPages=on_page)
