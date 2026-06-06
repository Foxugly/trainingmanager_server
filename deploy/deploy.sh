#!/usr/bin/env bash
# =============================================================================
# Training Manager API — Deployment script
#
# Run on the box as 'django' (by GitHub Actions via OIDC→SSM, or manually):
#   /var/www/django_websites/trainingmanager_server/deploy/deploy.sh
# =============================================================================
set -euo pipefail
umask 027   # new dirs 750 / files 640 from git/pip/collectstatic (OPERATIONS.md §3.1/§3.2)

APP_DIR="/var/www/django_websites/trainingmanager_server"
VENV="$APP_DIR/.venv"
export DJANGO_SETTINGS_MODULE="django-trainingmanager.settings.prod"

cd "$APP_DIR"

echo ">>> Pulling latest code..."
git fetch origin main
git reset --hard origin/main

echo ">>> Installing dependencies..."
"$VENV/bin/pip" install --quiet -r requirements.txt

# Load the SSM-fetched env so manage.py has SECRET_KEY, DB_*, etc. systemd
# injects this for the service via EnvironmentFile, but a manual command does
# not — source it here. tm-env-fetch.service writes it at boot from /tm/prod/*.
ENV_FILE="/run/tm/.env"
if [ -f "$ENV_FILE" ]; then
    echo ">>> Loading env from $ENV_FILE..."
    # Parse literally (key=value), NOT `source`: this is a systemd
    # EnvironmentFile, and values (e.g. SECRET_KEY) may contain shell-special
    # chars ($ ` ( ) …) that `.` would expand/mangle — which silently empties
    # SECRET_KEY and breaks `migrate`. Mirrors systemd's own parsing.
    while IFS='=' read -r _k _v || [ -n "$_k" ]; do
        case "$_k" in ''|\#*) continue ;; esac
        export "$_k=$_v"
    done < "$ENV_FILE"
    unset _k _v
else
    echo "WARNING: $ENV_FILE missing — has tm-env-fetch run? Trying without it." >&2
fi

echo ">>> Running migrations..."
"$VENV/bin/python" manage.py migrate --noinput

echo ">>> Compiling translation catalogs (.po -> .mo)..."
# .mo files are gitignored (binary build artifacts); compile them on each
# deploy so backend gettext strings (error messages, audit action labels,
# emails, model verbose names) localize to fr/nl/it/es. Requires msgfmt
# (gettext) on the box. Non-fatal: a missing toolchain must not abort a
# deploy — the app simply falls back to the English msgids.
"$VENV/bin/python" manage.py compilemessages || echo "WARN: compilemessages failed; continuing with English fallback."

echo ">>> Collecting static files..."
"$VENV/bin/python" manage.py collectstatic --noinput

echo ">>> Normalizing permissions (idempotent: dirs 750 / files 640, no o-rwx, no g-w)..."
# deploy.sh runs as django, which OWNS the whole tree (primary group www-data),
# so this needs NO sudo. chown first (fixes any group drift), then chmod (drop
# group-write + all "other"). Owner bits untouched -> execute preserved.
chown -R django:www-data "$APP_DIR"
chmod -R g-w,o-rwx "$APP_DIR"

# NOTE: tm-env-fetch is intentionally NOT restarted here — a code deploy keeps
# the env already in /run/tm/.env. To pick up changed SSM values:
#   sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn
echo ">>> Restarting service..."
sudo /bin/systemctl restart tm-gunicorn

echo ">>> Deploy complete."
