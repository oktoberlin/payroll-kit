from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from payroll.models import (
    AuditLog,
    DeductionType,
    Employee,
    EmployeeDeduction,
    Membership,
    PayPeriod,
    PayrollRun,
    Payslip,
    PayslipLine,
    TaxBracket,
    TaxTable,
    TimesheetEntry,
)
from payroll.services import runs as run_service
from payroll.services.ytd import ytd_totals


def timesheet_period_is_locked(period) -> bool:
    """Hours of a paid period can no longer change."""
    return PayrollRun.objects.filter(pay_period=period, status=PayrollRun.PAID).exists()


class CompanyScopedSerializer(serializers.ModelSerializer):
    """Rejects references to objects that belong to another tenant."""

    def _company(self):
        return self.context["request"].user.membership.company

    def validate(self, attrs):
        company = self._company()
        for value in attrs.values():
            owner = getattr(value, "company_id", None)
            if owner is not None and owner != company.pk:
                raise serializers.ValidationError("Referenced object belongs to another company.")
            nested_owner = getattr(getattr(value, "employee", None), "company_id", None)
            if nested_owner is not None and nested_owner != company.pk:
                raise serializers.ValidationError("Referenced object belongs to another company.")
        return attrs


class TaxBracketSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxBracket
        fields = ["id", "lower", "upper", "rate"]


class TaxTableSerializer(CompanyScopedSerializer):
    brackets = TaxBracketSerializer(many=True)

    class Meta:
        model = TaxTable
        fields = ["id", "name", "is_illustrative", "brackets"]

    def validate_brackets(self, brackets):
        ordered = sorted(brackets, key=lambda b: b["lower"])
        for i, b in enumerate(ordered):
            if not (0 <= b["rate"] <= 1):
                raise serializers.ValidationError("rate must be between 0 and 1.")
            if b.get("upper") is not None and b["upper"] <= b["lower"]:
                raise serializers.ValidationError("upper must be greater than lower.")
            if i < len(ordered) - 1:
                if b.get("upper") is None:
                    raise serializers.ValidationError("Only the last bracket may have no upper limit.")
                if b["upper"] > ordered[i + 1]["lower"]:
                    raise serializers.ValidationError("Brackets must not overlap.")
        return brackets

    def create(self, validated_data):
        brackets = validated_data.pop("brackets", [])
        table = TaxTable.objects.create(**validated_data)
        TaxBracket.objects.bulk_create(TaxBracket(table=table, **b) for b in brackets)
        return table

    def update(self, instance, validated_data):
        brackets = validated_data.pop("brackets", None)
        instance.name = validated_data.get("name", instance.name)
        instance.save()
        if brackets is not None:
            instance.brackets.all().delete()
            TaxBracket.objects.bulk_create(TaxBracket(table=instance, **b) for b in brackets)
        return instance


class EmployeeDeductionSerializer(serializers.ModelSerializer):
    class Meta:
        model = EmployeeDeduction
        fields = ["id", "deduction_type", "value"]


