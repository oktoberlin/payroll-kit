import pytest
from django.core.management import call_command

from payroll.models import Company, Payslip

pytestmark = pytest.mark.django_db


def test_seed_demo_is_repeatable():
    call_command("seed_demo")
    call_command("seed_demo")  # second call is a no-op
    call_command("seed_demo", "--reset")
    assert Company.objects.count() == 1
    assert Payslip.objects.count() == 6
