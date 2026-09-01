# EIMS System Analysis Report
## Electronic Immunization Management Information System

**Report Date:** February 2026  
**Version:** 1.0

---

## 1. Executive Summary

EIMS is a Django-based web application designed to support immunization tracking and management for health workers in Uganda. The system automates vaccine scheduling, detects overdue vaccinations, calculates child risk scores, simulates SMS reminders, and provides a monitoring dashboard with district-level analytics.

---

## 2. System Architecture

### 2.1 Technology Stack
- **Framework:** Django 5.2
- **Database:** PostgreSQL (configurable via environment variables)
- **Frontend:** Bootstrap 5.3 (CDN)
- **Configuration:** python-dotenv for environment variables

### 2.2 Application Structure
```
config/          # Django project configuration
apps/
├── core/        # Dashboard, views, URLs
└── immunization/ # Core business logic, models, services
```

---

## 3. Data Models

### 3.1 Caregiver
| Field | Type | Description |
|-------|------|-------------|
| full_name | CharField(150) | Caregiver's full name |
| phone_number | CharField(20), indexed | Contact number for SMS |
| district | CharField(150) | Administrative district |
| village | CharField(150) | Village/location |
| preferred_language | CharField | ENGLISH, LUGANDA, ATESO, RUNYANKOLE |
| created_at | DateTimeField | Record creation timestamp |

### 3.2 Child
| Field | Type | Description |
|-------|------|-------------|
| caregiver | ForeignKey | Links to Caregiver |
| full_name | CharField(150) | Child's full name |
| date_of_birth | DateField | Used for vaccine scheduling |
| gender | CharField | MALE, FEMALE |
| unique_id | UUIDField | Unique identifier (auto-generated) |
| created_at | DateTimeField | Record creation timestamp |

### 3.3 VaccineSchedule
| Field | Type | Description |
|-------|------|-------------|
| child | ForeignKey | Links to Child |
| vaccine_name | CharField(150) | Vaccine identifier |
| due_date | DateField | Scheduled administration date |
| administered | BooleanField | Whether vaccine was given |
| administered_date | DateField | Actual administration date (nullable) |

**Constraints:** Unique together (child, vaccine_name)

### 3.4 SMSLog
| Field | Type | Description |
|-------|------|-------------|
| child | ForeignKey | Links to Child |
| message | TextField | SMS content |
| status | CharField | SENT, DELIVERED, FAILED |
| timestamp | DateTimeField | When SMS was logged |

### 3.5 RiskScore
| Field | Type | Description |
|-------|------|-------------|
| child | ForeignKey | Links to Child |
| score | IntegerField | 1=LOW, 2=MEDIUM, 3=HIGH |
| level | CharField | LOW, MEDIUM, HIGH |
| dropout_probability | FloatField | Probability (0-1) of missing next vaccine |
| calculated_at | DateTimeField | When score was computed |

---

## 4. Features & Functionalities

### 4.1 Vaccine Scheduling (scheduler.py)
- **Uganda EPI Schedule:** Implements the national immunization schedule
  - Birth (0 days): BCG, OPV0
  - 6 weeks (42 days): Penta1, OPV1, PCV1, Rota1
  - 10 weeks (70 days): Penta2, OPV2, PCV2, Rota2
  - 14 weeks (98 days): Penta3, OPV3, PCV3, IPV
  - 9 months (270 days): Measles
- **Auto-generation:** Uses `get_or_create()` to prevent duplicates
- **Trigger:** Automatically runs when a new Child is created (signal)

### 4.2 Overdue Detection (detection.py)
- **get_overdue_vaccines():** Returns VaccineSchedule records where due_date < today and administered=False
- **get_overdue_children():** Returns unique Child records with at least one overdue vaccine (reuses get_overdue_vaccines)

### 4.3 Risk Scoring (risk_engine.py)
- **Rules:** 0 overdue = LOW, 1 overdue = MEDIUM, 2+ overdue = HIGH
- **Optimization:** Only creates new RiskScore when level changes
- **Integration:** Includes dropout probability from prediction engine

### 4.4 Dropout Prediction (prediction_engine.py)
- **Formula:** `(0.4 × overdue_ratio) + (0.3 × delay_factor) + (0.3 × missed_factor)`
- **Factors:** overdue_ratio, days_since_last_due (capped at 30 days), missed_factor (binary)
- **Output:** Probability between 0 and 1

### 4.5 SMS Reminder Simulation (sms_service.py, reminder_engine.py)
- **send_sms(child, message):** Simulates SMS, creates SMSLog with random SENT/FAILED status
- **send_overdue_reminders():** Sends reminders to caregivers of children with overdue vaccines
- **Note:** No actual SMS sent; simulation only

