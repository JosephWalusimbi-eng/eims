from datetime import timedelta
import base64

from django.db.models import Count, OuterRef, Q, Subquery
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.urls import reverse

from apps.immunization.models import Caregiver, Child, RiskScore, VaccineSchedule
from apps.immunization.services.analysis_engine import run_analysis
from apps.immunization.services.detection import get_overdue_vaccines
from apps.immunization.services.vaccine_service import administer_vaccine
from apps.core import audit_helpers as audit
from apps.core.auth import user_is_doctor, user_is_staff_member
from apps.core.forms import CaregiverChildRegistrationForm
from apps.immunization.services.card_service import build_child_card_assets, upsert_immunization_card
from apps.immunization.services.email_service import send_immunization_card_email


@login_required
@user_passes_test(user_is_staff_member)
def dashboard(request):
    """Facility monitoring view.

    Aggregate only -- no child is named here -- but it still reveals cohort
    size and coverage gaps per district, so it is behind authentication.
    """
    audit.log(request, "READ", "Dashboard")
    today = timezone.now().date()
    week_end = today + timedelta(days=7)

    total_children = Child.objects.count()

    vaccines_due_this_week = VaccineSchedule.objects.filter(
        due_date__gte=today,
        due_date__lte=week_end,
        administered=False,
    ).count()

    overdue_vaccines = get_overdue_vaccines().count()

    high_risk_children = Child.objects.filter(
        risk_scores__level=RiskScore.Level.HIGH,
    ).distinct().count()

    # Priority follow-up list.
    #
    # The dashboard previously headlined a facility-level mean of P_d. The
    # sensitivity analysis showed that P_d is robust as an *ordering* but not
    # as a calibrated probability, which makes averaging it across a facility
    # meaningless -- reweighting shifts the whole distribution while barely
    # disturbing the rank order. What the score does support is a ranked
    # follow-up list, so that is what the dashboard now reports.
    latest_risk_scores = RiskScore.objects.filter(
        id=Subquery(
            RiskScore.objects.filter(child=OuterRef("child"))
            .order_by("-calculated_at")
            .values("id")[:1]
        )
    )
    scored = list(
        latest_risk_scores.order_by("-dropout_probability")
        .values_list("child_id", "dropout_probability")
    )
    follow_up_budget = max(1, int(round(0.10 * len(scored)))) if scored else 0
    priority_ids = {cid for cid, _ in scored[:follow_up_budget]}
    priority_threshold = scored[follow_up_budget - 1][1] if follow_up_budget else 0.0

    # District-level statistics.
    #
    # One grouped query, not two per district. The previous loop issued a pair
    # of counting queries for every district, each scanning the schedule table;
    # at 100k children that made the dashboard the slowest operation in the
    # system by an order of magnitude (6.0 s, against 0.4 s for a full overdue
    # sweep over the same rows). Aggregating in one GROUP BY removes the
    # dependence on district count entirely.
    #
    # `.order_by()` is still load-bearing on the grouping. Caregiver.Meta sets
    # a default ordering, and Django appends every ordering column to the
    # GROUP BY and to a SELECT DISTINCT alike, so `created_at` entered the
    # projection and split one district row into one row per caregiver. That
    # is the defect behind the duplicated district table in the published
    # dashboard figure.
    district_rows = (
        Caregiver.objects.filter(children__isnull=False)
        .order_by()
        .values("district")
        .annotate(
            children_count=Count("children", distinct=True),
            overdue_count=Count(
                "children__vaccines",
                filter=Q(
                    children__vaccines__due_date__lt=today,
                    children__vaccines__administered=False,
                ),
                distinct=True,
            ),
            high_risk_count=Count(
                "children",
                filter=Q(children__risk_scores__level=RiskScore.Level.HIGH),
                distinct=True,
            ),
            priority_count=Count(
                "children", filter=Q(children__id__in=priority_ids), distinct=True
            ),
        )
        .order_by("-children_count", "district")
    )
    district_stats = [
        {
            "district": row["district"] or "Unknown",
            "children_count": row["children_count"],
            "overdue_count": row["overdue_count"],
            "high_risk_count": row["high_risk_count"],
            "priority_count": row["priority_count"],
        }
        for row in district_rows
    ]

    context = {
        "total_children": total_children,
        "vaccines_due_this_week": vaccines_due_this_week,
        "overdue_vaccines": overdue_vaccines,
        "high_risk_children": high_risk_children,
        "priority_follow_up": len(priority_ids),
        "priority_threshold": priority_threshold,
        "district_stats": district_stats,
    }

    return render(request, "core/dashboard.html", context)


@login_required
@user_passes_test(user_is_staff_member)
@require_POST
def run_analysis_view(request):
    """Trigger a scoring and reminder pass.

    POST-only and authenticated: the pass dispatches real SMS and email, so an
    unauthenticated GET was a request-forgery amplifier as well as an
    unmetered gateway spend.
    """
    audit.log(request, "UPDATE", "AnalysisRun", detail="manual trigger")
    processed, reminders = run_analysis()
    messages.success(
        request,
        f"Analysis completed. Processed {processed} children and sent {reminders} reminders.",
    )
    return redirect("dashboard")


