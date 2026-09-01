"""
Generate docs/EIMS_User_Guide.docx (Word) for administrators and doctors.
Run from project root: python scripts/generate_eims_user_guide.py
Requires: pip install python-docx
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    out_dir = root / "docs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "EIMS_User_Guide.docx"

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    title = doc.add_heading("Electronic Immunization Management Information System (EIMS)", level=0)
    title.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER

    sub = doc.add_paragraph()
    sub.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    run = sub.add_run("User guide for administrators and doctors")
    run.bold = True

    doc.add_paragraph(
        "This document describes what EIMS does, how it fits into routine immunization "
        "support workflows, and how to use the system in two roles: Django administrator "
        "(superuser) and clinical doctor (Doctor group user). "
        "It reflects the application as implemented in this codebase."
    )

    doc.add_heading("1. What EIMS is", level=1)
    doc.add_paragraph(
        "EIMS is a web-based Electronic Immunization Management Information System built with "
        "Django and PostgreSQL. It helps health facilities register children and caregivers, "
        "maintain national immunization schedules (due dates and vaccine names), record when "
        "doses are given, produce printable immunization cards (PDF), and share calendar "
        "information with caregivers via email (standard .ics file and Google Calendar links). "
        "The system also supports monitoring at a glance (dashboard metrics), dropout-risk "
        "scoring, and reminder workflows: email to caregivers five days before a due date, SMS for overdue "
        "and upcoming doses (real delivery via Twilio when configured, otherwise simulated SMSLog entries)."
    )

    doc.add_heading("2. Core capabilities", level=1)
    for line in [
        "Caregiver and child registration with automatic vaccine schedule generation when a child is created.",
        "Doctor portal: search children, open records, mark vaccines as administered or cancel an administration.",
        "Immunization card (PDF) generation from the current schedule; preview in the browser; optional download.",
        "Email to caregiver (primary and alternate email when present): PDF + .ics attachment, links, and Google Calendar links per visit.",
        "Public monitoring dashboard with aggregates (children, due/overdue vaccines, risk).",
        "Run immunization analysis (risk scoring plus reminders) from the dashboard or management command; "
        "schedule this daily in production so the five-day email and SMS windows are applied on time.",
        "Django admin for data stewardship and bulk actions on children (generate card, email caregiver).",
        "Automated caregiver email five days before each due visit (when analysis runs on that calendar day); "
        "SMS overdue and three-day-upcoming reminders; Twilio env vars enable real SMS (see section 8 and HOWTORUN.md).",
        "Direct URLs for PDF and .ics downloads per child UUID (useful for testing and integration).",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("3. Roles and access", level=1)
    doc.add_paragraph(
        "Administrator: a Django superuser. Uses /admin/ to manage all models, run bulk actions, "
        "and correct data. Typically also responsible for deployment, backups, and user creation."
    )
    doc.add_paragraph(
        "Doctor: a normal Django user who belongs to the group named exactly \"Doctor\". "
        "Doctors sign in at the application login page and use the Doctor Portal and child workflows. "
        "They are not required to be superusers."
    )
    doc.add_paragraph(
        "Caregiver: not a system login role. Caregiver details are stored on the child record; "
        "caregivers receive materials by email when staff use Send Calendar + Card (or admin bulk email)."
    )

    doc.add_heading("4. Environment and first-time setup (summary)", level=1)
    doc.add_paragraph(
        "Full technical steps (database, .env, migrations, email) are documented in HOWTORUN.md "
        "in the project root. In short:"
    )
    for line in [
        "Install Python 3.10+, PostgreSQL, and project dependencies (pip install -r requirements.txt).",
        "Create database and .env with DB_* variables; run python manage.py migrate.",
        "Create an admin: python manage.py createsuperuser.",
        "Create doctors: python manage.py create_doctor --username ... --password ... --email ...",
        "Start the server: python manage.py runserver.",
        "Optional: configure EMAIL_* and SITE_URL in .env so caregiver emails send via SMTP instead of console.",
        "Optional: configure TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER for real SMS reminders.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("5. Administrator guide", level=1)

    doc.add_heading("5.1 Signing in", level=2)
    doc.add_paragraph(
        "Open the Django Admin at http://127.0.0.1:8000/admin/ (replace host/port in production). "
        "Sign in with the superuser username and password created via createsuperuser."
    )

    doc.add_heading("5.2 Main admin sections", level=2)
    doc.add_paragraph(
        "Immunization models appear under the immunization app. Typical use:"
    )
    for line in [
        "Caregivers: view and edit contact details (phone, email, alternate email, district, village, language).",
        "Children: list children, link to caregiver, filter by gender; this is where bulk actions run (see below).",
        "Vaccine schedules: inspect or correct due dates, administered flag, administered date, and "
        "five-day email reminder flag (whether a 5-day-before-due reminder was already sent for that dose).",
        "Immunization cards: stored PDF files associated with children after generation.",
        "SMS logs and risk scores: audit reminder traffic and computed risk for each child.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("5.3 Bulk actions on children (admin)", level=2)
    doc.add_paragraph(
        "In Admin → Children, select one or more rows, then choose an action from the dropdown:"
    )
    doc.add_paragraph(
        "Generate immunization card (PDF) for selected children — rebuilds the PDF from the current "
        "schedule and saves it to the ImmunizationCard record for each selected child.",
        style="List Bullet",
    )
    doc.add_paragraph(
        "Send immunization card + calendar email to caregiver(s) — for each selected child, rebuilds "
        "assets, stores the PDF, and sends the same style of email as the doctor’s Send Calendar + Card "
        "(PDF and .ics attachments, download links, Google Calendar links per visit, platform import tips). "
        "Children without any email are skipped; the admin message indicates how many were emailed.",
        style="List Bullet",
    )

    doc.add_heading("5.4 Monitoring dashboard", level=2)
    doc.add_paragraph(
        "The public dashboard lives at the site root (http://127.0.0.1:8000/). It shows summary "
        "statistics such as total children, vaccines due in the coming week, overdue counts, "
        "high-risk child counts, average dropout probability, and district-level breakdowns. "
        "From here, staff can open the Doctor Portal (if they use that account in the same browser) "
        "or trigger Run Immunization Analysis, which executes the analysis engine and may send reminders."
    )

    doc.add_heading("5.5 User management", level=2)
    doc.add_paragraph(
        "Create additional doctors from the shell with create_doctor so they receive the Doctor group. "
        "You may also assign the Doctor group manually in Admin → Authentication → Users, but the "
        "management command is the documented path for consistency."
    )

    doc.add_heading("6. Doctor guide", level=1)

    doc.add_heading("6.1 Signing in", level=2)
    doc.add_paragraph(
        "Doctors use http://127.0.0.1:8000/login/ (not the admin URL). Enter the username and password "
        "provided by the administrator. Only users in the Doctor group may use doctor-only pages; "
        "the create_doctor command adds that group automatically."
    )

    doc.add_heading("6.2 Doctor Portal (home)", level=2)
    doc.add_paragraph(
        "After login, the Doctor Portal lists recently registered children (up to 50 by default), "
        "with search by name, caregiver name, or child UUID."
    )
    for line in [
        "Register Birth — opens a form to create a caregiver and child; vaccine schedules are created automatically for the new child.",
        "View — opens the child detail page.",
        "View Card — opens the immunization card preview page for that child (no download until you click Download Card).",
        "Logout — use the Logout button (submits a POST request; required by modern Django for security).",
        "Dashboard — link to the monitoring dashboard.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("6.3 Child detail page", level=2)
    doc.add_paragraph("For one child you will see demographic blocks and two vaccine tables:")
    for line in [
        "Pending vaccines — doses not yet marked administered; each row has Administered to record the dose.",
        "Completed vaccines — doses marked administered, with date; Cancel Administered returns a dose to pending if entered by mistake.",
        "Generate Immunization Card — POST action that builds/refreshes the PDF and redirects to the preview page.",
        "View Card — opens the preview page with the embedded card, Download Card, and Send Calendar + Card.",
        "Send Calendar + Card — emails the caregiver the PDF and .ics calendar (and instructions/links) if an email address exists.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("6.4 Immunization card preview", level=2)
    doc.add_paragraph(
        "Route: /doctor/child/<child-uuid>/view-card/ (also linked as View Card from the portal and child page). "
        "The preview opens in the same browser tab (not a pop-up). The PDF is shown inline; only the "
        "Download Card control triggers a file download. Send Calendar + Card emails the caregiver from this page."
    )

    doc.add_heading("6.5 Registration form (Register Birth)", level=2)
    doc.add_paragraph(
        "Capture caregiver name, phone, email (optional), alternate email (optional), district, village, "
        "preferred language, and child name, date of birth, and gender. Submitting creates the records "
        "and redirects to the new child's detail page."
    )

    doc.add_heading("7. Calendar and caregiver email", level=1)
    doc.add_paragraph(
        "The .ics file contains one all-day event per visit date, aligned to the child's scheduled due dates. "
        "It works with Google Calendar, Apple Calendar, and typical Android calendar apps once imported or opened. "
        "The email body includes short guidance for each platform. Separate Google Calendar template links are "
        "included (one per visit) for caregivers who prefer adding events via Google."
    )

    doc.add_heading("8. Analysis, risk, and reminders", level=1)
    doc.add_paragraph(
        "Running analysis (dashboard button or python manage.py analyze_immunization) processes every child, "
        "recalculates risk, then sends the reminder rules below. For the five-day email to fire on the correct "
        "calendar day, run analysis at least once per day (e.g. scheduled task or cron in production)."
    )
    for line in [
        "Email (5 days before due): when a dose is not administered and its due date is exactly five "
        "calendar days after today, one email per child lists those vaccine names and the due date (requires "
        "caregiver primary or alternate email). Each schedule row stores whether that reminder was sent; "
        "Cancel Administered on the doctor screen clears that flag for the row.",
        "SMS — overdue: one message per child who has any overdue (not administered) dose.",
        "SMS — upcoming: one message per child who has a dose due within the next three calendar days (inclusive).",
        "SMS delivery: set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, and TWILIO_FROM_NUMBER in .env (Twilio number from). "
        "Caregiver phone numbers should be in E.164 where possible (e.g. +256…). If Twilio is not configured, "
        "no external SMS is sent; SMSLog may still receive a simulated SENT/FAILED entry for development. "
        "Other gateways (e.g. Africa’s Talking) are not built in; extend sms_service.py if needed. Full env "
        "details: HOWTORUN.md.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("9. Reference URLs (development)", level=1)
    for line in [
        "Dashboard: /",
        "Admin: /admin/",
        "Doctor login: /login/",
        "Doctor portal: /doctor/",
        "Register birth: /doctor/register/",
        "Child card PDF: /immunization/child/<uuid>/card.pdf (optional ?download=1 to suggest download)",
        "Schedule .ics: /immunization/child/<uuid>/schedule.ics",
        "Doctor card preview: /doctor/child/<uuid>/view-card/",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_heading("10. Troubleshooting", level=1)
    for line in [
        "Doctor cannot log in — verify Doctor group membership and use /login/, not /admin/, unless also a staff user.",
        "Logout returns 405 — always use the Logout button (POST); bookmarking GET /logout/ will fail.",
        "Emails not received — set EMAIL_* in .env, restart server, confirm caregiver has email; use send_test_email command per HOWTORUN.md.",
        "Calendar import — caregivers must open/import the attachment; dates are pre-encoded in the file per visit.",
        "SMS not arriving — confirm Twilio credentials and FROM number; confirm caregiver phone includes country code; "
        "check Admin → SMS logs for FAILED; verify analysis job actually ran today.",
        "Five-day email missed — analysis must run on the day that is exactly five days before the due date; "
        "check caregiver has email; check vaccine schedule row not already marked reminder sent unless dose was reset.",
    ]:
        doc.add_paragraph(line, style="List Bullet")

    doc.add_paragraph("")
    foot = doc.add_paragraph()
    foot.add_run(
        "End of document. Regenerate with: python scripts/generate_eims_user_guide.py"
    ).italic = True

    doc.save(out_path)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
