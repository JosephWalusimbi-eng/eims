from django.core.management.base import BaseCommand, CommandError
from django.core.mail import send_mail
from django.conf import settings


class Command(BaseCommand):
    help = "Send a test email using current EMAIL_* settings."

    def add_arguments(self, parser):
        parser.add_argument("--to", required=True, help="Recipient email address")

    def handle(self, *args, **options):
        to = options["to"]
        if not to:
            raise CommandError("--to is required")

        sent = send_mail(
            subject="EIMS test email",
            message="If you received this, SMTP is configured correctly.",
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[to],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Sent={sent}"))

