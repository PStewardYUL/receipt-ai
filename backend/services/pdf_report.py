"""
PDF Tax Summary Report generator using ReportLab.
Produces a professional, print-ready annual tax receipt report.
"""
import io
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.orm import Session
from models.database import Receipt, Category

logger = logging.getLogger(__name__)


def generate_annual_report(db: Session, year: int) -> bytes:
    """
    Generate a complete PDF tax summary for the given year.
    Returns raw PDF bytes.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether
    )
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT

    # ── Pull data ─────────────────────────────────────────────────────────────
    receipts = (
        db.query(Receipt)
        .filter(Receipt.date.like(f"{year}-%"))
        .order_by(Receipt.date)
        .all()
    )

    # ── Color palette ─────────────────────────────────────────────────────────
    DARK = colors.HexColor("#1a1f2e")
    ACCENT = colors.HexColor("#4f46e5")
    LIGHT_BG = colors.HexColor("#f8f9ff")
    MID_GRAY = colors.HexColor("#6b7280")
    ROW_ALT = colors.HexColor("#f3f4f6")
    GREEN = colors.HexColor("#059669")
    RED = colors.HexColor("#dc2626")
    BORDER = colors.HexColor("#e5e7eb")

    # ── Document setup ────────────────────────────────────────────────────────
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    story = []

    def style(name="Normal", **kwargs):
        return ParagraphStyle(name, parent=styles[name], **kwargs)

    title_style = style("Normal", fontSize=24, fontName="Helvetica-Bold",
                        textColor=DARK, spaceAfter=4)
    sub_style = style("Normal", fontSize=11, textColor=MID_GRAY, spaceAfter=2)
    section_style = style("Normal", fontSize=13, fontName="Helvetica-Bold",
                         textColor=ACCENT, spaceBefore=18, spaceAfter=8)
    label_style = style("Normal", fontSize=9, textColor=MID_GRAY)
    value_style = style("Normal", fontSize=9, fontName="Helvetica-Bold", textColor=DARK)
    cell_style = style("Normal", fontSize=8, textColor=DARK)
    cell_r = style("Normal", fontSize=8, textColor=DARK, alignment=TA_RIGHT)
    head_style = style("Normal", fontSize=8, fontName="Helvetica-Bold",
                       textColor=colors.white, alignment=TA_CENTER)
    footer_style = style("Normal", fontSize=7, textColor=MID_GRAY, alignment=TA_CENTER)
    total_style = style("Normal", fontSize=9, fontName="Helvetica-Bold",
                       textColor=DARK, alignment=TA_RIGHT)

    # ── Cover header ──────────────────────────────────────────────────────────
    story.append(Paragraph(f"Annual Tax Receipt Report", title_style))
    story.append(Paragraph(f"Fiscal Year {year}", sub_style))
    story.append(Paragraph(f"Generated: {datetime.utcnow().strftime('%B %d, %Y')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=16))

    # ── Summary stats ─────────────────────────────────────────────────────────
    total_gst = sum(r.gst or 0 for r in receipts)
    total_qst = sum(getattr(r, "qst", 0) or 0 for r in receipts)
    total_pst = sum(r.pst or 0 for r in receipts)
    total_hst = sum(r.hst or 0 for r in receipts)
    total_tax = total_gst + total_qst + total_pst + total_hst
    total_pretax = sum(r.pre_tax or 0 for r in receipts)
    total_amount = sum(r.total or 0 for r in receipts)
    vendors = set(r.normalized_vendor for r in receipts if r.normalized_vendor)

    summary_data = [
        ["Total Receipts", "Total Pre-Tax", "Total Tax Paid", "Total Spent"],
        [str(len(receipts)), f"${total_pretax:,.2f}", f"${total_tax:,.2f}", f"${total_amount:,.2f}"],
    ]
    summary_table = Table(summary_data, colWidths=[1.7*inch]*4)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), ACCENT),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 9),
        ("BACKGROUND", (0,1), (-1,1), LIGHT_BG),
        ("FONTNAME", (0,1), (-1,1), "Helvetica-Bold"),
        ("FONTSIZE", (0,1), (-1,1), 14),
        ("TEXTCOLOR", (0,1), (-1,1), DARK),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0,0), (-1,-1), [ACCENT, LIGHT_BG]),
        ("BOX", (0,0), (-1,-1), 0.5, BORDER),
        ("INNERGRID", (0,0), (-1,-1), 0.5, BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 10),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 16))

    # ── Tax breakdown ─────────────────────────────────────────────────────────
    story.append(Paragraph("Canadian Tax Breakdown", section_style))
    tax_data = [
        ["Tax Type", "Amount", "% of Total Tax"],
        ["GST (Goods & Services Tax 5%)",
         f"${total_gst:,.2f}",
         f"{(total_gst/total_tax*100) if total_tax else 0:.1f}%"],
        ["QST (Quebec Sales Tax 9.975%)",
         f"${total_qst:,.2f}",
         f"{(total_qst/total_tax*100) if total_tax else 0:.1f}%"],
        ["PST (Provincial Sales Tax)",
         f"${total_pst:,.2f}",
         f"{(total_pst/total_tax*100) if total_tax else 0:.1f}%"],
        ["HST (Harmonized Sales Tax 13–15%)",
         f"${total_hst:,.2f}",
         f"{(total_hst/total_tax*100) if total_tax else 0:.1f}%"],
        ["TOTAL TAX PAID", f"${total_tax:,.2f}", "100%"],
    ]
    tax_table = Table(tax_data, colWidths=[3.5*inch, 1.5*inch, 1.8*inch])
    tax_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), DARK),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 8),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, ROW_ALT]),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#fef3c7")),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,1), (-1,-1), 9),
        ("TEXTCOLOR", (0,1), (-1,-2), DARK),
        ("TEXTCOLOR", (0,-1), (-1,-1), colors.HexColor("#92400e")),
        ("ALIGN", (1,0), (-1,-1), "RIGHT"),
        ("ALIGN", (0,0), (0,-1), "LEFT"),
        ("BOX", (0,0), (-1,-1), 0.5, BORDER),
        ("INNERGRID", (0,0), (-1,-1), 0.5, BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 7),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(tax_table)

    # ── By Category ───────────────────────────────────────────────────────────
    cat_totals: dict[str, dict] = {}
    for r in receipts:
        name = r.category.name if r.category else "Uncategorized"
        if name not in cat_totals:
            cat_totals[name] = {"count": 0, "pre_tax": 0, "tax": 0, "total": 0}
        cat_totals[name]["count"] += 1
        cat_totals[name]["pre_tax"] += r.pre_tax or 0
        cat_totals[name]["tax"] += (r.gst or 0) + (getattr(r, "qst", 0) or 0) + (r.pst or 0) + (r.hst or 0)
        cat_totals[name]["total"] += r.total or 0

    if cat_totals:
        story.append(Paragraph("Spending by Category", section_style))
        cat_data = [["Category", "Count", "Pre-Tax", "Tax", "Total"]]
        for cat_name, vals in sorted(cat_totals.items(), key=lambda x: -x[1]["total"]):
            cat_data.append([
                cat_name,
                str(vals["count"]),
                f"${vals['pre_tax']:,.2f}",
                f"${vals['tax']:,.2f}",
                f"${vals['total']:,.2f}",
            ])
        cat_data.append([
            "TOTAL", str(len(receipts)),
            f"${total_pretax:,.2f}", f"${total_tax:,.2f}", f"${total_amount:,.2f}"
        ])
        cat_table = Table(cat_data, colWidths=[2.8*inch, 0.7*inch, 1.2*inch, 1.1*inch, 1.1*inch])
        cat_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), DARK),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("FONTSIZE", (0,0), (-1,0), 8),
            ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, ROW_ALT]),
            ("BACKGROUND", (0,-1), (-1,-1), LIGHT_BG),
            ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE", (0,1), (-1,-1), 8),
            ("TEXTCOLOR", (0,1), (-1,-1), DARK),
            ("ALIGN", (1,0), (-1,-1), "RIGHT"),
            ("ALIGN", (0,0), (0,-1), "LEFT"),
            ("BOX", (0,0), (-1,-1), 0.5, BORDER),
            ("INNERGRID", (0,0), (-1,-1), 0.5, BORDER),
            ("TOPPADDING", (0,0), (-1,-1), 6),
            ("BOTTOMPADDING", (0,0), (-1,-1), 6),
            ("LEFTPADDING", (0,0), (-1,-1), 8),
            ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ]))
        story.append(cat_table)

    # ── Full Receipt Ledger ───────────────────────────────────────────────────
    story.append(Paragraph("Complete Receipt Ledger", section_style))

    ledger_data = [["Date", "Vendor", "Category", "Pre-Tax", "GST", "QST", "PST", "HST", "Total"]]
    for r in receipts:
        cat_name = r.category.name if r.category else ""
        vendor_str = (r.vendor or "Unknown")[:32]
        ledger_data.append([
            r.date or "—",
            vendor_str,
            cat_name[:18],
            f"${(r.pre_tax or 0):,.2f}",
            f"${(r.gst or 0):,.2f}",
            f"${(getattr(r, 'qst', 0) or 0):,.2f}",
            f"${(r.pst or 0):,.2f}",
            f"${(r.hst or 0):,.2f}",
            f"${(r.total or 0):,.2f}",
        ])

    # Total row
    ledger_data.append([
        "TOTAL", "", "",
        f"${total_pretax:,.2f}",
        f"${total_gst:,.2f}",
        f"${total_qst:,.2f}",
        f"${total_pst:,.2f}",
        f"${total_hst:,.2f}",
        f"${total_amount:,.2f}",
    ])

    col_w = [0.75*inch, 1.45*inch, 0.9*inch, 0.7*inch, 0.6*inch, 0.6*inch, 0.6*inch, 0.6*inch, 0.7*inch]
    ledger = Table(ledger_data, colWidths=col_w, repeatRows=1)
    ledger.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), DARK),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,0), 7),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, ROW_ALT]),
        ("BACKGROUND", (0,-1), (-1,-1), LIGHT_BG),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE", (0,1), (-1,-1), 7),
        ("TEXTCOLOR", (0,1), (-1,-2), DARK),
        ("TEXTCOLOR", (0,-1), (-1,-1), DARK),
        ("ALIGN", (3,0), (-1,-1), "RIGHT"),
        ("ALIGN", (0,0), (2,-1), "LEFT"),
        ("BOX", (0,0), (-1,-1), 0.5, BORDER),
        ("INNERGRID", (0,0), (-1,-1), 0.3, BORDER),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
    ]))
    story.append(ledger)

    # ── Footer ────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER))
    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"This report was generated by ReceiptAI from Paperless-ngx data. "
        f"Verify all figures against original receipts before filing. "
        f"Fiscal Year {year} · {len(receipts)} receipts · {len(vendors)} vendors.",
        footer_style
    ))

    doc.build(story)
    return buf.getvalue()
