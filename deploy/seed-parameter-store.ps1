# =============================================================================
# Training Manager — Seed AWS SSM Parameter Store from a local .env (PowerShell).
#
#   pwsh deploy/seed-parameter-store.ps1 [-Yes] .\prod.env
#
# SAFETY (these scripts differ between repos ONLY by the prefix — running the
# wrong copy once clobbered a live site, so this refuses to write blind):
#   1. Prints the target PREFIX + a per-key plan (name + type) before writing.
#   2. Requires re-typing the prefix to confirm (skip with -Yes).
#   3. Auto-stores secret-looking keys as SecureString (regex safety net).
#
# Idempotent (--overwrite). To flip String<->SecureString: delete-parameter first.
# After seeding, on the box:
#   sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn
# =============================================================================
param([switch]$Yes, [Parameter(Mandatory = $true, Position = 0)][string]$EnvFile)

$ErrorActionPreference = "Stop"
$SsmPrefix = "/tm/prod"
$AwsRegion = "eu-west-1"
$SecretKeys = @("SECRET_KEY","DB_PASSWORD","GRAPH_CLIENT_SECRET","ANTHROPIC_API_KEY","TURNSTILE_SECRET_KEY","SENTRY_DSN")
$SecretRegex = "(SECRET|PASSWORD|_TOKEN|DSN|API_KEY|CLIENT_SECRET)"

if (-not (Test-Path $EnvFile)) { throw "No such file: $EnvFile" }

$plan = @()
foreach ($line in Get-Content $EnvFile) {
    $t = $line.Trim()
    if ($t -eq "" -or $t.StartsWith("#") -or ($t -notmatch "=")) { continue }
    $key = ($t -split "=", 2)[0].Trim()
    $val = ($t -split "=", 2)[1]
    $type = if (($SecretKeys -contains $key) -or ($key -match $SecretRegex)) { "SecureString" } else { "String" }
    $plan += [pscustomobject]@{ Key = $key; Value = $val; Type = $type }
}
if ($plan.Count -eq 0) { throw "No KEY=VALUE lines in $EnvFile." }

Write-Host ""
Write-Host "============================================================"
Write-Host "  SEED -> AWS SSM Parameter Store"
Write-Host "  PREFIX : $SsmPrefix        <-- writing here"
Write-Host "  Region : $AwsRegion"
Write-Host "  File   : $EnvFile  ($($plan.Count) keys)"
Write-Host "============================================================"
$plan | ForEach-Object { Write-Host ("  {0,-30} {1}" -f $_.Key, $_.Type) }
Write-Host "------------------------------------------------------------"
if ($plan | Where-Object { $_.Type -eq "SecureString" -and $_.Value -eq "" }) {
    Write-Host "  WARNING: one or more SecureString values are EMPTY."
}

if (-not $Yes) {
    $ans = Read-Host "Re-type the prefix EXACTLY to proceed (anything else aborts)"
    if ($ans -ne $SsmPrefix) { Write-Host "Aborted — typed '$ans', expected '$SsmPrefix'. Nothing written."; exit 1 }
}

foreach ($p in $plan) {
    aws ssm put-parameter --region $AwsRegion --name "$SsmPrefix/$($p.Key)" --type $p.Type --value $p.Value --overwrite | Out-Null
    Write-Host "  seeded $SsmPrefix/$($p.Key) ($($p.Type))"
}
Write-Host "Done. Seeded $SsmPrefix/* in $AwsRegion."
Write-Host "Apply on the box: sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn"
