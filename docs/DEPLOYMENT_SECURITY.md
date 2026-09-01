# EIMS deployment security checklist

Every item here backs a claim made in Section VI of the paper. The application
enforces what it can; the rest is deployment configuration, and a claim in the
paper is only true once both halves are in place.

## Before first run

1. **Generate secrets.** Never reuse the development values.
   ```
   python -c "import secrets; print(secrets.token_urlsafe(64))"      # DJANGO_SECRET_KEY
   python -c "from apps.core.crypto import generate_key; print(generate_key())"   # EIMS_FIELD_KEY
   ```
   Put them in `.env`. `.env` is gitignored and must never be committed.

2. **Turn off debug.** `DJANGO_DEBUG=false`. With debug off the app refuses to
   start unless `DJANGO_SECRET_KEY` is set, which is intentional.

3. **Set hosts.** `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` to
   the real hostname. TLS redirect, HSTS, and secure cookies switch on
   automatically once debug is off.

4. **Encrypt legacy rows.** If the database predates field encryption:
   ```
   python manage.py encrypt_existing_pii --dry-run
   python manage.py encrypt_existing_pii
   ```
   Re-run the dry run afterwards; it must report zero rows needing rewrite.

## Database roles

The append-only audit log is enforced in the application, which refuses to
update or delete a row. That is half the control. The other half is a database
role for the application that has no `UPDATE` or `DELETE` on the audit table,
so a bug or an injected statement cannot rewrite history either:

```sql
REVOKE UPDATE, DELETE ON core_auditlog FROM eims_app;
GRANT INSERT, SELECT ON core_auditlog TO eims_app;
```

## Key management

`EIMS_FIELD_KEY` decrypts every identifying field. Treat it as the most
sensitive value in the deployment.

- It must not live in the same backup as the database. A backup containing both
  is equivalent to an unencrypted backup.
- On facility devices it is held in the process environment, so an attacker with
  a running, unlocked machine can recover it. This is the residual risk the
  paper states rather than claims to have solved. Full-disk encryption and
  short screen-lock timeouts are the practical mitigations available today.
- Rotation requires re-encrypting every row. Keep the old key until the rewrite
  completes.

## Caregiver links

PDF cards and `.ics` schedules are served against a signed token that names the
child, names the artifact type, and expires (`EIMS_ARTIFACT_LINK_MAX_AGE`,
14 days by default). Shorten it if caregivers are expected to act sooner. Do not
remove the signature requirement to "make sharing easier": a bare UUID in a URL
is a permanent unauthenticated handle on a named child's clinical record.

## Operational review

The audit log records access but nothing reads it automatically. Before a real
deployment, decide who reviews it and how often. A log nobody reads is a control
in name only, and the paper says so.

## Verifying the claims

```
python manage.py test apps.core.tests.test_security
```

Seventeen tests, each corresponding to a sentence in Section VI. If one fails,
the paper is making a claim the system no longer honours.
