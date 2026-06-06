#!/usr/bin/env bash
# =============================================================================
# Training Manager — one-time AWS provisioning for #6 file attachments (S3).
#
# Creates the private attachments bucket, locks it down, sets browser CORS for
# presigned PUT/GET, grants the EC2 instance role least-privilege object access,
# and publishes the bucket name to SSM so tm-env-fetch picks it up.
#
# RUN THIS OFF-BOX (from an admin workstation) with AWS *admin* credentials —
# NOT on the EC2 (the box's instance role can't do IAM; §3.10). It is
# idempotent: safe to re-run.
#
#   AWS_PROFILE=<admin> bash deploy/create-attachments-infra.sh
#
# After it succeeds, on the EC2:  sudo systemctl restart tm-env-fetch
# (then redeploy / restart tm-gunicorn to read ATTACHMENTS_S3_BUCKET).
# =============================================================================
set -euo pipefail

REGION="eu-west-1"
BUCKET="foxugly-tm-attachments"
ROLE="foxugly-fleet-ec2"            # EC2 instance role (was quizonline-ec2)
POLICY_NAME="tm-attachments-s3"
SSM_PARAM="/tm/prod/ATTACHMENTS_S3_BUCKET"
# Browser origins that perform presigned PUT/GET directly against S3.
CORS_ORIGINS='["https://tm.foxugly.com","http://localhost:4200"]'

echo "==> Account / identity check"
aws sts get-caller-identity --output table

echo "==> 1/7 Create bucket ${BUCKET} (${REGION}) if absent"
if aws s3api head-bucket --bucket "${BUCKET}" 2>/dev/null; then
  echo "    bucket already exists — skipping create"
else
  aws s3api create-bucket \
    --bucket "${BUCKET}" \
    --region "${REGION}" \
    --create-bucket-configuration "LocationConstraint=${REGION}"
fi

echo "==> 2/7 Block ALL public access"
aws s3api put-public-access-block \
  --bucket "${BUCKET}" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "==> 3/7 Default server-side encryption (SSE-S3 / AES256)"
aws s3api put-bucket-encryption \
  --bucket "${BUCKET}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}'

echo "==> 4/7 Lifecycle: abort incomplete multipart uploads after 7 days"
aws s3api put-bucket-lifecycle-configuration \
  --bucket "${BUCKET}" \
  --lifecycle-configuration '{"Rules":[{"ID":"abort-incomplete-mpu","Status":"Enabled","Filter":{"Prefix":""},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'

echo "==> 5/7 CORS for browser presigned PUT/GET"
aws s3api put-bucket-cors \
  --bucket "${BUCKET}" \
  --cors-configuration "{
    \"CORSRules\": [{
      \"AllowedOrigins\": ${CORS_ORIGINS},
      \"AllowedMethods\": [\"PUT\",\"GET\",\"HEAD\"],
      \"AllowedHeaders\": [\"*\"],
      \"ExposeHeaders\": [\"ETag\"],
      \"MaxAgeSeconds\": 3000
    }]
  }"

echo "==> 6/7 Least-priv inline policy ${POLICY_NAME} on role ${ROLE}"
if ! aws iam get-role --role-name "${ROLE}" >/dev/null 2>&1; then
  echo "ERROR: IAM role ${ROLE} not found. Edit ROLE= to the box's actual"
  echo "       instance role and re-run. (Find it: aws ec2 describe-instances"
  echo "       --filters Name=tag:Name,Values=* --query"
  echo "       'Reservations[].Instances[].IamInstanceProfile.Arn')"
  exit 1
fi
aws iam put-role-policy \
  --role-name "${ROLE}" \
  --policy-name "${POLICY_NAME}" \
  --policy-document "{
    \"Version\": \"2012-10-17\",
    \"Statement\": [{
      \"Sid\": \"TmAttachmentsObjectRW\",
      \"Effect\": \"Allow\",
      \"Action\": [\"s3:PutObject\",\"s3:GetObject\",\"s3:DeleteObject\"],
      \"Resource\": \"arn:aws:s3:::${BUCKET}/*\"
    }]
  }"

echo "==> 7/7 Publish bucket name to SSM ${SSM_PARAM}"
aws ssm put-parameter \
  --name "${SSM_PARAM}" \
  --value "${BUCKET}" \
  --type String \
  --overwrite \
  --region "${REGION}"

cat <<EOF

==> DONE.
Bucket : ${BUCKET}  (private, AES256, CORS for ${CORS_ORIGINS})
Role   : ${ROLE}  +inline policy ${POLICY_NAME} (Put/Get/Delete on bucket/*)
SSM    : ${SSM_PARAM} = ${BUCKET}

NEXT (on the EC2, picks up the new env var):
  ssh ... 'sudo systemctl restart tm-env-fetch && sudo systemctl restart tm-gunicorn'
Then tell me — I'll build + deploy the attachment app and QA uploads live.
EOF