class EmployeeSerializer(CompanyScopedSerializer):
    deductions = EmployeeDeductionSerializer(many=True, read_only=True)

    class Meta:
        model = Employee
        fields = [
            "id",
            "employee_number",
            "first_name",
            "last_name",
            "email",
            "pay_type",
            "annual_salary",
            "hourly_rate",
            "overtime_multiplier",
            "tax_table",
            "is_active",
            "hire_date",
            "termination_date",
            "deductions",
            "created_at",
        ]
        read_only_fields = ["created_at"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        number = attrs.get("employee_number")
        if number is not None:
            clash = Employee.objects.filter(company=self._company(), employee_number=number)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            other = clash.first()
            if other:
                raise serializers.ValidationError(
                    {"employee_number": f"{number} is already used by {other.first_name} {other.last_name}"}
                )
        hire = attrs.get("hire_date", getattr(self.instance, "hire_date", None))
        end = attrs.get("termination_date", getattr(self.instance, "termination_date", None))
        if hire and end and end < hire:
            raise serializers.ValidationError({"termination_date": "Must be on or after hire_date."})
        pay_type = attrs.get("pay_type", getattr(self.instance, "pay_type", None))
        if (
            pay_type == Employee.SALARY
            and attrs.get("annual_salary", getattr(self.instance, "annual_salary", 0)) <= 0
        ):
            raise serializers.ValidationError({"annual_salary": "Required for salaried employees."})
        if (
            pay_type == Employee.HOURLY
            and attrs.get("hourly_rate", getattr(self.instance, "hourly_rate", 0)) <= 0
        ):
            raise serializers.ValidationError({"hourly_rate": "Required for hourly employees."})
        return attrs


class DeductionTypeSerializer(CompanyScopedSerializer):
    class Meta:
        model = DeductionType
        fields = ["id", "code", "name", "kind", "method", "default_value", "pre_tax"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        code = attrs.get("code")
        if code is not None:
            clash = DeductionType.objects.filter(company=self._company(), code=code)
            if self.instance:
                clash = clash.exclude(pk=self.instance.pk)
            if clash.exists():
                raise serializers.ValidationError({"code": f"{code} is already used by another type"})
        return attrs


class AssignDeductionSerializer(CompanyScopedSerializer):
    class Meta:
        model = EmployeeDeduction
        fields = ["id", "employee", "deduction_type", "value"]


class PayPeriodSerializer(CompanyScopedSerializer):
    class Meta:
        model = PayPeriod
        fields = ["id", "start_date", "end_date", "pay_date", "periods_per_year"]

    def validate(self, attrs):
        attrs = super().validate(attrs)

        def value(name):
            return attrs.get(name, getattr(self.instance, name, None))

        start, end, frequency = value("start_date"), value("end_date"), value("periods_per_year")
        if end < start:
            raise serializers.ValidationError("end_date must be on or after start_date")
        others = PayPeriod.objects.filter(company=self._company())
        if self.instance:
            others = others.exclude(pk=self.instance.pk)
        if others.filter(start_date=start, end_date=end).exists():
            raise serializers.ValidationError("A pay period with these dates already exists.")
        if others.filter(periods_per_year=frequency, start_date__lte=end, end_date__gte=start).exists():
            raise serializers.ValidationError("Pay period overlaps an existing period.")
        return attrs


class TimesheetEntrySerializer(CompanyScopedSerializer):
    class Meta:
        model = TimesheetEntry
        fields = ["id", "employee", "pay_period", "regular_hours", "overtime_hours"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        period = attrs.get("pay_period", getattr(self.instance, "pay_period", None))
        if period is not None and timesheet_period_is_locked(period):
            raise serializers.ValidationError({"pay_period": "This pay period is paid and locked."})
        return attrs


class PayslipLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLine
        fields = ["kind", "code", "description", "amount"]


class PayslipPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayPeriod
        fields = ["start_date", "end_date", "pay_date"]


class PayslipSerializer(serializers.ModelSerializer):
    lines = PayslipLineSerializer(many=True, read_only=True)
    employee_name = serializers.SerializerMethodField()
    employee_number = serializers.CharField(source="employee.employee_number", read_only=True)
    pay_type = serializers.CharField(source="employee.pay_type", read_only=True)
    annual_salary = serializers.DecimalField(
        source="employee.annual_salary", max_digits=12, decimal_places=2, read_only=True
    )
    hourly_rate = serializers.DecimalField(
        source="employee.hourly_rate", max_digits=12, decimal_places=2, read_only=True
    )
    run_status = serializers.CharField(source="run.status", read_only=True)
    period = PayslipPeriodSerializer(source="run.pay_period", read_only=True)

    class Meta:
        model = Payslip
        fields = [
            "id",
            "run",
            "run_status",
            "period",
            "employee",
            "employee_number",
            "employee_name",
            "pay_type",
            "annual_salary",
            "hourly_rate",
            "gross",
            "pre_tax_deductions",
            "taxable",
            "tax",
            "post_tax_deductions",
            "employer_benefits",
            "net",
            "lines",
        ]

    def get_employee_name(self, obj) -> str:
        return f"{obj.employee.first_name} {obj.employee.last_name}"


class YtdSerializer(serializers.Serializer):
    gross = serializers.DecimalField(max_digits=14, decimal_places=2)
    tax = serializers.DecimalField(max_digits=14, decimal_places=2)
    net = serializers.DecimalField(max_digits=14, decimal_places=2)


class PayslipDetailSerializer(PayslipSerializer):
    """Single payslip, with year-to-date totals (one extra query, so not used on lists)."""

    ytd = serializers.SerializerMethodField()

    class Meta(PayslipSerializer.Meta):
        fields = [*PayslipSerializer.Meta.fields, "ytd"]

    @extend_schema_field(YtdSerializer)
    def get_ytd(self, obj):
        return YtdSerializer(ytd_totals(obj)).data


class RunPeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayPeriod
        fields = ["start_date", "end_date", "pay_date", "periods_per_year"]


class RunTotalsSerializer(serializers.Serializer):
    gross = serializers.DecimalField(max_digits=14, decimal_places=2)
    pre_tax_deductions = serializers.DecimalField(max_digits=14, decimal_places=2)
    taxable = serializers.DecimalField(max_digits=14, decimal_places=2)
    tax = serializers.DecimalField(max_digits=14, decimal_places=2)
    post_tax_deductions = serializers.DecimalField(max_digits=14, decimal_places=2)
    employer_benefits = serializers.DecimalField(max_digits=14, decimal_places=2)
    net = serializers.DecimalField(max_digits=14, decimal_places=2)


class PayrollRunSerializer(CompanyScopedSerializer):
    """Users are shown by username. `totals` are exact sums; `is_stale` means a draft needs recalculating."""

    period = RunPeriodSerializer(source="pay_period", read_only=True)
    payslip_count = serializers.SerializerMethodField()
    totals = serializers.SerializerMethodField()
    is_stale = serializers.SerializerMethodField()
    calculated_by = serializers.CharField(source="calculated_by.username", read_only=True, allow_null=True)
    approved_by = serializers.CharField(source="approved_by.username", read_only=True, allow_null=True)
    paid_by = serializers.CharField(source="paid_by.username", read_only=True, allow_null=True)

    class Meta:
        model = PayrollRun
        fields = [
            "id",
            "pay_period",
            "period",
            "status",
            "is_stale",
            "input_hash",
            "calculated_at",
            "calculated_by",
            "approved_by",
            "approved_at",
            "paid_at",
            "paid_by",
            "payslip_count",
            "totals",
            "created_at",
        ]
        read_only_fields = ["status", "input_hash", "calculated_at", "approved_at", "paid_at", "created_at"]

    def get_payslip_count(self, obj) -> int:
        return obj.n_payslips if hasattr(obj, "n_payslips") else obj.payslips.count()

    @extend_schema_field(RunTotalsSerializer)
    def get_totals(self, obj):
        if hasattr(obj, "t_net"):  # annotated by the list/detail queryset: no extra query
            totals = {name: getattr(obj, f"t_{name}") or 0 for name in run_service.TOTAL_FIELDS}
        else:
            totals = run_service.run_totals(obj)
        return RunTotalsSerializer(totals).data

    def get_is_stale(self, obj) -> bool:
        return run_service.is_stale(obj)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if PayrollRun.objects.filter(company=self._company(), pay_period=attrs["pay_period"]).exists():
            raise serializers.ValidationError({"pay_period": "A payroll run already exists for this period."})
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    """`changes`: field -> {"old", "new"} on updates, {"new"} on creates; service entries hold plain facts."""

    actor = serializers.CharField(source="actor.username", read_only=True, allow_null=True)

    class Meta:
        model = AuditLog
        fields = [
            "id",
            "actor",
            "action",
            "object_type",
            "object_id",
            "object_label",
            "changes",
            "created_at",
        ]


class MeCompanySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    currency = serializers.CharField()


class MeEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    employee_number = serializers.CharField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class MeSerializer(serializers.Serializer):
    """The logged-in user: what the UI needs to build the shell and decide what to show."""

    id = serializers.IntegerField()
    username = serializers.CharField()
    role = serializers.ChoiceField(choices=Membership.ROLES)
    company = MeCompanySerializer()
    employee = MeEmployeeSerializer(allow_null=True)


class CsvUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
