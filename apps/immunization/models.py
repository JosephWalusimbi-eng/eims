import uuid

from django.db import models

from apps.core.crypto import EncryptedCharField, EncryptedEmailField, blind_index


class Caregiver(models.Model):
    class PreferredLanguage(models.TextChoices):
        ENGLISH = "ENGLISH", "English"
        LUGANDA = "LUGANDA", "Luganda"
        ATESO = "ATESO", "Ateso"
        RUNYANKOLE = "RUNYANKOLE", "Runyankole"

    full_name = EncryptedCharField(max_length=512)
    phone_number = EncryptedCharField(max_length=512)
    phone_index = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="HMAC of phone_number; permits equality lookup without decryption.",
    )
    alternate_phone_number = EncryptedCharField(max_length=512, blank=True, default="")
    email = EncryptedEmailField(max_length=512, blank=True, default="")
    alternate_email = EncryptedEmailField(max_length=512, blank=True, default="")
    district = models.CharField(max_length=150, default="", blank=True)
    village = models.CharField(max_length=150)
    preferred_language = models.CharField(
        max_length=20,
        choices=PreferredLanguage.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Maintain the blind index alongside the encrypted value.
        self.phone_index = blind_index(self.phone_number) if self.phone_number else ""
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.full_name


class Child(models.Model):
    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"

    caregiver = models.ForeignKey(
        "Caregiver",
        on_delete=models.CASCADE,
        related_name="children",
    )
    full_name = EncryptedCharField(max_length=512)
    date_of_birth = models.DateField()
    gender = models.CharField(
        max_length=10,
        choices=Gender.choices,
    )
    unique_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.full_name


class VaccineSchedule(models.Model):
    child = models.ForeignKey(
        "Child",
        on_delete=models.CASCADE,
        related_name="vaccines",
    )
    vaccine_name = models.CharField(max_length=150)
    due_date = models.DateField()
    administered = models.BooleanField(default=False)
    administered_date = models.DateField(null=True, blank=True)
    five_day_email_reminder_sent = models.BooleanField(
        default=False,
        help_text="Set when caregiver was emailed 5 days before due_date for this dose.",
    )

    # --- offline-first replication state -------------------------------
    sync_uid = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True,
        help_text="Stable identity across replicas; the key an envelope targets.",
    )
    version = models.IntegerField(
        default=0,
        help_text="Monotonic server version. A device edit carries the version it "
                  "started from, which is how a concurrent edit is detected.",
    )
    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    last_client_write_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Device clock reading of the most recent edit applied to this "
                  "row. Conflict resolution compares device clocks against each "
                  "other, never against server apply time, which reflects when "
                  "the network came back rather than when the work was done.",
    )
    sync_status = models.CharField(
        max_length=10,
        choices=[("PENDING", "Pending"), ("SYNCED", "Synced")],
        default="SYNCED",
        db_index=True,
    )

    class Meta:
        unique_together = ("child", "vaccine_name")
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.vaccine_name} - {self.child.full_name}"


class ImmunizationCard(models.Model):
    child = models.OneToOneField(
        "Child",
        on_delete=models.CASCADE,
        related_name="immunization_card",
    )
    pdf = models.FileField(upload_to="immunization_cards/")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Immunization Card - {self.child.full_name}"


class SMSLog(models.Model):
    class Status(models.TextChoices):
        SENT = "SENT", "Sent"
        DELIVERED = "DELIVERED", "Delivered"
        FAILED = "FAILED", "Failed"

    child = models.ForeignKey(
        "Child",
        on_delete=models.CASCADE,
        related_name="sms_logs",
    )
    message = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
    )
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"SMS to {self.child.full_name} - {self.status}"


class RiskScore(models.Model):
    class Level(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"

    child = models.ForeignKey(
        "Child",
        on_delete=models.CASCADE,
        related_name="risk_scores",
    )
    score = models.IntegerField()
    level = models.CharField(
        max_length=10,
        choices=Level.choices,
    )
    dropout_probability = models.FloatField(default=0.0)
    calculated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-calculated_at"]

    def __str__(self):
        return f"{self.child.full_name} - {self.level} ({self.score})"


class NotificationLog(models.Model):
    """Unified multi-channel notification record.

    Enqueue and dispatch are separate events with separate timestamps. That
    split is what makes the offline claim measurable: while a facility is
    offline a message is QUEUED locally with `queued_at` set and
    `dispatched_at` null, and the queue drains on reconnection. It also
    separates the latency the system controls (enqueue) from the latency it
    does not (gateway round-trip), which the paper reports separately.
    """

    class Channel(models.TextChoices):
        SMS = "SMS", "SMS"
        EMAIL = "EMAIL", "Email"
        CALENDAR = "CALENDAR", "Calendar (.ics)"
        VOICE = "VOICE", "Automated voice call"

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Queued (offline or awaiting dispatch)"
        SENT = "SENT", "Sent to gateway"
        DELIVERED = "DELIVERED", "Delivery confirmed"
        FAILED = "FAILED", "Failed"

    class Tier(models.TextChoices):
        PLANNING = "PLANNING", "Planning (T-5 days, email)"
        URGENCY = "URGENCY", "Urgency (T-3 days, SMS)"
        OVERDUE = "OVERDUE", "Overdue alert (SMS)"
        PERSISTENCE = "PERSISTENCE", "Persistence (.ics at registration)"
        CARD_UPDATE = "CARD_UPDATE", "Card update (ticked card re-sent after a dose change)"
        VOICE_CALL = "VOICE_CALL", "Voice call (automated assistant)"

    child = models.ForeignKey(
        "Child", on_delete=models.CASCADE, related_name="notifications"
    )
    channel = models.CharField(max_length=10, choices=Channel.choices, db_index=True)
    tier = models.CharField(max_length=12, choices=Tier.choices, blank=True, default="")
    message = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True
    )
    attempts = models.IntegerField(default=0)
    error = models.CharField(max_length=255, blank=True, default="")

    queued_at = models.DateTimeField(auto_now_add=True, db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    enqueue_ms = models.FloatField(default=0.0, help_text="Local enqueue latency.")
    gateway_ms = models.FloatField(default=0.0, help_text="Gateway round-trip latency.")

    # Idempotency: one message per (child, channel, tier, target date).
    dedupe_key = models.CharField(max_length=128, blank=True, default="", db_index=True)

    class Meta:
        ordering = ["-queued_at"]
        indexes = [models.Index(fields=["status", "channel"])]

    def __str__(self):
        return f"{self.channel} to {self.child_id} [{self.status}]"


class CalendarEvent(models.Model):
    """A vaccination visit materialised into the caregiver's calendar.

    The persistence tier of the adherence architecture. One row per generated
    .ics event, so delivery of the calendar layer can be audited and, in the
    planned trial, attributed separately from SMS and email.
    """

    child = models.ForeignKey(
        "Child", on_delete=models.CASCADE, related_name="calendar_events"
    )
    title = models.CharField(max_length=200)
    event_date = models.DateField(db_index=True)
    ics_uid = models.CharField(max_length=128, unique=True)
    delivered = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["event_date"]

    def __str__(self):
        return f"{self.title} ({self.event_date})"
