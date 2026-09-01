"""Backfill a distinct replication identity for every existing schedule row.

`sync_uid` is the key an offline envelope targets, so it has to be unique and
stable. A callable default cannot supply that in an AddField -- Django would
write one value across every existing row -- so the column is added nullable,
filled here, then tightened to NOT NULL UNIQUE in 0010.
"""
import uuid

from django.db import migrations


def forwards(apps, schema_editor):
    VaccineSchedule = apps.get_model("immunization", "VaccineSchedule")
    rows = VaccineSchedule.objects.filter(sync_uid__isnull=True).only("id")
    batch = []
    for row in rows.iterator(chunk_size=2000):
        row.sync_uid = uuid.uuid4()
        batch.append(row)
        if len(batch) >= 2000:
            VaccineSchedule.objects.bulk_update(batch, ["sync_uid"])
            batch = []
    if batch:
        VaccineSchedule.objects.bulk_update(batch, ["sync_uid"])


def backwards(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [("immunization", "0008_caregiver_alternate_phone_number_and_more")]
    operations = [migrations.RunPython(forwards, backwards)]