@login_required
@user_passes_test(user_is_doctor)
def doctor_portal(request):
    q = (request.GET.get("q") or "").strip()
    children = Child.objects.select_related("caregiver").all().order_by("-created_at")

    if q:
        children = children.filter(
            Q(full_name__icontains=q)
            | Q(caregiver__full_name__icontains=q)
            | Q(unique_id__icontains=q)
        )

    children = list(children[:50])
    audit.log(request, "READ", "ChildList",
              detail=f"query={q[:60]!r} results={len(children)}")
    return render(request, "core/doctor_portal.html", {"q": q, "children": children})


@login_required
@user_passes_test(user_is_doctor)
def doctor_child_detail(request, child_uuid):
    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    audit.log(request, "READ", "Child", child.unique_id)
    schedules = list(child.vaccines.order_by("due_date", "vaccine_name").all())
    pending = [s for s in schedules if not s.administered]
    completed = [s for s in schedules if s.administered]
    return render(
        request,
        "core/doctor_child_detail.html",
        {
            "child": child,
            "pending": pending,
            "completed": completed,
        },
    )


@login_required
@user_passes_test(user_is_doctor)
def doctor_register_child(request):
    if request.method == "POST":
        form = CaregiverChildRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                caregiver = Caregiver.objects.create(
                    full_name=form.cleaned_data["caregiver_full_name"],
                    phone_number=form.cleaned_data["caregiver_phone_number"],
                    email=form.cleaned_data["caregiver_email"] or "",
                    alternate_email=form.cleaned_data["caregiver_alternate_email"] or "",
                    district=form.cleaned_data["caregiver_district"] or "",
                    village=form.cleaned_data["caregiver_village"],
                    preferred_language=form.cleaned_data["caregiver_preferred_language"],
                )
                child = Child.objects.create(
                    caregiver=caregiver,
                    full_name=form.cleaned_data["child_full_name"],
                    date_of_birth=form.cleaned_data["child_date_of_birth"],
                    gender=form.cleaned_data["child_gender"],
                )

            messages.success(request, f"Registered child: {child.full_name}")
            return redirect("doctor_child_detail", child_uuid=child.unique_id)
    else:
        form = CaregiverChildRegistrationForm()

    return render(request, "core/doctor_register_child.html", {"form": form})


@login_required
@user_passes_test(user_is_doctor)
def doctor_generate_card(request, child_uuid):
    if request.method != "POST":
        return redirect("doctor_portal")

    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    pdf_bytes, _, _ = build_child_card_assets(child)
    upsert_immunization_card(child, pdf_bytes)
    messages.success(request, "Immunization card generated. You can preview or download it.")
    return redirect("doctor_view_card", child_uuid=child.unique_id)


@login_required
@user_passes_test(user_is_doctor)
def doctor_view_card(request, child_uuid):
    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    pdf_bytes, _, _ = build_child_card_assets(child)
    upsert_immunization_card(child, pdf_bytes)
    pdf_base64 = base64.b64encode(pdf_bytes).decode("ascii")
    return render(
        request,
        "core/doctor_card_view.html",
        {
            "child": child,
            "pdf_data_uri": f"data:application/pdf;base64,{pdf_base64}",
        },
    )


@login_required
@user_passes_test(user_is_doctor)
def doctor_send_calendar(request, child_uuid):
    if request.method != "POST":
        return redirect("doctor_portal")

    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    pdf_bytes, ics_bytes, google_links = build_child_card_assets(child)
    upsert_immunization_card(child, pdf_bytes)

    from apps.immunization.views import sign_artifact_link

    pdf_url = sign_artifact_link(child, "pdf")
    ics_url = sign_artifact_link(child, "ics")

    sent = send_immunization_card_email(
        child=child,
        pdf_bytes=pdf_bytes,
        ics_bytes=ics_bytes,
        google_links=google_links,
        pdf_download_url=pdf_url,
        ics_download_url=ics_url,
    )
    if sent:
        messages.success(request, "Calendar + card sent to caregiver email.")
    else:
        messages.warning(request, "No caregiver email found; nothing sent.")

    return redirect("doctor_child_detail", child_uuid=child.unique_id)


@login_required
@user_passes_test(user_is_doctor)
def doctor_administer_vaccine(request, schedule_id: int):
    if request.method != "POST":
        return redirect("doctor_portal")

    schedule = get_object_or_404(
        VaccineSchedule.objects.select_related("child", "child__caregiver"),
        id=schedule_id,
    )
    administer_vaccine(schedule)
    messages.success(
        request,
        f"Marked administered: {schedule.child.full_name} - {schedule.vaccine_name}",
    )
    return redirect("doctor_child_detail", child_uuid=schedule.child.unique_id)


@login_required
@user_passes_test(user_is_doctor)
def doctor_unadminister_vaccine(request, schedule_id: int):
    if request.method != "POST":
        return redirect("doctor_portal")

    schedule = get_object_or_404(
        VaccineSchedule.objects.select_related("child", "child__caregiver"),
        id=schedule_id,
    )
    schedule.administered = False
    schedule.administered_date = None
    schedule.five_day_email_reminder_sent = False
    schedule.save(
        update_fields=["administered", "administered_date", "five_day_email_reminder_sent"]
    )
    messages.success(
        request,
        f"Administration cancelled: {schedule.child.full_name} - {schedule.vaccine_name}",
    )
    return redirect("doctor_child_detail", child_uuid=schedule.child.unique_id)
