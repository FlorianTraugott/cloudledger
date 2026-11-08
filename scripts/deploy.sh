#!/usr/bin/env bash
# Deploy CloudLedger to AWS in one command.
#   ./scripts/deploy.sh
# Optional:  STACK_NAME=cloudledger AWS_REGION=ap-south-1 ./scripts/deploy.sh
# New AWS account still waiting for CloudFront verification?
#             ENABLE_CLOUDFRONT=false ./scripts/deploy.sh      (serves the site from S3 website hosting)
set -euo pipefail

STACK="${STACK_NAME:-cloudledger}"
REGION="${AWS_REGION:-ap-south-1}"
ENABLE_CLOUDFRONT="${ENABLE_CLOUDFRONT:-true}"
cd "$(dirname "$0")/.."

step() { printf "\n\033[1;33m==> %s\033[0m\n" "$*"; }

step "Checking tools and AWS credentials"
for t in aws sam; do
  command -v "$t" >/dev/null || { echo "Missing '$t'. See docs/DEPLOYMENT_GUIDE.md step 1."; exit 1; }
done
aws sts get-caller-identity --query "Arn" --output text

step "Validating template.yaml"
sam validate --region "$REGION"

STATUS="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo NONE)"
if [ "$STATUS" = "ROLLBACK_COMPLETE" ]; then
  step "Previous first deploy failed (ROLLBACK_COMPLETE) - deleting the empty stack first"
  aws cloudformation delete-stack --stack-name "$STACK" --region "$REGION"
  aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$REGION"
fi

step "Deploying backend + infrastructure (CloudFormation stack: $STACK, region: $REGION)"
# No third-party Python packages are needed, so SAM can zip backend/src directly (no 'sam build').
sam deploy \
  --template-file template.yaml \
  --stack-name "$STACK" \
  --region "$REGION" \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "EnableCloudFront=$ENABLE_CLOUDFRONT" \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
API_URL="$(out ApiUrl)"
WEB_URL="$(out WebsiteUrl)"
BUCKET="$(out FrontendBucketName)"
DIST="$(out DistributionId)"
DASH="$(out DashboardUrl)"

step "Uploading website to S3 (API_URL=$API_URL)"
BUILD_DIR="$(mktemp -d)"
cp -R frontend/. "$BUILD_DIR/"
printf 'window.APP_CONFIG = { API_URL: "%s" };\n' "$API_URL" > "$BUILD_DIR/config.js"
aws s3 sync "$BUILD_DIR" "s3://$BUCKET" --delete --region "$REGION"
rm -rf "$BUILD_DIR"

if [ -n "$DIST" ] && [ "$DIST" != "None" ]; then
  step "Refreshing CloudFront cache"
  aws cloudfront create-invalidation --distribution-id "$DIST" --paths "/*" --query "Invalidation.Id" --output text
fi

step "Smoke test"
curl -fsS "$API_URL/health" && echo

cat <<EOF

  ✅ CloudLedger is deployed

  Website   : $WEB_URL
  API       : $API_URL
  Dashboard : $DASH

  (A brand-new CloudFront distribution can take 5-10 minutes before the website loads.)
EOF
