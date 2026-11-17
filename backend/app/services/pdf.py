"""Markdown → PDF with ReportLab (pure Python; works on Windows and in slim Docker images)."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from xml.sax.saxutils import escape

from markdown_it import MarkdownIt
from markdown_it.token import Token
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, ListFlowable, ListItem, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

log = logging.getLogger(__name__)

_FONT_CANDIDATES = [
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
]
_EMOJI = {"✅": "[OK]", "❌": "[X]", "⚠️": "[!]", "⚠": "[!]", "🟡": "[~]", "❔": "[?]"}
_font_cache: tuple[str, str, bool] | None = None


def _fonts() -> tuple[str, str, bool]:
    """Returns (regular, bold, unicode_capable)."""
    global _font_cache
    if _font_cache is None:
        _font_cache = ("Helvetica", "Helvetica-Bold", False)
        for reg, bold in _FONT_CANDIDATES:
            if Path(reg).exists() and Path(bold).exists():
                try:
                    pdfmetrics.registerFont(TTFont("AF-Regular", reg))
                    pdfmetrics.registerFont(TTFont("AF-Bold", bold))
                    pdfmetrics.registerFontFamily("AF", normal="AF-Regular", bold="AF-Bold",
                                                  italic="AF-Regular", boldItalic="AF-Bold")
                    _font_cache = ("AF-Regular", "AF-Bold", True)
                    break
                except Exception:  # pragma: no cover - broken font file
                    log.warning("Could not register font %s", reg)
    return _font_cache


def _clean(text: str, unicode_ok: bool) -> str:
    for k, v in _EMOJI.items():
        text = text.replace(k, v)
    if not unicode_ok:
        text = text.encode("latin-1", "replace").decode("latin-1")
    return text


def _inline(tokens: list[Token] | None, unicode_ok: bool) -> str:
    out: list[str] = []
    link_stack: list[str] = []
    for t in tokens or []:
        if t.type == "text":
            out.append(escape(_clean(t.content, unicode_ok)))
        elif t.type == "code_inline":
            out.append(f'<font face="Courier">{escape(t.content)}</font>')
        elif t.type in ("strong_open", "strong_close"):
            out.append("<b>" if t.type.endswith("open") else "</b>")
        elif t.type in ("em_open", "em_close"):
            out.append("<i>" if t.type.endswith("open") else "</i>")
        elif t.type == "link_open":
            href = str(t.attrGet("href") or "")
            if re.match(r"^https?://", href):
                out.append(f'<link href="{escape(href, {chr(34): "&quot;"})}" color="#2563eb">')
                link_stack.append("</link>")
            else:
                out.append("<u>")
                link_stack.append("</u>")
        elif t.type == "link_close":
            out.append(link_stack.pop() if link_stack else "")
        elif t.type in ("softbreak", "hardbreak"):
            out.append("<br/>")
        elif t.type == "html_inline":
            out.append(escape(t.content))
    return "".join(out)


def markdown_to_pdf(markdown: str, path: Path, *, title: str, watermark: str | None = None) -> Path:
    regular, bold, unicode_ok = _fonts()
    ss = getSampleStyleSheet()
    styles = {
        "body": ParagraphStyle("body", parent=ss["BodyText"], fontName=regular, fontSize=9.5,
                               leading=13.5, alignment=TA_LEFT, spaceAfter=5),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName=bold, fontSize=18, leading=22,
                             spaceAfter=8, textColor=colors.HexColor("#0f172a")),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=bold, fontSize=13.5, leading=17,
                             spaceBefore=10, spaceAfter=5, textColor=colors.HexColor("#1e293b")),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontName=bold, fontSize=11, leading=14,
                             spaceBefore=6, spaceAfter=4),
        "quote": ParagraphStyle("quote", parent=ss["BodyText"], fontName=bold, fontSize=9.5,
                                leading=13, textColor=colors.HexColor("#b45309"),
                                backColor=colors.HexColor("#fef3c7"), borderPadding=6, spaceAfter=8),
        "cell": ParagraphStyle("cell", parent=ss["BodyText"], fontName=regular, fontSize=8, leading=10),
        "cellh": ParagraphStyle("cellh", parent=ss["BodyText"], fontName=bold, fontSize=8, leading=10),
    }
    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(markdown)
    story: list = []
    i = 0
    list_stack: list[list] = []
    in_quote = False
    while i < len(tokens):
        t = tokens[i]
        if t.type == "heading_open":
            level = min(int(t.tag[1]), 3)
            story.append(Paragraph(_inline(tokens[i + 1].children, unicode_ok), styles[f"h{level}"]))
            i += 3
            continue
        if t.type == "blockquote_open":
            in_quote = True
        elif t.type == "blockquote_close":
            in_quote = False
        elif t.type in ("bullet_list_open", "ordered_list_open"):
            list_stack.append([])
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            items = list_stack.pop()
            flow = ListFlowable([ListItem(p, leftIndent=12) for p in items], bulletType="bullet",
                                start="•", leftIndent=12, bulletFontName=regular)
            (list_stack[-1] if list_stack else story).append(flow)
        elif t.type == "paragraph_open":
            para = Paragraph(_inline(tokens[i + 1].children, unicode_ok),
                             styles["quote" if in_quote else "body"])
            (list_stack[-1] if list_stack else story).append(para)
            i += 3
            continue
        elif t.type == "table_open":
            rows: list[list] = []
            j = i + 1
            header = True
            current: list = []
            while tokens[j].type != "table_close":
                tj = tokens[j]
                if tj.type == "thead_close":
                    header = False
                elif tj.type == "tr_open":
                    current = []
                elif tj.type == "tr_close":
                    rows.append(current)
                elif tj.type == "inline":
                    current.append(Paragraph(_inline(tj.children, unicode_ok),
                                             styles["cellh" if header else "cell"]))
                j += 1
            if rows:
                ncols = max(len(r) for r in rows)
                rows = [r + [""] * (ncols - len(r)) for r in rows]
                width = (A4[0] - 36 * mm) / ncols
                tbl = Table(rows, colWidths=[width] * ncols, repeatRows=1)
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ]))
                story += [tbl, Spacer(1, 6)]
            i = j + 1
            continue
        elif t.type in ("fence", "code_block"):
            story.append(Paragraph(f'<font face="Courier">{escape(t.content)}</font>'.replace("\n", "<br/>"),
                                   styles["body"]))
        i += 1

    def _decorate(canvas, doc) -> None:  # type: ignore[no-untyped-def]
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(18 * mm, 10 * mm, _clean(f"AgentFlow · {title}"[:110], unicode_ok))
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        if watermark:
            canvas.setFont(bold, 46)
            canvas.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.12))
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, watermark)
        canvas.restoreState()

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm, title=_clean(title, unicode_ok),
                            author="AgentFlow")
    doc.build(story or [Paragraph("(empty report)", styles["body"])],
              onFirstPage=_decorate, onLaterPages=_decorate)
    return path


__all__ = ["markdown_to_pdf", "KeepTogether"]
