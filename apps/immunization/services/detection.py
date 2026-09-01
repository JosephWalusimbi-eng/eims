from datetime import timedelta

from django.utils import timezone

from apps.immunization.models import Child, VaccineSchedule


def get_overdue_vaccines():
    """
    Return all VaccineSchedule records where due_date is before today
    and administered is False.
    """
    today = timezone.now().date()
    return VaccineSchedule.objects.filter(
        due_date__lt=today,
        administered=False,
    )


def get_upcoming_vaccines(days_ahead: int = 3):
    """
    Return VaccineSchedule records due within the next N days (inclusive),
    not yet administered.
    """
    today = timezone.now().date()
    end = today + timedelta(days=days_ahead)
    return VaccineSchedule.objects.filter(
        due_date__gte=today,
        due_date__lte=end,
        administered=False,
    )


def get_overdue_children():
    """
    Return unique Child records that have at least one overdue vaccine.
    """
    overdue_vaccines = get_overdue_vaccines()
    return Child.objects.filter(vaccines__in=overdue_vaccines).distinct()


def get_children_with_upcoming_vaccines(days_ahead: int = 3):
    upcoming = get_upcoming_vaccines(days_ahead=days_ahead)
    return Child.objects.filter(vaccines__in=upcoming).distinct()
