#!/usr/bin/env bash
# =============================================================================
# Training Manager API — first-time server setup for Ubuntu 24.04 EC2.
#
# Cohabits with the other Foxugly sites under /var/www/django_websites/.
# Env vars come from AWS SSM (/tm/prod/*, eu-west-1), NOT a .env on disk.
#
# Run as 'ubuntu' (needs sudo), AFTER:
#   - DNS A record  tm-api.foxugly.com → EC2 public IP
#   - SSM /tm/prod/* seeded (deploy/seed-parameter-store.sh) and the instance
#     role quizonline-ec2 granted ssm:GetParametersByPath on /tm/prod(/*)
#   - the §3.10 root-only fetch script installed (see "=== 5/8" note)
#
#   bash /var/www/django_websites/trainingmanager_server/deploy/setup-server.sh
# =============================================================================
set -euo pipefail
umask 027

APP_DIR="/var/www/django_websites/trainingmanager_server"
APP_USER="django"
APP_GROUP="www-data"
DOMAIN="tm-api.foxugly.com"
REPO="https://github.com/Foxugly/trainingmanager_server.git"
EMAIL="rvilain@foxugly.com"

echo "=== 1/8 Verify base infrastructure ==="
id "$APP_USER" &>/dev/null || sudo useradd --system --create-home --shell /bin/bash --gid "$APP_GROUP" "$APP_USER"
MISSING=()
for pkg in nginx certbot python3-certbot-nginx git awscli postgresql-client; do
    dpkg -l "$pkg" &>/dev/null || MISSING+=("$pkg")
done
[ ${#MISSING[@]} -gt 0 ] && { sudo apt update; sudo apt install -y "${MISSING[@]}"; } || echo "packages OK"

echo "=== 2/8 App directory + log dir ==="
sudo mkdir -p "$APP_DIR" /var/log/tm
sudo chown "$APP_USER":"$APP_GROUP" "$APP_DIR" /var/log/tm

echo "=== 3/8 Clone repository ==="
if [ ! -d "$APP_DIR/.git" ]; then
    sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
else
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin main
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard origin/main
fi

echo "=== 4/8 Python venv + dependencies ==="
[ -d "$APP_DIR/.venv" ] || sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "=== 5/8 Install root-only env-fetch script + systemd units + sudoers ==="
# §3.10: the env-fetch script runs as ROOT, so it must live OUTSIDE the
# django-writable tree. Install it root:root 0755 from the committed git blob.
sudo install -o root -g root -m 0755 "$APP_DIR/deploy/fetch-env-from-ssm.sh" /usr/local/sbin/tm-env-fetch.sh

sudo cp "$APP_DIR/deploy/systemd/"*.service /etc/systemd/system/
sudo systemctl daemon-reload

# sudoers (validate before install so a typo can't break sudo for everyone).
sudo install -o root -g root -m 0440 "$APP_DIR/deploy/sudoers/tm-deploy" /etc/sudoers.d/tm-deploy
sudo visudo -c -f /etc/sudoers.d/tm-deploy

echo "=== 6/8 Fetch environment from AWS SSM ==="
sudo systemctl enable tm-env-fetch
if ! sudo systemctl start tm-env-fetch; then
    echo "ERROR: tm-env-fetch failed — is SSM /tm/prod seeded and the instance" >&2
    echo "       role allowed to read it?  journalctl -u tm-env-fetch" >&2
    exit 1
fi

echo "=== 7/8 Initial migrate + collectstatic ==="
sudo -u "$APP_USER" bash -c "set -a; . /run/tm/.env; set +a; \
    export DJANGO_SETTINGS_MODULE=django-trainingmanager.settings.prod; \
    '$APP_DIR/.venv/bin/python' '$APP_DIR/manage.py' migrate --noinput && \
    '$APP_DIR/.venv/bin/python' '$APP_DIR/manage.py' collectstatic --noinput"
sudo chown -R "$APP_USER":"$APP_GROUP" "$APP_DIR"
sudo chmod -R g-w,o-rwx "$APP_DIR"

echo "=== 8/8 nginx vhost + TLS + start service ==="
sudo cp "$APP_DIR/deploy/nginx/tm-api.conf" /etc/nginx/sites-available/tm-api.conf
sudo ln -sf /etc/nginx/sites-available/tm-api.conf /etc/nginx/sites-enabled/tm-api.conf
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL"
sudo systemctl enable --now tm-gunicorn

echo ""
echo "=== Setup complete ==="
echo "  API:      https://$DOMAIN"
echo "  Docs:     https://$DOMAIN/api/v1/schema/swagger-ui/"
echo "  Health:   https://$DOMAIN/api/v1/health/"
echo "  Logs:     journalctl -u tm-gunicorn -f"
echo "            journalctl -u tm-env-fetch"
echo "            tail -f /var/log/tm/gunicorn-error.log"
