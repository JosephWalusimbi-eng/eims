from django.utils import timezone


def administer_vaccine(schedule):
    """
    Mark a vaccine schedule as administered and set the administered date.
    Returns the updated VaccineSchedule record.
    """
    schedule.administered = True
    schedule.administered_date = timezone.now().date()
    schedule.save()
    return schedule
