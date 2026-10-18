# systemd units (Training Manager)

Versioned source for the app's systemd units. Install **as root, out-of-band**
(units/timers are root-managed; never symlink them from this django-writable
tree into `/etc/systemd/system`, copy them).

| File | Role |
|---|---|
| `tm-env-fetch.service` | oneshot: fetch SSM secrets into `/run/tm/.env` |
| `tm-gunicorn.service` | the API (gunicorn), `User=django` |
| `tm-weekly-recap.service` | oneshot: `manage.py send_weekly_recaps`, `User=django` |
| `tm-weekly-recap.timer` | runs the recap service Monday 07:00 (`Persistent=true`) |
| `tm-session-reminder.service` | oneshot: `manage.py send_session_reminders`, `User=django` |
| `tm-session-reminder.timer` | runs the reminder service daily 07:00 (`Persistent=true`); notifies athletes of tomorrow's sessions |
| `tm-audit-purge.service` | oneshot: `manage.py purge_audit_log`, `User=django` |
| `tm-audit-purge.timer` | runs the purge service Sunday 03:30 (`Persistent=true`); deletes audit rows older than the retention window |
| `tm-digest.service` | oneshot: `manage.py send_digests`, `User=django` |
| `tm-digest.timer` | runs the digest service daily 07:30 (`Persistent=true`); one summary of the prior day's notifications for digest users |
| `tm-attachment-purge.service` | oneshot: `manage.py purge_pending_attachments`, `User=django` |
| `tm-attachment-purge.timer` | runs the purge daily 04:00 (`Persistent=true`); deletes `pending` uploads older than 24 h (row + S3 object) |

The deploy pipeline does **not** install units (restart-only model,
`OPERATIONS.md §3.11`): every unit added here must be installed on the box
by root, out-of-band, with the commands below. Check with
`systemctl list-timers 'tm-*'` that each `*.timer` in this directory is listed.

## Installing the weekly recap (root, on the box)

```bash
sudo install -o root -g root -m 0644 \
  /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-weekly-recap.service \
  /etc/systemd/system/tm-weekly-recap.service
sudo install -o root -g root -m 0644 \
  /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-weekly-recap.timer \
  /etc/systemd/system/tm-weekly-recap.timer

sudo systemctl daemon-reload
sudo systemctl enable --now tm-weekly-recap.timer

# Verify the schedule and do a safe manual dry-run:
systemctl list-timers tm-weekly-recap.timer
sudo systemctl start tm-weekly-recap.service   # real run, on demand
# Dry-run (no email sent) as the django user:
sudo -u django env DJANGO_SETTINGS_MODULE=django-trainingmanager.settings.prod \
  /var/www/django_websites/trainingmanager_server/.venv/bin/python \
  /var/www/django_websites/trainingmanager_server/manage.py send_weekly_recaps --dry-run
```

The recap service `ExecStart` is:

```
/var/www/django_websites/trainingmanager_server/.venv/bin/python \
  /var/www/django_websites/trainingmanager_server/manage.py send_weekly_recaps
```

It runs as `User=django`, reads `/run/tm/.env` via `EnvironmentFile=`, with
`DJANGO_SETTINGS_MODULE=django-trainingmanager.settings.prod` and `UMask=0027`,
mirroring `tm-gunicorn.service`.

## Installing the daily session reminder (root, on the box)

Same pattern as the weekly recap. The reminder notifies athletes of sessions
happening **tomorrow**, daily at 07:00.

```bash
sudo install -o root -g root -m 0644 \
  /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-session-reminder.service \
  /etc/systemd/system/tm-session-reminder.service
sudo install -o root -g root -m 0644 \
  /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-session-reminder.timer \
  /etc/systemd/system/tm-session-reminder.timer

sudo systemctl daemon-reload
sudo systemctl enable --now tm-session-reminder.timer

# Verify the schedule and do a safe manual dry-run:
systemctl list-timers tm-session-reminder.timer
sudo -u django env DJANGO_SETTINGS_MODULE=django-trainingmanager.settings.prod \
  /var/www/django_websites/trainingmanager_server/.venv/bin/python \
  /var/www/django_websites/trainingmanager_server/manage.py send_session_reminders --dry-run
```

It runs as `User=django` with the same `EnvironmentFile=` / settings / `UMask=0027`
as the recap service.

## Installing the audit-log retention purge (root, on the box)

Same pattern. Deletes `audit.AuditLogEntry` rows older than the retention window
(`--days`, default 365), batched and idempotent — caps storage and the lifetime
of any PII-adjacent `target_repr` snapshots.

```bash
sudo install -o root -g root -m 0644 \
  /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-audit-purge.service \
  /etc/systemd/system/tm-audit-purge.service
sudo install -o root -g root -m 0644 \
  /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-audit-purge.timer \
  /etc/systemd/system/tm-audit-purge.timer

sudo systemctl daemon-reload
sudo systemctl enable --now tm-audit-purge.timer

# Verify the schedule and do a safe dry-run (counts, deletes nothing):
systemctl list-timers tm-audit-purge.timer
sudo -u django env DJANGO_SETTINGS_MODULE=django-trainingmanager.settings.prod \
  /var/www/django_websites/trainingmanager_server/.venv/bin/python \
  /var/www/django_websites/trainingmanager_server/manage.py purge_audit_log --dry-run
```

Tune retention with `--days N` in the service `ExecStart`, or set
`AUDIT_RETENTION_DAYS` in SSM (`/run/tm/.env`) and let the command's default
pick it up.

## Installing the daily notification digest (root, on the box)

Same pattern. Sends one summary of the prior day's notifications to users who
opted for the digest, daily at 07:30.

```bash
sudo install -o root -g root -m 0644   /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-digest.service   /etc/systemd/system/tm-digest.service
sudo install -o root -g root -m 0644   /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-digest.timer   /etc/systemd/system/tm-digest.timer

sudo systemctl daemon-reload
sudo systemctl enable --now tm-digest.timer
systemctl list-timers tm-digest.timer
```

## Installing the abandoned-upload purge (root, on the box)

Same pattern. Deletes attachment rows still `pending` 24 h after their presign
(the upload was never completed) and their S3 object, daily at 04:00.

```bash
sudo install -o root -g root -m 0644   /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-attachment-purge.service   /etc/systemd/system/tm-attachment-purge.service
sudo install -o root -g root -m 0644   /var/www/django_websites/trainingmanager_server/deploy/systemd/tm-attachment-purge.timer   /etc/systemd/system/tm-attachment-purge.timer

sudo systemctl daemon-reload
sudo systemctl enable --now tm-attachment-purge.timer
systemctl list-timers tm-attachment-purge.timer
sudo -u django env DJANGO_SETTINGS_MODULE=django-trainingmanager.settings.prod   /var/www/django_websites/trainingmanager_server/.venv/bin/python   /var/www/django_websites/trainingmanager_server/manage.py purge_pending_attachments --dry-run
```
