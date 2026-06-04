#!/usr/bin/env bash
# =============================================================================
# Training Manager — Fetch environment from AWS SSM Parameter Store into tmpfs.
#
# Run as root by tm-env-fetch.service (oneshot) at boot, BEFORE gunicorn starts.
# The written file lives in /run (tmpfs): it never touches disk, never lands in
# an EBS snapshot, and is re-fetched each boot.
#
# Source of truth = SSM /tm/prod/* (region eu-west-1), read with the EC2
# instance role via IMDS — no AWS keys on disk. (The unit blanks
# AWS_SHARED_CREDENTIALS_FILE/AWS_CONFIG_FILE so the role is used, not certbot's.)
#
# Guard-rails (so the box never serves traffic with a broken config):
#   - aws failure stops the script before touching $ENV_FILE (last valid kept);
#   - refuses to write an empty result;
#   - rejects any value containing a newline (would corrupt the EnvironmentFile);
#   - writes atomically (.env.tmp -> mv) with mode 640, owner django:www-data;
#   - exits non-zero on any failure -> tm-gunicorn (Requires=) won't start.
# =============================================================================
set -euo pipefail
umask 077   # temp files (which briefly hold decrypted secrets) are root-only.

SSM_PREFIX="/tm/prod"
AWS_REGION="eu-west-1"
RUN_DIR="/run/tm"
ENV_FILE="$RUN_DIR/.env"
TMP_FILE="$RUN_DIR/.env.tmp"
RAW_FILE="$RUN_DIR/.ssm.json"
OWNER="django:www-data"

mkdir -p "$RUN_DIR"
# Dir must be traversable by the django service user (the .env itself stays 640,
# so its contents remain protected). 750 root:www-data — owner root writes it;
# the www-data group (django is a member) gets r-x to traverse; "other" nothing.
chmod 750 "$RUN_DIR"
chown root:www-data "$RUN_DIR"

# Fetch raw JSON to a file first. If aws errors (IAM/IMDS/network), we stop here
# (set -e) and the previous $ENV_FILE is left untouched.
aws ssm get-parameters-by-path \
    --path "$SSM_PREFIX" \
    --recursive \
    --with-decryption \
    --region "$AWS_REGION" \
    --output json > "$RAW_FILE"

# Parse JSON -> KEY=VALUE. The program is read from the heredoc (python3 -),
# and the data is read from the file passed as an argument — so there is no
# clash between "program on stdin" and "data on stdin".
python3 - "$SSM_PREFIX" "$TMP_FILE" "$RAW_FILE" <<'PY'
import json, sys

prefix, tmp_path, raw_path = sys.argv[1], sys.argv[2], sys.argv[3]
with open(raw_path) as fh:
    params = json.load(fh).get("Parameters", [])

if not params:
    sys.stderr.write(f"ERROR: no parameters under {prefix}; refusing to write an empty env.\n")
    sys.exit(1)

lines = []
for p in params:
    key = p["Name"][len(prefix):].lstrip("/")
    # Tolerate a trailing CR/LF (artifact of a Windows/CRLF-edited seed file)
    # but still reject any *internal* newline, which would corrupt the file.
    value = p["Value"].strip("\r\n")
    if "\n" in value or "\r" in value:
        sys.stderr.write(f"ERROR: value for {key} contains an internal newline; refusing.\n")
        sys.exit(1)
    lines.append(f"{key}={value}")

with open(tmp_path, "w") as fh:
    fh.write("\n".join(sorted(lines)) + "\n")
PY

rm -f "$RAW_FILE"

# Belt-and-braces: never promote an empty file.
if [ ! -s "$TMP_FILE" ]; then
    echo "ERROR: assembled env file is empty; keeping previous $ENV_FILE." >&2
    rm -f "$TMP_FILE"
    exit 1
fi

chmod 640 "$TMP_FILE"
chown "$OWNER" "$TMP_FILE"
mv -f "$TMP_FILE" "$ENV_FILE"

echo "Wrote $(wc -l < "$ENV_FILE") variables to $ENV_FILE."
