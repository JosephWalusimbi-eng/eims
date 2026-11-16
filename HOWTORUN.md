## How to Run EIMS (Electronic Immunization Management Information System)

This project is a Django app backed by PostgreSQL.

### Prerequisites
- **Python**: 3.10+
- **PostgreSQL**: running locally or remotely

### Install dependencies

```bash
pip install -r requirements.txt
```

### Configure environment variables

Create a `.env` file in the project root (`d:\eims\eims\.env`).

#### Database

```env
DB_NAME=eims
DB_USER=postgres
DB_PASSWORD=StrongPassword123
DB_HOST=localhost
DB_PORT=5432
```

Create the database in PostgreSQL (example):

```bash
createdb eims
```

### Run migrations

```bash
python manage.py migrate
```

### Start the server

```bash
python manage.py runserver
```
if it refuses, maybe if another port is using, try another port python manage.py runserver 8765
Then open http://127.0.0.1:8765/.

### URLs
- **Dashboard**: `http://127.0.0.1:8000/`
- **Admin**: `http://127.0.0.1:8000/admin/`
- **Doctor login**: `http://127.0.0.1:8000/login/`
- **Doctor portal**: `http://127.0.0.1:8000/doctor/`

### Create an Admin (superuser)

```bash
python manage.py createsuperuser
```

### Create a Doctor account (separate login)

Doctors are regular Django users added to the **Doctor** group.

```bash
python manage.py create_doctor --username doctor1 --password DoctorPass123 --email doctor1@example.com
```

Then login at `http://127.0.0.1:8000/login/`.

### Doctor workflow
- **Register a birth (caregiver + child)**: open Doctor portal → **Register Birth**
- **View child details**: Doctor portal → search → **View**
- **Mark vaccine administered**: click **Administered** next to a pending vaccine
- **Generate Immunization Card (PDF)**: click **Generate Immunization Card**
- **Send Calendar + PDF to caregiver email**: click **Send Calendar to Email**

### Admin workflow (Django Admin)
In **Admin → Children**, you can run actions on selected children:
- **Generate immunization card (PDF)**
- **Send immunization card + calendar email to caregiver(s)**

### Immunization card + calendar downloads
For a child identified by `unique_id`:
- **PDF**: `/immunization/child/<uuid>/card.pdf`
- **iOS/Apple Calendar (.ics)**: `/immunization/child/<uuid>/schedule.ics`

When `DEBUG=True`, Django serves media at `/media/...` in development.

---

## Email Setup (Gmail SMTP)

EIMS sends real emails **only when `EMAIL_HOST` is set**. Otherwise it prints emails in the server console.

### Step 1: Create a Gmail App Password
Gmail SMTP requires an **App Password** (not your normal password):
- Enable **2-Step Verification** on your Google account
- Create an **App password** and copy it

### Step 2: Add Gmail SMTP settings to `.env`

```env
DEFAULT_FROM_EMAIL=yourgmail@gmail.com
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=yourgmail@gmail.com
EMAIL_HOST_PASSWORD=YOUR_16_CHAR_APP_PASSWORD

# Recommended for clickable links inside emails:
SITE_URL=http://127.0.0.1:8000
```

Restart the server after updating `.env`.

### Step 3: Send a test email

```bash
python manage.py send_test_email --to your_other_email@example.com
```

If you receive it, caregiver emails will also send.

---

## Reminders (email + SMS)

When you **Run Immunization Analysis** (dashboard button or `analyze_immunization` command), the system:

- **Email (5 days before due date):** for each **not administered** dose whose `due_date` is **exactly five calendar days after today**, sends **one email per child** listing those vaccine(s). Caregivers need a primary or alternate email. Each schedule row is marked so the same dose is not emailed twice (cleared again if a doctor uses **Cancel Administered**).
- **SMS — overdue:** one SMS per child with any overdue (not administered) dose.
- **SMS — upcoming:** one SMS per child with a dose due within the **next 3 days** (inclusive).

Emails use the same SMTP settings as other EIMS mail (`EMAIL_HOST`, etc.). Without SMTP, reminders print to the console like other mail.

### Real SMS (Twilio)

By default, SMS is **simulated**: `SMSLog` rows are created but no message is sent unless Twilio is configured.

Add to `.env` (use your Twilio Console values; **From** must be a Twilio SMS-capable number in **E.164**, e.g. `+15551234567`; **To** uses the caregiver’s stored phone — include country code, e.g. `+256...`):

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_FROM_NUMBER=+1234567890
```

Restart the server. On analysis, overdue and upcoming reminders call Twilio; failed API calls are logged with `SMSLog` status `FAILED`.

Other gateways (e.g. Africa’s Talking) are not built in; you can add a small client in `apps/immunization/services/sms_service.py` alongside the Twilio branch.

### Run reminders / analysis manually

```bash
python manage.py analyze_immunization
```

Or from the dashboard click **Run Immunization Analysis**.

---

## Troubleshooting

### Doctor login says “failed”
- Make sure you are using `http://127.0.0.1:8000/login/`
- Recreate the doctor user:

```bash
python manage.py create_doctor --username doctor1 --password DoctorPass123 --email doctor1@example.com
```

### Emails still print in console
- Confirm `.env` has `EMAIL_HOST=smtp.gmail.com`
- Restart the server
- Run `python manage.py send_test_email --to you@example.com` and confirm it arrives

