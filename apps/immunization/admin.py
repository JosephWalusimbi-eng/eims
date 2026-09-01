from django.contrib import admin
from django.urls import reverse

from .models import Caregiver, Child, ImmunizationCard, RiskScore, SMSLog, VaccineSchedule
from .services.card_service import build_child_card_assets, upsert_immunization_card
from .services.email_service import send_immunization_card_email


@admin.register(Caregiver)
class CaregiverAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "phone_number",
        "email",
        "alternate_email",
        "village",
        "preferred_language",
        "created_at",
    )
    search_fields = ("full_name", "phone_number", "email", "alternate_email", "village")
    list_filter = ("preferred_language",)


@admin.register(Child)
class ChildAdmin(admin.ModelAdmin):
    list_display = ("full_name", "caregiver", "gender", "date_of_birth", "created_at")
    search_fields = ("full_name",)
    list_filter = ("gender",)
    autocomplete_fields = ("caregiver",)
    list_select_related = ("caregiver",)
    actions = ["generate_immunization_card", "send_calendar_to_email"]

    @admin.action(description="Generate immunization card (PDF) for selected children")
    def generate_immunization_card(self, request, queryset):
        count = 0
        for child in queryset.select_related("caregiver"):
            pdf_bytes, _, _ = build_child_card_assets(child)
            upsert_immunization_card(child, pdf_bytes)
            count += 1
        self.message_user(request, f"Generated immunization card for {count} child(ren).")

    @admin.action(description="Send immunization card + calendar email to caregiver(s)")
    def send_calendar_to_email(self, request, queryset):
        sent_any = 0
        for child in queryset.select_related("caregiver"):
            pdf_bytes, ics_bytes, google_links = build_child_card_assets(child)
            upsert_immunization_card(child, pdf_bytes)

            pdf_url = reverse("immunization_card_pdf", kwargs={"child_uuid": child.unique_id})
            ics_url = reverse("immunization_schedule_ics", kwargs={"child_uuid": child.unique_id})
            sent = send_immunization_card_email(
                child=child,
                pdf_bytes=pdf_bytes,
                ics_bytes=ics_bytes,
                google_links=google_links,
                pdf_download_url=pdf_url,
                ics_download_url=ics_url,
            )
            if sent:
                sent_any += 1

        self.message_user(request, f"Emailed {sent_any} child(ren) (skipped those without emails).")

@admin.register(ImmunizationCard)
class ImmunizationCardAdmin(admin.ModelAdmin):
    list_display = ("child", "created_at", "pdf")
    autocomplete_fields = ("child",)


@admin.register(VaccineSchedule)
class VaccineScheduleAdmin(admin.ModelAdmin):
    list_display = ("child", "vaccine_name", "due_date", "administered", "five_day_email_reminder_sent")
    list_filter = ("administered", "vaccine_name")
    autocomplete_fields = ("child",)


@admin.register(SMSLog)
class SMSLogAdmin(admin.ModelAdmin):
    list_display = ("child", "status", "timestamp")
    list_filter = ("status",)
    autocomplete_fields = ("child",)


@admin.register(RiskScore)
class RiskScoreAdmin(admin.ModelAdmin):
    list_display = ("child", "score", "level", "calculated_at")
    list_filter = ("level",)
    autocomplete_fields = ("child",)
