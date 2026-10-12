from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

MONEY = {"max_digits": 12, "decimal_places": 2}
RATE = {"max_digits": 7, "decimal_places": 4}  # 0.1250 = 12.5%
HOURS = {"max_digits": 6, "decimal_places": 2}


class Company(models.Model):
    """A tenant. Every other record hangs off a company."""

    name = models.CharField(max_length=200)
    currency = models.CharField(max_length=3, default="USD")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class Membership(models.Model):
    ADMIN = "admin"
    MANAGER = "payroll_manager"
    EMPLOYEE = "employee"
    ROLES = [(ADMIN, "Admin"), (MANAGER, "Payroll manager"), (EMPLOYEE, "Employee")]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="membership")
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLES)

    def __str__(self):
        return f"{self.user} @ {self.company} ({self.role})"


class TaxTable(models.Model):
    """Progressive annual brackets. ILLUSTRATIVE ONLY, not real tax rules."""

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="tax_tables")
    name = models.CharField(max_length=100)
    is_illustrative = models.BooleanField(default=True, editable=False)

    def __str__(self):
        return self.name


class TaxBracket(models.Model):
    table = models.ForeignKey(TaxTable, on_delete=models.CASCADE, related_name="brackets")
    lower = models.DecimalField(**MONEY)
    upper = models.DecimalField(**MONEY, null=True, blank=True)  # null = no cap
    rate = models.DecimalField(**RATE)

    class Meta:
        ordering = ["lower"]

    def __str__(self):
        return f"{self.lower}-{self.upper or '∞'} @ {self.rate}"


class Employee(models.Model):
    SALARY = "salary"
    HOURLY = "hourly"
    PAY_TYPES = [(SALARY, "Salary"), (HOURLY, "Hourly")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="employees")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="employee"
    )
    employee_number = models.CharField(max_length=30)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    pay_type = models.CharField(max_length=10, choices=PAY_TYPES)
    annual_salary = models.DecimalField(**MONEY, default=Decimal("0"))
    hourly_rate = models.DecimalField(**MONEY, default=Decimal("0"))
    overtime_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("1.50"))
    tax_table = models.ForeignKey(TaxTable, null=True, blank=True, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)
    hire_date = models.DateField(null=True, blank=True)  # null = employed before any period
    termination_date = models.DateField(null=True, blank=True)  # last day employed
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "employee_number"], name="uniq_employee_number")
        ]
        ordering = ["employee_number"]

    def __str__(self):
        return f"{self.employee_number} {self.first_name} {self.last_name}"


class DeductionType(models.Model):
    FIXED = "fixed"
    PERCENT = "percent"
    METHODS = [(FIXED, "Fixed amount"), (PERCENT, "Percent of gross")]
    DEDUCTION = "deduction"
    BENEFIT = "benefit"
    KINDS = [(DEDUCTION, "Employee deduction"), (BENEFIT, "Employer benefit")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="deduction_types")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    kind = models.CharField(max_length=10, choices=KINDS, default=DEDUCTION)
    method = models.CharField(max_length=10, choices=METHODS, default=FIXED)
    default_value = models.DecimalField(**MONEY, default=Decimal("0"))
    pre_tax = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "code"], name="uniq_deduction_code")]

    def __str__(self):
        return self.code


class EmployeeDeduction(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="deductions")
    deduction_type = models.ForeignKey(DeductionType, on_delete=models.PROTECT)
    value = models.DecimalField(**MONEY, null=True, blank=True)  # null = use type default

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["employee", "deduction_type"], name="uniq_employee_deduction")
        ]

    def __str__(self):
        return f"{self.employee_id}:{self.deduction_type_id}"


class PayPeriod(models.Model):
    FREQUENCIES = [(12, "Monthly"), (24, "Semi-monthly"), (26, "Bi-weekly"), (52, "Weekly")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="pay_periods")
    start_date = models.DateField()
    end_date = models.DateField()
    pay_date = models.DateField()
    periods_per_year = models.PositiveSmallIntegerField(choices=FREQUENCIES, default=12)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "start_date", "end_date"], name="uniq_pay_period")
        ]

    def __str__(self):
        return f"{self.start_date} to {self.end_date}"

    def clean(self):
        if self.end_date < self.start_date:
            raise ValidationError("end_date must be on or after start_date")


class TimesheetEntry(models.Model):
    """Hours worked by an hourly employee in a pay period."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="timesheets")
    pay_period = models.ForeignKey(PayPeriod, on_delete=models.CASCADE, related_name="timesheets")
    regular_hours = models.DecimalField(**HOURS, default=Decimal("0"))
    overtime_hours = models.DecimalField(**HOURS, default=Decimal("0"))

    class Meta:
        constraints = [models.UniqueConstraint(fields=["employee", "pay_period"], name="uniq_timesheet")]

    def __str__(self):
        return f"{self.employee_id} {self.regular_hours}+{self.overtime_hours}h"


class PayrollRun(models.Model):
    DRAFT = "draft"
    APPROVED = "approved"
    PAID = "paid"
    STATUSES = [(DRAFT, "Draft"), (APPROVED, "Approved"), (PAID, "Paid")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="payroll_runs")
    pay_period = models.ForeignKey(PayPeriod, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    input_hash = models.CharField(max_length=64, blank=True)
    calculated_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["company", "pay_period"], name="uniq_run_per_period")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Run {self.pk} ({self.status})"

    def save(self, *args, **kwargs):
        if self.pk:
            previous = PayrollRun.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if previous == self.PAID:
                raise ValidationError("A paid payroll run is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status != self.DRAFT:
            raise ValidationError("Only draft payroll runs can be deleted.")
        return super().delete(*args, **kwargs)


class Payslip(models.Model):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    gross = models.DecimalField(**MONEY)
    pre_tax_deductions = models.DecimalField(**MONEY)
    taxable = models.DecimalField(**MONEY)
    tax = models.DecimalField(**MONEY)
    post_tax_deductions = models.DecimalField(**MONEY)
    employer_benefits = models.DecimalField(**MONEY)
    net = models.DecimalField(**MONEY)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="uniq_payslip")]
        ordering = ["employee__employee_number"]

    def __str__(self):
        return f"Payslip {self.pk} net {self.net}"

    def save(self, *args, **kwargs):
        if self.run.status != PayrollRun.DRAFT:
            raise ValidationError("Payslips can only change while the run is a draft.")
        super().save(*args, **kwargs)


class PayslipLine(models.Model):
    payslip = models.ForeignKey(Payslip, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=10)  # earning | pre_tax | tax | post_tax | benefit
    code = models.CharField(max_length=20)
    description = models.CharField(max_length=200)
    amount = models.DecimalField(**MONEY)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]

    def __str__(self):
        return f"{self.code} {self.amount}"


class AuditLog(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=50)
    object_type = models.CharField(max_length=50)
    object_id = models.CharField(max_length=50)
    changes = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action} {self.object_type}:{self.object_id}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit log entries are append-only.")
        super().save(*args, **kwargs)
