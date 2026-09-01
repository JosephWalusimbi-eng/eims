"""Encrypt identifying columns that were written before encryption was enabled.

The field descriptors pass legacy plaintext through unchanged on read, so an
existing deployment keeps working during the transition. This command performs
the one-off rewrite: read each row (plaintext passes through), save it (the
descriptor encrypts on write), and rebuild the caregiver blind index.

Idempotent -- a row that is already ciphertext round-trips unchanged.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.core.crypto import PREFIX
from apps.immunization.models import Caregiver, Child


class Command(BaseCommand):
    help = "Encrypt legacy plaintext values in identifying columns."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry = options["dry_run"]
        caregivers = self._scan(Caregiver, ["full_name", "phone_number", "email",
                                            "alternate_email", "alternate_phone_number"])
        children = self._scan(Child, ["full_name"])
        self.stdout.write(f"Caregivers needing rewrite: {caregivers}")
        self.stdout.write(f"Children needing rewrite:   {children}")
        if dry:
            self.stdout.write(self.style.WARNING("Dry run - nothing written."))
            return

        with transaction.atomic():
            for obj in Caregiver.objects.all().iterator(chunk_size=500):
                obj.save()
            for obj in Child.objects.all().iterator(chunk_size=500):
                obj.save(update_fields=["full_name"])
        self.stdout.write(self.style.SUCCESS("Rewrite complete; all rows are now ciphertext."))

    def _scan(self, model, fields):
        """Count rows whose raw column value is not yet a v1 ciphertext."""
        from django.db import connection

        table = model._meta.db_table
        cols = [model._meta.get_field(f).column for f in fields]
        where = " OR ".join(f"({c} <> '' AND {c} NOT LIKE %s)" for c in cols)
        with connection.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", [PREFIX + "%"] * len(cols))
            return cur.fetchone()[0]
