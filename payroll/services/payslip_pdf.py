"""Render a payslip to PDF bytes with ReportLab (no system dependencies)."""

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from payroll.services.ytd import ytd_totals

KIND_LABELS = {
    "earning": "Earnings",
    "pre_tax": "Pre-tax deductions",
    "tax": "Tax",
    "post_tax": "Post-tax deductions",
    "benefit": "Employer benefit*",
}


def render_payslip(payslip) -> bytes:
    run, emp = payslip.run, payslip.employee
    currency = run.company.currency
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Payslip {emp.employee_number}")
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"{run.company.name}: Payslip", styles["Title"]),
        Paragraph(
            f"{emp.first_name} {emp.last_name} ({emp.employee_number})<br/>"
            f"Period {run.pay_period.start_date} to {run.pay_period.end_date}, "
            f"paid {run.pay_period.pay_date}<br/>Run status: {run.status}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    rows = [["Type", "Description", f"Amount ({currency})"]]
    order = list(KIND_LABELS)
    for line in sorted(payslip.lines.all(), key=lambda ln: (order.index(ln.kind), ln.position)):
        rows.append([KIND_LABELS.get(line.kind, line.kind), line.description, f"{line.amount:,.2f}"])
    ytd = ytd_totals(payslip)
    rows += [
        ["", "Gross pay", f"{payslip.gross:,.2f}"],
        ["", "Net pay", f"{payslip.net:,.2f}"],
        ["", "Year to date: gross", f"{ytd['gross']:,.2f}"],
        ["", "Year to date: tax", f"{ytd['tax']:,.2f}"],
        ["", "Year to date: net", f"{ytd['net']:,.2f}"],
    ]
    table = Table(rows, colWidths=[140, 230, 110])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, -5), (-1, -4), "Helvetica-Bold"),
            ]
        )
    )
    story += [
        table,
        Spacer(1, 12),
        Paragraph(
            "Demo document. Tax figures use an illustrative table, not real tax rules.", styles["Italic"]
        ),
    ]
    doc.build(story)
    return buf.getvalue()
