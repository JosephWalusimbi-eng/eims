from datetime import timedelta

from apps.immunization.models import VaccineSchedule


# Uganda EPI basic schedule: (days offset from birth, list of vaccine names)
UGANDA_EPI_SCHEDULE = [
    (0, ["BCG", "OPV0"]),
    (42, ["Penta1", "OPV1", "PCV1", "Rota1"]),
    (70, ["Penta2", "OPV2", "PCV2", "Rota2"]),
    (98, ["Penta3", "OPV3", "PCV3", "IPV"]),
    (270, ["Measles"]),
]


def generate_schedule(child):
    """
    Generate vaccine schedule entries for a child based on Uganda EPI basic schedule.
    Skips vaccines that already exist for the child.
    """
    created = []

    for days_offset, vaccine_names in UGANDA_EPI_SCHEDULE:
        due_date = child.date_of_birth + timedelta(days=days_offset)
        for vaccine_name in vaccine_names:
            schedule, was_created = VaccineSchedule.objects.get_or_create(
                child=child,
                vaccine_name=vaccine_name,
                defaults={
                    "due_date": due_date,
                    "administered": False,
                },
            )
            if was_created:
                created.append(schedule)

    return created