### 4.6 Vaccine Administration (vaccine_service.py)
- **administer_vaccine(schedule):** Marks VaccineSchedule as administered, sets administered_date to today

### 4.7 Analysis Engine (analysis_engine.py)
- **run_analysis():** Orchestrates full system analysis
  1. Calculates risk for all children
  2. Sends overdue reminders
  3. Returns (children_processed, reminders_sent)

---

## 5. User Interfaces

### 5.1 Web Dashboard (/)
- **Metrics displayed:**
  - Average Dropout Probability
  - Total Children Registered
  - Vaccines Due This Week
  - Overdue Vaccines
  - High Risk Children
- **District Statistics Table:** Children, overdue vaccines, high-risk count per district
- **Run Immunization Analysis button:** Triggers analysis and redirects with success message

### 5.2 Run Analysis (/run-analysis/)
- Executes full analysis (risk calculation + reminders)
- Redirects to dashboard with success message

### 5.3 Django Admin (/admin/)
- **CaregiverAdmin:** list_display, search (name, phone, village), filter by language
- **ChildAdmin:** list_display, search, filter by gender, autocomplete for caregiver
- **VaccineScheduleAdmin:** list_display, filter by administered/vaccine
- **SMSLogAdmin:** list_display, filter by status
- **RiskScoreAdmin:** list_display, filter by level

---

## 6. Automation & Signals

### 6.1 Child Creation Signal (signals.py)
- **Trigger:** post_save on Child model when created=True
- **Action:** Calls generate_schedule(child) if child has no vaccines
- **Result:** New children automatically receive Uganda EPI vaccine schedule

---

## 7. Management Commands

### 7.1 analyze_immunization
```bash
python manage.py analyze_immunization
```
- Runs full analysis (risk + reminders)
- Outputs: Processed X children, Sent Y reminders

---

## 8. Service Dependencies

```
analysis_engine
├── risk_engine
│   ├── detection (get_overdue_vaccines)
│   └── prediction_engine (calculate_dropout_probability)
└── reminder_engine
    ├── detection (get_overdue_children)
    └── sms_service (send_sms)

scheduler → VaccineSchedule (get_or_create)
vaccine_service → VaccineSchedule (administer)
```

---

## 9. Configuration

### 9.1 Environment Variables (.env)
| Variable | Description | Default |
|----------|-------------|---------|
| DB_NAME | PostgreSQL database name | eims |
| DB_USER | Database user | — |
| DB_PASSWORD | Database password | — |
| DB_HOST | Database host | localhost |
| DB_PORT | Database port | 5432 |

### 9.2 Time Zone
- **Africa/Kampala** (Uganda)

---

## 10. Capabilities Summary

| Capability | Status |
|------------|--------|
| Caregiver & child registration | ✓ |
| Uganda EPI vaccine scheduling | ✓ |
| Auto-schedule on child creation | ✓ |
| Overdue vaccine detection | ✓ |
| Risk scoring (LOW/MEDIUM/HIGH) | ✓ |
| Dropout probability prediction | ✓ |
| SMS reminder simulation | ✓ |
| Vaccine administration recording | ✓ |
| Web dashboard with metrics | ✓ |
| District-level analytics | ✓ |
| One-click full analysis | ✓ |
| CLI analysis command | ✓ |
| Django admin for data management | ✓ |
| PostgreSQL backend | ✓ |
| Multi-language support (caregiver preference) | ✓ |

---

## 11. Limitations & Notes

1. **SMS:** Simulation only; no real SMS gateway integration
2. **District:** Requires manual population; empty districts show as "Unknown"
3. **Authentication:** Dashboard is unauthenticated; admin requires login
4. **Language:** Caregiver preference stored but not used for message translation

---

## 12. File Inventory

| Path | Purpose |
|------|---------|
| apps/immunization/models.py | Data models |
| apps/immunization/admin.py | Admin configuration |
| apps/immunization/signals.py | Child creation signal |
| apps/immunization/services/scheduler.py | Vaccine schedule generation |
| apps/immunization/services/detection.py | Overdue detection |
| apps/immunization/services/risk_engine.py | Risk scoring |
| apps/immunization/services/prediction_engine.py | Dropout probability |
| apps/immunization/services/sms_service.py | SMS simulation |
| apps/immunization/services/reminder_engine.py | Overdue reminders |
| apps/immunization/services/analysis_engine.py | Full analysis orchestration |
| apps/immunization/services/vaccine_service.py | Vaccine administration |
| apps/immunization/management/commands/analyze_immunization.py | CLI command |
| apps/core/views.py | Dashboard & run_analysis views |
| apps/core/urls.py | URL routing |
| apps/core/templates/core/dashboard.html | Dashboard template |
| config/settings.py | Django settings |
| config/urls.py | Root URL config |
| RUN.txt | Run instructions |

---

*End of Report*
