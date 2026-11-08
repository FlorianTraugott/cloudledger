#!/usr/bin/env bash
# Delete everything CloudLedger created in AWS (run after your evaluation to avoid charges).
set -euo pipefail

STACK="${STACK_NAME:-cloudledger}"
REGION="${AWS_REGION:-ap-south-1}"

out() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

read -r -p "This permanently deletes stack '$STACK' and ALL its data in $REGION. Type 'delete' to continue: " ok
[ "$ok" = "delete" ] || { echo "Cancelled."; exit 1; }

for b in "$(out FrontendBucketName)" "$(out ReceiptsBucketName)"; do
  echo "Emptying s3://$b"
  aws s3 rm "s3://$b" --recursive --region "$REGION" || true
done

sam delete --stack-name "$STACK" --region "$REGION" --no-prompts
echo "Done. All CloudLedger resources removed."
