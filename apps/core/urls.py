from django.urls import path
from django.contrib.auth import views as auth_views

from .views import (
    dashboard,
    run_analysis_view,
    doctor_portal,
    doctor_child_detail,
    doctor_administer_vaccine,
    doctor_unadminister_vaccine,
    doctor_register_child,
    doctor_generate_card,
    doctor_view_card,
    doctor_send_calendar,
    doctor_update_card_and_send,
    doctor_call_caregiver,
    at_risk_list,
)

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("run-analysis/", run_analysis_view, name="run_analysis"),
    path("at-risk/", at_risk_list, name="at_risk_list"),

    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),

    path("doctor/", doctor_portal, name="doctor_portal"),
    path("doctor/register/", doctor_register_child, name="doctor_register_child"),
    path("doctor/child/<uuid:child_uuid>/", doctor_child_detail, name="doctor_child_detail"),
    path("doctor/child/<uuid:child_uuid>/generate-card/", doctor_generate_card, name="doctor_generate_card"),
    path("doctor/child/<uuid:child_uuid>/view-card/", doctor_view_card, name="doctor_view_card"),
    path("doctor/child/<uuid:child_uuid>/send-calendar/", doctor_send_calendar, name="doctor_send_calendar"),
    path("doctor/child/<uuid:child_uuid>/update-card-and-send/", doctor_update_card_and_send, name="doctor_update_card_and_send"),
    path("doctor/child/<uuid:child_uuid>/call/", doctor_call_caregiver, name="doctor_call_caregiver"),
    path(
        "doctor/vaccine/<int:schedule_id>/administer/",
        doctor_administer_vaccine,
        name="doctor_administer_vaccine",
    ),
    path(
        "doctor/vaccine/<int:schedule_id>/unadminister/",
        doctor_unadminister_vaccine,
        name="doctor_unadminister_vaccine",
    ),
]
