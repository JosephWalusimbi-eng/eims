from __future__ import annotations

from django import forms

from apps.immunization.models import Caregiver, Child


class CaregiverChildRegistrationForm(forms.Form):
    caregiver_full_name = forms.CharField(max_length=150)
    caregiver_phone_number = forms.CharField(max_length=20)
    caregiver_email = forms.EmailField(required=False)
    caregiver_alternate_email = forms.EmailField(required=False)
    caregiver_district = forms.CharField(max_length=150, required=False)
    caregiver_village = forms.CharField(max_length=150)
    caregiver_preferred_language = forms.ChoiceField(choices=Caregiver.PreferredLanguage.choices)

    child_full_name = forms.CharField(max_length=150)
    child_date_of_birth = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}))
    child_gender = forms.ChoiceField(choices=Child.Gender.choices)

