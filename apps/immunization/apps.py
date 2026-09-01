from django.apps import AppConfig


class ImmunizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.immunization"
    label = "immunization"

    def ready(self):
        from . import signals  # noqa: F401
