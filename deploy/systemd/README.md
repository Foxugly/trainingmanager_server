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
