# =============================================================================
# Training Manager — Seed AWS SSM Parameter Store from a local .env (PowerShell).
#
# Source of truth for prod env is SSM (/tm/prod/*, eu-west-1), NOT a .env on the
# server. Run with an admin identity (ssm:PutParameter) — NOT the EC2 role.
#
#   pwsh deploy/seed-parameter-store.ps1 .\prod.env
#
# Idempotent (--overwrite). NOTE: --overwrite does NOT change a parameter's
# Type. To promote String -> SecureString: delete-parameter first, then re-seed.
# After seeding, on the box:
#   sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn
# =============================================================================
param([Parameter(Mandatory=$true)][string]$EnvFile)

$ErrorActionPreference = "Stop"
$SsmPrefix = "/tm/prod"
$AwsRegion = "eu-west-1"

# Keys whose values are secrets -> SecureString (KMS key aws/ssm).
$SecretKeys = @("SECRET_KEY","DB_PASSWORD","GRAPH_CLIENT_SECRET","ANTHROPIC_API_KEY","TURNSTILE_SECRET_KEY")

if (-not (Test-Path $EnvFile)) { throw "No such file: $EnvFile" }

foreach ($line in Get-Content $EnvFile) {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#") -or ($t -notmatch "=")) { continue }
    $key = ($t -split "=", 2)[0].Trim()
    $value = ($t -split "=", 2)[1]
    $ptype = if ($SecretKeys -contains $key) { "SecureString" } else { "String" }
    aws ssm put-parameter --region $AwsRegion --name "$SsmPrefix/$key" `
        --type $ptype --value "$value" --overwrite | Out-Null
    Write-Host "  seeded $SsmPrefix/$key ($ptype)"
}
Write-Host "Done. Secrets stored as SecureString: $($SecretKeys -join ', ')"
