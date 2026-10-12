from django.contrib import admin

from payroll import models

for model in [
    models.Company,
    models.Membership,
    models.TaxTable,
    models.TaxBracket,
    models.Employee,
    models.DeductionType,
    models.EmployeeDeduction,
    models.PayPeriod,
    models.TimesheetEntry,
    models.PayrollRun,
    models.Payslip,
    models.PayslipLine,
    models.AuditLog,
]:
    admin.site.register(model)
