#!/usr/bin/env bash
# =============================================================================
# Training Manager — Seed AWS SSM Parameter Store from a local .env file.
#
# Source of truth for prod env is SSM (/tm/prod/*, eu-west-1), NOT a .env on the
# server. This pushes a local prod.env up to SSM. Run from your machine /
# CloudShell with an admin identity (ssm:PutParameter) — NOT the EC2 role.
#
#   bash deploy/seed-parameter-store.sh ./prod.env
#
# Idempotent (--overwrite). NOTE: --overwrite does NOT change a parameter's
# Type. To promote a String to SecureString: `aws ssm delete-parameter
# --name <name>` first, then re-seed.
#
# After seeding, apply on the box:
#   sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn
# =============================================================================
set -euo pipefail

ENV_FILE="${1:?Usage: $0 <path-to-.env>}"
SSM_PREFIX="/tm/prod"
AWS_REGION="eu-west-1"

# Keys whose values are secrets -> SecureString (KMS key aws/ssm). Everything
# else is a plain String.
SECRET_KEYS=" SECRET_KEY DB_PASSWORD GRAPH_CLIENT_SECRET ANTHROPIC_API_KEY TURNSTILE_SECRET_KEY "

[ -f "$ENV_FILE" ] || { echo "No such file: $ENV_FILE" >&2; exit 1; }

while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue

    key="${line%%=*}"; value="${line#*=}"
    key="${key//[[:space:]]/}"

    if [[ "$SECRET_KEYS" == *" $key "* ]]; then
        ptype="SecureString"
    else
        ptype="String"
    fi

    aws ssm put-parameter --region "$AWS_REGION" \
        --name "$SSM_PREFIX/$key" --type "$ptype" --value "$value" --overwrite >/dev/null
    echo "  seeded $SSM_PREFIX/$key ($ptype)"
done < "$ENV_FILE"

echo "Done. Remember: secrets ($SECRET_KEYS) must be SecureString."
