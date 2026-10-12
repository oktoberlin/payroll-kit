"""Year-to-date totals for a payslip."""

from decimal import Decimal

from django.db.models import Q, Sum

from payroll.models import PayrollRun, Payslip

ZERO = Decimal("0.00")


def ytd_totals(payslip: Payslip) -> dict[str, Decimal]:
    """Gross, tax and net for this employee from 1 January of the pay date's year up to and including
    this payslip. Earlier payslips count only once their run is approved or paid; this one always counts.
    """
    run = payslip.run
    pay_date = run.pay_period.pay_date
    earlier = Q(
        run__status__in=[PayrollRun.APPROVED, PayrollRun.PAID],
        run__pay_period__pay_date__year=pay_date.year,
    ) & (Q(run__pay_period__pay_date__lt=pay_date) | Q(run__pay_period__pay_date=pay_date, run_id__lt=run.pk))
    totals = (
        Payslip.objects.filter(employee=payslip.employee)
        .filter(earlier | Q(pk=payslip.pk))
        .aggregate(gross=Sum("gross"), tax=Sum("tax"), net=Sum("net"))
    )
    return {k: v or ZERO for k, v in totals.items()}
