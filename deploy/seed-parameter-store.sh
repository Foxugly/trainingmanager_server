#!/usr/bin/env bash
# =============================================================================
# Training Manager — Seed AWS SSM Parameter Store from a local .env file.
#
# Source of truth for prod env is SSM (/tm/prod/*, eu-west-1), NOT a .env on the
# server. Run with an admin identity (ssm:PutParameter) — NOT the EC2 role.
#
#   bash deploy/seed-parameter-store.sh [--yes] ./prod.env
#
# SAFETY (these scripts differ between repos ONLY by SSM_PREFIX — running the
# wrong copy once clobbered a live site's params, so this one refuses to write
# blind):
#   1. Prints the target PREFIX + a per-key plan (name + type) BEFORE writing.
#   2. Requires you to re-type the prefix to confirm (skip with --yes).
#   3. Auto-stores anything secret-looking as SecureString (regex safety net),
#      so a secret is never silently pushed as plaintext String.
#
# Idempotent (--overwrite). NOTE: --overwrite does NOT change an existing
# parameter's Type. To flip String<->SecureString: delete-parameter first.
# After seeding, on the box:
#   sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn
# =============================================================================
set -euo pipefail

SSM_PREFIX="/tm/prod"
AWS_REGION="eu-west-1"

# Explicit secrets + a safety-net regex: a key is stored as SecureString if it
# is in this list OR matches the regex. TURNSTILE_SITE_KEY stays String (public
# widget key) because the regex matches API_KEY/SECRET/…, not a bare "KEY".
SECRET_KEYS=" SECRET_KEY DB_PASSWORD GRAPH_CLIENT_SECRET ANTHROPIC_API_KEY TURNSTILE_SECRET_KEY SENTRY_DSN "
SECRET_REGEX='(SECRET|PASSWORD|_TOKEN|DSN|API_KEY|CLIENT_SECRET)'

ASSUME_YES=0
ENV_FILE=""
for a in "$@"; do
    case "$a" in
        -y|--yes) ASSUME_YES=1 ;;
        -*) echo "Unknown option: $a" >&2; exit 2 ;;
        *)  ENV_FILE="$a" ;;
    esac
done
[ -n "$ENV_FILE" ] || { echo "Usage: $0 [--yes] <path-to-.env>" >&2; exit 1; }
[ -f "$ENV_FILE" ] || { echo "No such file: $ENV_FILE" >&2; exit 1; }

is_secret() {  # $1 = key name
    case "$SECRET_KEYS" in *" $1 "*) return 0 ;; esac
    printf '%s' "$1" | grep -Eq "$SECRET_REGEX"
}

# --- Build the plan (parse only, no writes) ---------------------------------
declare -a KEYS VALS TYPES
empty_secret=0
while IFS= read -r line || [ -n "$line" ]; do
    case "${line//[[:space:]]/}" in ''|\#*) continue ;; esac
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"; key="${key//[[:space:]]/}"
    val="${line#*=}"
    if is_secret "$key"; then t="SecureString"; else t="String"; fi
    [ "$t" = "SecureString" ] && [ -z "$val" ] && empty_secret=1
    KEYS+=("$key"); VALS+=("$val"); TYPES+=("$t")
done < "$ENV_FILE"
[ "${#KEYS[@]}" -gt 0 ] || { echo "No KEY=VALUE lines in $ENV_FILE." >&2; exit 1; }

# --- Banner + per-key preview (values never printed) ------------------------
echo
echo "============================================================"
echo "  SEED → AWS SSM Parameter Store"
echo "  PREFIX : $SSM_PREFIX        <-- writing here"
echo "  Region : $AWS_REGION"
echo "  File   : $ENV_FILE   (${#KEYS[@]} keys)"
echo "============================================================"
for i in "${!KEYS[@]}"; do printf "  %-30s %s\n" "${KEYS[$i]}" "${TYPES[$i]}"; done
echo "------------------------------------------------------------"
[ "$empty_secret" = 1 ] && echo "  WARNING: one or more SecureString values are EMPTY."

# --- Confirmation: re-type the prefix (catches wrong script / wrong dir) -----
if [ "$ASSUME_YES" != 1 ]; then
    printf 'Re-type the prefix EXACTLY to proceed (Ctrl-C / anything else aborts):\n> '
    read -r ans
    if [ "$ans" != "$SSM_PREFIX" ]; then
        echo "Aborted — typed '$ans', expected '$SSM_PREFIX'. Nothing written."
        exit 1
    fi
fi

# --- Write ------------------------------------------------------------------
for i in "${!KEYS[@]}"; do
    aws ssm put-parameter --region "$AWS_REGION" \
        --name "$SSM_PREFIX/${KEYS[$i]}" --type "${TYPES[$i]}" \
        --value "${VALS[$i]}" --overwrite >/dev/null
    echo "  seeded $SSM_PREFIX/${KEYS[$i]} (${TYPES[$i]})"
done
echo "Done. Seeded $SSM_PREFIX/* in $AWS_REGION."
echo "Apply on the box: sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn"
