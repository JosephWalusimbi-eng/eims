from datetime import timedelta
import base64

from django.db.models import Count, OuterRef, Q, Subquery
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.http import Http404
from django.utils.http import url_has_allowed_host_and_scheme
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.urls import reverse

from apps.immunization.models import Caregiver, Child, NotificationLog, RiskScore, VaccineSchedule
from apps.immunization.services.analysis_engine import run_analysis
from apps.immunization.services.detection import get_overdue_vaccines
from apps.immunization.services.vaccine_service import administer_vaccine
from apps.core import audit_helpers as audit
from apps.core.auth import user_is_doctor, user_is_staff_member
from apps.core.forms import CaregiverChildRegistrationForm
from apps.immunization.services import atrisk
from apps.immunization.services import notification_service as notify
from apps.immunization.services.card_service import (
    build_child_card_assets,
    queue_card_update,
    upsert_immunization_card,
)
from apps.immunization.services.voice_service import queue_voice_call
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

    # "At risk" has exactly one definition (services/atrisk.py), shared with the
    # lists these tiles link to, so a tile and its list always agree.
    high_risk_children = Child.objects.filter(pk__in=atrisk.high_risk_child_ids()).count()
    overdue_children = atrisk.children_for("overdue").count()

    # Priority follow-up list.
    #
    # The dashboard previously headlined a facility-level mean of P_d. The
    # sensitivity analysis showed that P_d is robust as an *ordering* but not
    # as a calibrated probability, which makes averaging it across a facility
    # meaningless -- reweighting shifts the whole distribution while barely
    # disturbing the rank order. What the score does support is a ranked
    # follow-up list, so that is what the dashboard reports.
    priority_ids, priority_threshold = atrisk.priority_ids()

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
                filter=Q(children__id__in=atrisk.high_risk_child_ids()),
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
        "overdue_children": overdue_children,
        "can_name_children": user_is_doctor(request.user),
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


PAGE_SIZE = 25


@login_required
@user_passes_test(user_is_staff_member)
def at_risk_list(request):
    """Drill-down behind a dashboard tile: *which* children, not just how many.

    Doctors see names and caregiver contact and can open the record or place a
    call. Monitoring officers see the same ranked rows but pseudonymised -- a
    short reference, district and village only -- because the Monitor role is
    scoped to aggregate oversight (paper Sec. VI) and must not gain a roster of
    named children through a link.
    """
    kind = request.GET.get("filter", "priority")
    if kind not in atrisk.FILTERS:
        raise Http404
    district = (request.GET.get("district") or "").strip() or None
    named = user_is_doctor(request.user)

    qs = atrisk.children_for(kind, district)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    audit.log(
        request, "READ", "AtRiskList",
        detail=f"filter={kind} district={district or '*'} page={page.number} named={named}",
    )
    return render(
        request,
        "core/at_risk_list.html",
        {
            "kind": kind,
            "title": atrisk.FILTERS[kind],
            "district": district,
            "page": page,
            "named": named,
            "filters": atrisk.FILTERS,
        },
    )


@login_required
@user_passes_test(user_is_doctor)
@require_POST
def doctor_update_card_and_send(request, child_uuid):
    """Regenerate the card with its current ticks and send it now.

    Queued first, then that one row is dispatched: the doctor sees the real
    outcome, and if the facility is offline the update stays in the queue and
    goes out on reconnection instead of being lost.
    """
    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    if not (child.caregiver.email or child.caregiver.alternate_email):
        messages.warning(request, "Card refreshed, but there is no caregiver email on record; nothing sent.")
        upsert_immunization_card(child, build_child_card_assets(child)[0])
        return redirect("doctor_child_detail", child_uuid=child.unique_id)

    upsert_immunization_card(child, build_child_card_assets(child)[0])
    log = queue_card_update(child, reason="manual update by clinician")
    audit.log(request, "UPDATE", "Child", child.unique_id, detail="card updated and sent")
    ok, err = notify.dispatch_log(log)
    if ok:
        messages.success(request, "Card updated with current ticks and sent to the caregiver.")
    else:
        messages.warning(
            request,
            "Card updated. Delivery failed (%s); it is queued and will be retried." % (err or "unknown error"),
        )
    return redirect("doctor_child_detail", child_uuid=child.unique_id)


@login_required
@user_passes_test(user_is_doctor)
@require_POST
def doctor_call_caregiver(request, child_uuid):
    """Place an automated assistant call to the caregiver (via the queue)."""
    child = get_object_or_404(Child.objects.select_related("caregiver"), unique_id=child_uuid)
    overdue = child.vaccines.filter(
        administered=False, due_date__lt=timezone.now().date()
    ).exists()
    log = queue_voice_call(
        child, overdue=overdue,
        dedupe_key=f"voice-manual:{child.pk}:{timezone.now().isoformat()}",
    )
    audit.log(request, "EXPORT", "Child", child.unique_id, detail="automated voice call requested")
    ok, err = notify.dispatch_log(log)
    if ok:
        messages.success(request, "Automated call placed to the caregiver.")
    else:
        messages.warning(request, "Call could not be placed (%s); it is queued for retry." % (err or "unknown error"))
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect("doctor_child_detail", child_uuid=child.unique_id)
