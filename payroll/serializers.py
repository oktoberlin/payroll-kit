from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from payroll.models import (
    AuditLog,
    DeductionType,
    Employee,
    EmployeeDeduction,
    PayPeriod,
    PayrollRun,
    Payslip,
    PayslipLine,
    TaxBracket,
    TaxTable,
    TimesheetEntry,
)
from payroll.services.ytd import ytd_totals


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
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError("end_date must be on or after start_date")
        overlapping = PayPeriod.objects.filter(
            company=self._company(),
            periods_per_year=attrs["periods_per_year"],
            start_date__lte=attrs["end_date"],
            end_date__gte=attrs["start_date"],
        )
        if self.instance:
            overlapping = overlapping.exclude(pk=self.instance.pk)
        if overlapping.exists():
            raise serializers.ValidationError("Pay period overlaps an existing period.")
        return attrs


class TimesheetEntrySerializer(CompanyScopedSerializer):
    class Meta:
        model = TimesheetEntry
        fields = ["id", "employee", "pay_period", "regular_hours", "overtime_hours"]


class PayslipLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayslipLine
        fields = ["kind", "code", "description", "amount"]


class PayslipSerializer(serializers.ModelSerializer):
    lines = PayslipLineSerializer(many=True, read_only=True)
    employee_name = serializers.SerializerMethodField()

    class Meta:
        model = Payslip
        fields = [
            "id",
            "run",
            "employee",
            "employee_name",
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


class PayrollRunSerializer(CompanyScopedSerializer):
    payslip_count = serializers.IntegerField(source="payslips.count", read_only=True)

    class Meta:
        model = PayrollRun
        fields = [
            "id",
            "pay_period",
            "status",
            "input_hash",
            "calculated_at",
            "approved_by",
            "approved_at",
            "paid_at",
            "payslip_count",
            "created_at",
        ]
        read_only_fields = [
            "status",
            "input_hash",
            "calculated_at",
            "approved_by",
            "approved_at",
            "paid_at",
            "created_at",
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if PayrollRun.objects.filter(company=self._company(), pay_period=attrs["pay_period"]).exists():
            raise serializers.ValidationError({"pay_period": "A payroll run already exists for this period."})
        return attrs


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = ["id", "actor", "action", "object_type", "object_id", "changes", "created_at"]


class CsvUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
