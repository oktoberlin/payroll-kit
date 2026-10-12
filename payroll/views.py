from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from payroll.models import (
    AuditLog,
    DeductionType,
    Employee,
    EmployeeDeduction,
    Membership,
    PayPeriod,
    PayrollRun,
    Payslip,
    TaxTable,
    TimesheetEntry,
)
from payroll.permissions import IsCompanyAdmin, IsPayrollStaff, IsStaffOrReadOwnPayslip
from payroll.serializers import (
    AssignDeductionSerializer,
    AuditLogSerializer,
    CsvUploadSerializer,
    DeductionTypeSerializer,
    EmployeeSerializer,
    PayPeriodSerializer,
    PayrollRunSerializer,
    PayslipDetailSerializer,
    PayslipSerializer,
    TaxTableSerializer,
    TimesheetEntrySerializer,
)
from payroll.services import audit, runs
from payroll.services.calculation import PayrollCalculationError
from payroll.services.csv_import import import_employees
from payroll.services.export import payroll_register_csv
from payroll.services.payslip_pdf import render_payslip


class Conflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "The object is referenced by other records."
    default_code = "conflict"


def _domain_errors(fn):
    """Turn service errors into DRF responses: validation -> 400, protected delete -> 409."""
    try:
        return fn()
    except DjangoValidationError as exc:
        raise ValidationError(exc.messages) from exc
    except PayrollCalculationError as exc:
        raise ValidationError([str(exc)]) from exc
    except ProtectedError as exc:
        raise Conflict("Cannot delete: it is still referenced by other records.") from exc


class CompanyScopedViewSet(viewsets.ModelViewSet):
    permission_classes = [IsPayrollStaff]
    company_field = "company"

    @property
    def company(self):
        return self.request.user.membership.company

    def get_queryset(self):
        return self.queryset.filter(**{self.company_field: self.company})

    def perform_create(self, serializer):
        extra = {"company": self.company} if self.company_field == "company" else {}
        obj = serializer.save(**extra)
        audit.record(
            company=self.company, actor=self.request.user, action="created", obj=obj, changes=serializer.data
        )

    def perform_update(self, serializer):
        obj = serializer.save()
        audit.record(
            company=self.company,
            actor=self.request.user,
            action="updated",
            obj=obj,
            changes=serializer.validated_data and {k: str(v) for k, v in serializer.validated_data.items()},
        )

    def perform_destroy(self, instance):
        audit.record(company=self.company, actor=self.request.user, action="deleted", obj=instance)
        _domain_errors(instance.delete)


class EmployeeViewSet(CompanyScopedViewSet):
    queryset = Employee.objects.prefetch_related("deductions")
    serializer_class = EmployeeSerializer
    filterset_fields = ["pay_type", "is_active"]

    @extend_schema(request={"multipart/form-data": CsvUploadSerializer}, responses={200: dict})
    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser], url_path="import")
    def import_csv(self, request):
        upload = CsvUploadSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        result = import_employees(self.company, upload.validated_data["file"], actor=request.user)
        code = status.HTTP_201_CREATED if result["created"] else status.HTTP_400_BAD_REQUEST
        return Response(result, status=code)


class TaxTableViewSet(CompanyScopedViewSet):
    queryset = TaxTable.objects.prefetch_related("brackets")
    serializer_class = TaxTableSerializer


class DeductionTypeViewSet(CompanyScopedViewSet):
    queryset = DeductionType.objects.all()
    serializer_class = DeductionTypeSerializer


class EmployeeDeductionViewSet(CompanyScopedViewSet):
    queryset = EmployeeDeduction.objects.select_related("employee", "deduction_type")
    serializer_class = AssignDeductionSerializer
    company_field = "employee__company"
    filterset_fields = ["employee"]


class PayPeriodViewSet(CompanyScopedViewSet):
    queryset = PayPeriod.objects.all()
    serializer_class = PayPeriodSerializer


class TimesheetEntryViewSet(CompanyScopedViewSet):
    queryset = TimesheetEntry.objects.select_related("employee", "pay_period")
    serializer_class = TimesheetEntrySerializer
    company_field = "employee__company"
    filterset_fields = ["pay_period", "employee"]


class PayrollRunViewSet(CompanyScopedViewSet):
    """Runs are created empty, then moved through calculate -> approve -> pay."""

    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = PayrollRun.objects.select_related("pay_period")
    serializer_class = PayrollRunSerializer
    filterset_fields = ["status", "pay_period"]

    def _respond(self, run, code=status.HTTP_200_OK):
        return Response(PayrollRunSerializer(run, context={"request": self.request}).data, status=code)

    @extend_schema(request=None, responses=PayrollRunSerializer)
    @action(detail=True, methods=["post"])
    def calculate(self, request, pk=None):
        run, changed = _domain_errors(lambda: runs.calculate_run(self.get_object(), actor=request.user))
        response = self._respond(run)
        response["X-Recalculated"] = "true" if changed else "false"
        return response

    @extend_schema(responses={(200, "text/csv"): bytes})
    @action(detail=True, methods=["get"])
    def export(self, request, pk=None):
        run = self.get_object()
        audit.record(company=self.company, actor=request.user, action="payroll_run.exported", obj=run)
        response = HttpResponse(payroll_register_csv(run), content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="payroll-register-{run.pk}.csv"'
        return response

    @extend_schema(request=None, responses=PayrollRunSerializer)
    @action(detail=True, methods=["post"], permission_classes=[IsCompanyAdmin])
    def approve(self, request, pk=None):
        return self._respond(_domain_errors(lambda: runs.approve_run(self.get_object(), request.user)))

    @extend_schema(request=None, responses=PayrollRunSerializer)
    @action(detail=True, methods=["post"], permission_classes=[IsCompanyAdmin])
    def reopen(self, request, pk=None):
        return self._respond(_domain_errors(lambda: runs.reopen_run(self.get_object(), request.user)))

    @extend_schema(request=None, responses=PayrollRunSerializer)
    @action(detail=True, methods=["post"], url_path="mark-paid", permission_classes=[IsCompanyAdmin])
    def mark_paid(self, request, pk=None):
        return self._respond(_domain_errors(lambda: runs.mark_paid(self.get_object(), request.user)))


class PayslipViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PayslipSerializer
    permission_classes = [IsStaffOrReadOwnPayslip]
    filterset_fields = ["run", "employee"]

    def get_serializer_class(self):
        return PayslipDetailSerializer if self.action == "retrieve" else PayslipSerializer

    def get_queryset(self):
        m = self.request.user.membership
        qs = (
            Payslip.objects.filter(run__company=m.company)
            .select_related("employee", "run__pay_period", "run__company")
            .prefetch_related("lines")
        )
        if m.role == Membership.EMPLOYEE:
            qs = qs.filter(employee__user=self.request.user)
        return qs

    @extend_schema(responses={(200, "application/pdf"): bytes})
    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        slip = self.get_object()
        response = HttpResponse(render_payslip(slip), content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="payslip-{slip.pk}.pdf"'
        return response


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AuditLogSerializer
    permission_classes = [IsCompanyAdmin]
    filterset_fields = ["action", "object_type"]

    def get_queryset(self):
        return AuditLog.objects.filter(company=self.request.user.membership.company)
