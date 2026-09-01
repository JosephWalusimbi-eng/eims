from django.urls import path

from apps.immunization import views


urlpatterns = [
    path("child/<uuid:child_uuid>/card.pdf", views.immunization_card_pdf, name="immunization_card_pdf"),
    path("child/<uuid:child_uuid>/schedule.ics", views.immunization_schedule_ics, name="immunization_schedule_ics"),
]

