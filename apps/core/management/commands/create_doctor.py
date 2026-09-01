from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from apps.core.auth import ensure_doctor_group


class Command(BaseCommand):
    help = "Create (or update) a doctor login and add them to the Doctor group."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)
        parser.add_argument("--email", default="")

    def handle(self, *args, **options):
        username = options["username"]
        password = options["password"]
        email = options["email"]

        user, _ = User.objects.get_or_create(username=username, defaults={"email": email})
        if email and user.email != email:
            user.email = email

        user.set_password(password)
        user.is_active = True
        user.save()

        group = ensure_doctor_group()
        user.groups.add(group)
        self.stdout.write(self.style.SUCCESS(f"Doctor user ready: {username}"))

