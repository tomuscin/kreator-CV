"""
DOCX generator for Kreator CV.
Generates a clean, professional Word document from adapted CV data.
"""

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Inches, Cm


# ── Design constants ────────────────────────────────────────────────
COLOR_ACCENT  = RGBColor(0x1A, 0x3A, 0x5C)   # dark navy
COLOR_SECTION = RGBColor(0x2E, 0x74, 0xB5)   # medium blue
COLOR_TEXT    = RGBColor(0x1F, 0x1F, 0x1F)   # near black
COLOR_LIGHT   = RGBColor(0x55, 0x55, 0x55)   # grey for dates

FONT_NAME = "Calibri"


def generate_cv_docx(cv_data: dict, output_path: Path) -> Path:
    """
    Generates a .docx CV from adapted cv_data.

    Args:
        cv_data:     Adapted CV dict (output of cv_adapter.adapt_cv()).
        output_path: Where to save the .docx file.

    Returns:
        output_path (for chaining).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    _set_margins(doc)

    # ── Header: Name + contact ──────────────────────────────────────
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(cv_data["personal"]["name"].upper())
    run.bold = True
    run.font.size = Pt(22)
    run.font.color.rgb = COLOR_ACCENT
    run.font.name = FONT_NAME

    contact = f"{cv_data['personal']['phone']}  |  {cv_data['personal']['email']}"
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r2 = p2.add_run(contact)
    r2.font.size = Pt(10)
    r2.font.color.rgb = COLOR_LIGHT
    r2.font.name = FONT_NAME

    _add_divider(doc)

    # ── Summary ─────────────────────────────────────────────────────
    _section_heading(doc, "PODSUMOWANIE ZAWODOWE")
    _body_paragraph(doc, cv_data.get("summary", ""))

    # ── Competencies ─────────────────────────────────────────────────
    _section_heading(doc, "KOMPETENCJE")
    comps = cv_data.get("competencies", [])
    if comps:
        comp_text = "  •  ".join(comps)
        _body_paragraph(doc, comp_text)

    # ── Work Experience ──────────────────────────────────────────────
    _section_heading(doc, "DOŚWIADCZENIE ZAWODOWE")
    for job in cv_data.get("experience", []):
        # Job title + dates
        p_job = doc.add_paragraph()
        p_job.paragraph_format.space_before = Pt(6)
        r_title = p_job.add_run(job["title"])
        r_title.bold = True
        r_title.font.size = Pt(11)
        r_title.font.color.rgb = COLOR_SECTION
        r_title.font.name = FONT_NAME
        r_dates = p_job.add_run(f"   {job['dates']}")
        r_dates.font.size = Pt(10)
        r_dates.font.color.rgb = COLOR_LIGHT
        r_dates.font.name = FONT_NAME

        # Bullet points
        for bullet in job.get("bullets", []):
            if bullet.strip():
                p_b = doc.add_paragraph(style="List Bullet")
                p_b.paragraph_format.left_indent = Inches(0.25)
                r_b = p_b.add_run(bullet)
                r_b.font.size = Pt(10)
                r_b.font.color.rgb = COLOR_TEXT
                r_b.font.name = FONT_NAME

    # ── Education ───────────────────────────────────────────────────
    _section_heading(doc, "WYKSZTAŁCENIE")
    for edu in cv_data.get("education", []):
        p_e = doc.add_paragraph()
        r_inst = p_e.add_run(edu["institution"])
        r_inst.bold = True
        r_inst.font.size = Pt(10)
        r_inst.font.name = FONT_NAME
        if edu.get("faculty"):
            p_e.add_run(f", {edu['faculty']}")
        if edu.get("degree"):
            p_e2 = doc.add_paragraph()
            p_e2.paragraph_format.left_indent = Inches(0.2)
            r_deg = p_e2.add_run(edu["degree"])
            r_deg.font.size = Pt(10)
            r_deg.font.color.rgb = COLOR_LIGHT
            r_deg.font.name = FONT_NAME

    # ── Languages ───────────────────────────────────────────────────
    _section_heading(doc, "ZNAJOMOŚĆ JĘZYKÓW")
    for lang in cv_data.get("languages", []):
        _body_paragraph(doc, f"{lang['language']} – {lang['level']}")

    # ── Interests ───────────────────────────────────────────────────
    if cv_data.get("interests"):
        _section_heading(doc, "OBSZARY ZAINTERESOWAŃ")
        _body_paragraph(doc, cv_data["interests"])

    # ── ATS keywords note (hidden / small grey) ──────────────────────
    ats = cv_data.get("ats_keywords", [])
    if ats:
        _add_divider(doc)
        p_ats = doc.add_paragraph()
        r_ats = p_ats.add_run("Słowa kluczowe ATS: " + " • ".join(ats))
        r_ats.font.size = Pt(8)
        r_ats.font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
        r_ats.font.name = FONT_NAME

    # ── RODO clause ─────────────────────────────────────────────────
    if cv_data.get("rodo_clause"):
        p_rodo = doc.add_paragraph()
        p_rodo.paragraph_format.space_before = Pt(12)
        r_rodo = p_rodo.add_run(cv_data["rodo_clause"])
        r_rodo.font.size = Pt(7)
        r_rodo.font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
        r_rodo.font.name = FONT_NAME
        r_rodo.italic = True

    doc.save(str(output_path))
    return output_path


# ── Helpers ─────────────────────────────────────────────────────────

def _set_margins(doc: Document) -> None:
    for section in doc.sections:
        section.top_margin    = Cm(1.8)
        section.bottom_margin = Cm(1.8)
        section.left_margin   = Cm(2.2)
        section.right_margin  = Cm(2.2)


def _section_heading(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after  = Pt(4)
    run = p.add_run(text)
    run.bold = True
    run.font.size = Pt(11)
    run.font.color.rgb = COLOR_ACCENT
    run.font.name = FONT_NAME
    # Bottom border
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "1A3A5C")
    pBdr.append(bottom)
    pPr.append(pBdr)


def _body_paragraph(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(10)
    run.font.color.rgb = COLOR_TEXT
    run.font.name = FONT_NAME


def _add_divider(doc: Document) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after  = Pt(2)
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "2E74B5")
    pBdr.append(bottom)
    pPr.append(pBdr)
