"""Monitoring dashboard aggregation.

The published dashboard figure showed eight district rows for five districts,
with the per-district overdue counts summing to more than the headline total.
The cause was not a join in the aggregate query but `Caregiver.Meta.ordering`:
Django appends every ordering column to a `SELECT DISTINCT`, so `created_at`
entered the projection and each caregiver produced its own district row.

These tests pin the fixed behaviour so the figure cannot silently regress.
"""
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.core.auth import MONITOR_GROUP_NAME
from apps.immunization.models import Caregiver, Child
from apps.immunization.services.detection import get_overdue_vaccines


def make_child(district, dob, name="Child"):
    caregiver = Caregiver.objects.create(
        full_name="Caregiver " + district, phone_number="+25670000%04d" % Caregiver.objects.count(),
        district=district, village="V",
        preferred_language=Caregiver.PreferredLanguage.ENGLISH,
    )
    return Child.objects.create(
        caregiver=caregiver, full_name=name, date_of_birth=dob,
        gender=Child.Gender.MALE,
    )


class DistrictAggregationTests(TestCase):
    DISTRICTS = ["Gulu", "Mbarara", "Jinja", "Wakiso", "Kampala"]

    def setUp(self):
        dob = timezone.now().date() - timedelta(days=300)
        for district in self.DISTRICTS:
            for i in range(4):                      # 4 children per district
                make_child(district, dob, "%s child %d" % (district, i))

        user = User.objects.create_user("mon", password="correct-horse-battery")
        user.groups.add(Group.objects.get_or_create(name=MONITOR_GROUP_NAME)[0])
        self.client.force_login(user)

    def test_one_row_per_district_not_one_per_caregiver(self):
        rows = self.client.get(reverse("dashboard")).context["district_stats"]
        names = [r["district"] for r in rows]
        self.assertEqual(len(names), len(self.DISTRICTS))
        self.assertEqual(sorted(names), sorted(self.DISTRICTS))
        self.assertEqual(len(names), len(set(names)))

    def test_per_district_overdue_counts_sum_to_the_headline_total(self):
        """The check a reviewer would run on the figure."""
        ctx = self.client.get(reverse("dashboard")).context
        self.assertEqual(
            sum(r["overdue_count"] for r in ctx["district_stats"]),
            ctx["overdue_vaccines"],
        )

    def test_per_district_child_counts_sum_to_the_headline_total(self):
        ctx = self.client.get(reverse("dashboard")).context
        self.assertEqual(
            sum(r["children_count"] for r in ctx["district_stats"]),
            ctx["total_children"],
        )

    def test_headline_overdue_matches_the_detection_module(self):
        ctx = self.client.get(reverse("dashboard")).context
        self.assertEqual(ctx["overdue_vaccines"], get_overdue_vaccines().count())

    def test_a_caregiver_with_no_children_contributes_no_district_row(self):
        Caregiver.objects.create(
            full_name="Childless", phone_number="+256700999999", district="Soroti",
            village="V", preferred_language=Caregiver.PreferredLanguage.ENGLISH,
        )
        rows = self.client.get(reverse("dashboard")).context["district_stats"]
        self.assertNotIn("Soroti", [r["district"] for r in rows])


class DashboardQueryCostTests(TestCase):
    """The district breakdown must not cost a pair of queries per district.

    This is the property behind the scalability fix: adding districts must not
    add round-trips, or the dashboard becomes the slowest operation in the
    system at district scale.
    """

    def setUp(self):
        user = User.objects.create_user("mon", password="correct-horse-battery")
        user.groups.add(Group.objects.get_or_create(name=MONITOR_GROUP_NAME)[0])
        self.client.force_login(user)

    def _load(self, districts):
        dob = timezone.now().date() - timedelta(days=300)
        for district in districts:
            make_child(district, dob, district + " child")

    def test_query_count_does_not_grow_with_district_count(self):
        self._load(["Gulu", "Mbarara"])
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse("dashboard"))

        self._load(["Jinja", "Wakiso", "Kampala", "Soroti", "Mbale", "Lira"])
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse("dashboard"))

        self.assertEqual(len(few), len(many))
