from django.core.management.base import BaseCommand

from apps.immunization.services.analysis_engine import run_analysis


class Command(BaseCommand):
    help = "Analyze immunization data and calculate risk scores for all children"

    def handle(self, *args, **options):
        self.stdout.write("Analyzing immunization data...")

        processed, reminders_sent = run_analysis()

        self.stdout.write(f"Processed {processed} children")
        self.stdout.write(f"Sent {reminders_sent} reminders")
        self.stdout.write("Analysis complete")
