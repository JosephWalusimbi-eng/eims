from django.contrib.auth.models import Group


DOCTOR_GROUP_NAME = "Doctor"


def user_is_doctor(user) -> bool:
    return bool(user and user.is_authenticated and user.groups.filter(name=DOCTOR_GROUP_NAME).exists())


def ensure_doctor_group() -> Group:
    group, _ = Group.objects.get_or_create(name=DOCTOR_GROUP_NAME)
    return group



MONITOR_GROUP_NAME = "Monitor"


def user_is_monitor(user) -> bool:
    return bool(
        user
        and user.is_authenticated
        and user.groups.filter(name=MONITOR_GROUP_NAME).exists()
    )


def user_is_staff_member(user) -> bool:
    """Any authenticated EIMS role: doctor, monitoring officer, or admin.

    Role separation is enforced in the other direction too -- a monitoring user
    passes this check but fails `user_is_doctor`, so the registration and
    record-modification views stay closed to them.
    """
    return bool(
        user
        and user.is_authenticated
        and (user.is_staff or user_is_doctor(user) or user_is_monitor(user))
    )


def ensure_monitor_group():
    return Group.objects.get_or_create(name=MONITOR_GROUP_NAME)[0]
