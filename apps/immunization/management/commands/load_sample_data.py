"""
Load sample data for EIMS dashboard demonstration.
Creates caregivers, children, and triggers vaccine schedules.
Run: python manage.py load_sample_data
"""
from datetime import timedelta
import random

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.immunization.models import Caregiver, Child, VaccineSchedule
from apps.immunization.services.risk_engine import calculate_risk
from apps.immunization.services.reminder_engine import send_overdue_reminders
from apps.immunization.services.vaccine_service import administer_vaccine


class Command(BaseCommand):
    help = "Load sample caregivers, children, and populate dashboard metrics"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing data before loading (optional)",
        )

    def handle(self, *args, **options):
        today = timezone.now().date()

        if options["clear"]:
            self.stdout.write("Clearing existing data...")
            Caregiver.objects.all().delete()  # Cascades to Child, VaccineSchedule, RiskScore, SMSLog
            self.stdout.write("Cleared.")

        if Caregiver.objects.exists():
            self.stdout.write(
                self.style.WARNING("Data already exists. Use --clear to replace.")
            )
            return

        # Sample data: (district, village, name, phone, language)
        caregiver_data = [
            ("Kampala", "Ntinda", "Sarah Nakato", "256700111001", "LUGANDA"),
            ("Kampala", "Kololo", "James Mugisha", "256700111002", "ENGLISH"),
            ("Wakiso", "Entebbe", "Mary Akello", "256700111003", "LUGANDA"),
            ("Wakiso", "Kira", "Peter Okello", "256700111004", "LUGANDA"),
            ("Jinja", "Jinja Town", "Grace Atim", "256700111005", "ATESO"),
            ("Jinja", "Njeru", "David Ochieng", "256700111006", "RUNYANKOLE"),
            ("Mbarara", "Mbarara Town", "Rose Tumusiime", "256700111007", "RUNYANKOLE"),
            ("Mbarara", "Rwizi", "Joseph Byaruhanga", "256700111008", "RUNYANKOLE"),
            ("Gulu", "Gulu Town", "Alice Acan", "256700111009", "ATESO"),
            ("Gulu", "Layibi", "Robert Odong", "256700111010", "ATESO"),
        ]

        self.stdout.write("Creating caregivers...")
        caregivers = []
        for district, village, name, phone, lang in caregiver_data:
            c = Caregiver.objects.create(
                full_name=name,
                phone_number=phone,
                district=district,
                village=village,
                preferred_language=lang,
            )
            caregivers.append(c)

        # Birth dates for varied dashboard metrics:
        # - Many overdue: 8 months ago
        # - Several overdue: 5 months, 4 months, 3 months ago
        # - Vaccines due this week: 10 weeks ago (Penta2 due today), 6 weeks ago (Penta1 due)
        # - Few overdue: 2 months ago
        # - Newborn: 2 weeks ago
        birth_offsets = [
            -240,   # 8 months - many overdue
            -210,   # 7 months - many overdue
            -150,   # 5 months - many overdue
            -120,   # 4 months - several overdue
            -98,    # 14 weeks - Penta3 due soon
            -70,    # 10 weeks - Penta2 due today
            -60,    # ~2 months - several overdue
            -42,    # 6 weeks - Penta1 due today
            -30,    # 1 month - BCG, OPV0 overdue
            -14,    # 2 weeks - BCG, OPV0 overdue
            -7,     # 1 week - BCG, OPV0 overdue
            -3,     # 3 days - BCG, OPV0 due soon
        ]

        child_names_m = ["Ivan", "Moses", "Daniel", "Joshua", "David", "Peter", "Joseph"]
        child_names_f = ["Grace", "Hope", "Faith", "Joy", "Mary", "Sarah", "Esther"]

        self.stdout.write("Creating children and vaccine schedules...")
        children_count = 0
        for i, caregiver in enumerate(caregivers):
            for j in range(1, 3):  # 2 children per caregiver
                birth_date = today + timedelta(days=birth_offsets[(i + j) % len(birth_offsets)])
                gender = random.choice([Child.Gender.MALE, Child.Gender.FEMALE])
                names = child_names_m if gender == Child.Gender.MALE else child_names_f
                name = f"{names[(i + j) % len(names)]} {caregiver.full_name.split()[0]}"

                child = Child.objects.create(
                    caregiver=caregiver,
                    full_name=name,
                    date_of_birth=birth_date,
                    gender=gender,
                )
                children_count += 1

        # Mark some vaccines as administered (for variety)
        self.stdout.write("Marking some vaccines as administered...")
        schedules = VaccineSchedule.objects.filter(administered=False)[:30]
        for schedule in schedules:
            if random.random() < 0.4:  # 40% chance
                administer_vaccine(schedule)

        # Run risk calculation
        self.stdout.write("Calculating risk scores...")
        for child in Child.objects.all():
            calculate_risk(child)

        # Send overdue reminders (creates SMS logs)
        self.stdout.write("Sending overdue reminders...")
        reminders = send_overdue_reminders()

        self.stdout.write(
            self.style.SUCCESS(
                f"Done! Created {len(caregivers)} caregivers, {children_count} children, "
                f"sent {reminders} reminders."
            )
        )
