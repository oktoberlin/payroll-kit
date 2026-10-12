"""Payroll register: one CSV row per payslip in a run."""

import csv
import io

COLUMNS = [
    "employee_number",
    "employee_name",
    "gross",
    "pre_tax_deductions",
    "taxable",
    "tax",
    "post_tax_deductions",
    "employer_benefits",
    "net",
]


def _safe(value) -> str:
    """Neutralise spreadsheet formula injection in text cells (=, +, -, @, tab, CR)."""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def payroll_register_csv(run) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for slip in run.payslips.select_related("employee"):
        emp = slip.employee
        writer.writerow(
            [
                _safe(emp.employee_number),
                _safe(f"{emp.first_name} {emp.last_name}"),
                slip.gross,
                slip.pre_tax_deductions,
                slip.taxable,
                slip.tax,
                slip.post_tax_deductions,
                slip.employer_benefits,
                slip.net,
            ]
        )
    return buf.getvalue()
