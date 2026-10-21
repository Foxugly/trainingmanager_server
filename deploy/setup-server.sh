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
#     role foxugly-fleet-ec2 granted ssm:GetParametersByPath on /tm/prod(/*)
#   - the §3.10 root-only fetch script installed (see "=== 5/8" note)
#
# ⚠️ §3.10: run THIS script from a checkout django cannot write — never from
#    the django-owned app tree (the script runs with ubuntu's sudo). E.g.:
#      D=$(mktemp -d) && git clone --depth=1 https://github.com/Foxugly/trainingmanager_server.git "$D" \
#        && bash "$D/deploy/setup-server.sh"
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
# Existence tests run as ROOT (sudo test): the unprivileged shell can't traverse
# the 750 django tree, so a plain `[ -d $APP_DIR/.git ]` would wrongly report
# "missing" and retry the clone over an existing dir.
if sudo test -d "$APP_DIR/.git"; then
    sudo -u "$APP_USER" git -C "$APP_DIR" fetch origin main
    sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard origin/main
else
    sudo -u "$APP_USER" git clone "$REPO" "$APP_DIR"
fi

echo "=== 4/8 Python venv + dependencies ==="
sudo test -d "$APP_DIR/.venv" || sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "=== 5/8 Install root-only env-fetch script + systemd units + sudoers ==="
# §3.10 / §3.11: every file root installs is read from ROOT'S OWN bare mirror
# (/var/lib/fleet-deploy/trainingmanager_server.git, root:root 700) fetched from GitHub —
# never from $APP_DIR or its .git, which django owns and could rewrite before
# root copies them (django -> root escalation).
MIRROR=/var/lib/fleet-deploy/trainingmanager_server.git
sudo install -d -m 700 -o root -g root /var/lib/fleet-deploy
sudo test -d "$MIRROR" || sudo git init --quiet --bare "$MIRROR"
sudo git --git-dir="$MIRROR" fetch --quiet --depth=1 "https://github.com/Foxugly/trainingmanager_server.git" main
SHA=$(sudo git --git-dir="$MIRROR" rev-parse FETCH_HEAD)
root_install() {  # <mode> <path in repo> <destination>
    sudo git --git-dir="$MIRROR" show "$SHA:$2" | sudo install -o root -g root -m "$1" /dev/stdin "$3"
}

# §3.10: the env-fetch script runs as ROOT, so it must live OUTSIDE the
# django-writable tree. Install it root:root 0755 from the committed git blob.
root_install 0755 deploy/fetch-env-from-ssm.sh /usr/local/sbin/tm-env-fetch.sh

root_install 0644 deploy/systemd/tm-env-fetch.service /etc/systemd/system/tm-env-fetch.service
root_install 0644 deploy/systemd/tm-gunicorn.service /etc/systemd/system/tm-gunicorn.service
sudo systemctl daemon-reload

# sudoers (validate before install so a typo can't break sudo for everyone).
SUDOERS_TMP=$(sudo mktemp)
sudo git --git-dir="$MIRROR" show "$SHA:deploy/sudoers/tm-deploy" | sudo tee "$SUDOERS_TMP" > /dev/null
sudo visudo -c -f "$SUDOERS_TMP"
sudo install -o root -g root -m 0440 "$SUDOERS_TMP" /etc/sudoers.d/tm-deploy
sudo rm -f "$SUDOERS_TMP"

echo "=== 6/8 Fetch environment from AWS SSM ==="
sudo systemctl enable tm-env-fetch
if ! sudo systemctl start tm-env-fetch; then
    echo "ERROR: tm-env-fetch failed — is SSM /tm/prod seeded and the instance" >&2
    echo "       role allowed to read it?  journalctl -u tm-env-fetch" >&2
    exit 1
fi

echo "=== 7/8 First deploy (migrate + collectstatic + start gunicorn) ==="
# Reuse deploy.sh — it loads /run/tm/.env with LITERAL key=value parsing (never
# `source`: SECRET_KEY & co. contain shell-special chars like ()$# that `.`
# would choke on, §3.11), runs migrate + collectstatic, normalises perms, and
# (re)starts tm-gunicorn via the sudoers grant. Enable first so it survives boot.
sudo systemctl enable tm-gunicorn
sudo -u "$APP_USER" bash "$APP_DIR/deploy/deploy.sh"

echo "=== 8/8 nginx vhost (wildcard TLS, no certbot) ==="
# TLS = existing *.foxugly.com wildcard cert (referenced in tm-api.conf). No
# per-domain certbot — the vhost already has its 443 block.
root_install 0644 deploy/nginx/tm-limits.conf /etc/nginx/conf.d/tm-limits.conf
root_install 0644 deploy/nginx/tm-api.conf /etc/nginx/sites-available/tm-api.conf
sudo ln -sf /etc/nginx/sites-available/tm-api.conf /etc/nginx/sites-enabled/tm-api.conf
sudo nginx -t
sudo systemctl reload nginx

echo ""
echo "=== Setup complete ==="
echo "  API:      https://$DOMAIN"
echo "  Docs:     https://$DOMAIN/api/v1/schema/swagger-ui/"
echo "  Health:   https://$DOMAIN/api/v1/health/"
echo "  Logs:     journalctl -u tm-gunicorn -f"
echo "            journalctl -u tm-env-fetch"
echo "            tail -f /var/log/tm/gunicorn-error.log"
