#!/usr/bin/env bash
# One-time AWS setup for the Context Window podcast bucket + GitHub Actions OIDC role.
#
# Usage:
#   infra/setup-aws.sh <bucket-name> [region] [github-owner/repo] [s3-prefix]
# Example:
#   infra/setup-aws.sh context-window-podcast us-east-1 msangui/cw-github-actions
#
# Creates (idempotently):
#   1. S3 bucket with public READ only on feed.xml, feed-extended.xml, cover.png, episodes/*
#      (work/ and state/ stay private).
#   2. GitHub OIDC identity provider in IAM (if missing).
#   3. IAM role "<bucket>-github-actions" trusted by the given repo, allowed to read/write the bucket.
# Prints the repo variables to set at the end.
set -euo pipefail

BUCKET="${1:?bucket name required}"
REGION="${2:-us-east-1}"
REPO="${3:-}"
PREFIX="${4:-}"
ROLE_NAME="${BUCKET}-github-actions"
HERE="$(cd "$(dirname "$0")" && pwd)"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

echo "▶ Account $ACCOUNT_ID · bucket s3://$BUCKET · region $REGION"

# 1. Bucket
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "✓ Bucket exists"
else
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
  echo "✓ Bucket created"
fi

aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false
aws s3api put-bucket-ownership-controls --bucket "$BUCKET" --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'

PFX=""; [ -n "$PREFIX" ] && PFX="${PREFIX%/}/"
sed -e "s#BUCKET_NAME#$BUCKET#g" -e "s#PREFIX_#$PFX#g" "$HERE/bucket-policy.json" > /tmp/cw-bucket-policy.json
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file:///tmp/cw-bucket-policy.json
echo "✓ Public-read policy applied (feed*.xml, cover.png, episodes/*)"

# CORS so browser-based podcast players can fetch the feed/audio
aws s3api put-bucket-cors --bucket "$BUCKET" --cors-configuration '{"CORSRules":[{"AllowedOrigins":["*"],"AllowedMethods":["GET","HEAD"],"AllowedHeaders":["*"],"MaxAgeSeconds":3000}]}'
echo "✓ CORS configured"

if [ -z "$REPO" ]; then
  echo
  echo "No GitHub repo given — skipping OIDC role. Re-run with owner/repo to create it,"
  echo "or use AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY secrets instead."
  exit 0
fi

# 2. OIDC provider
OIDC_ARN="arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  echo "✓ GitHub OIDC provider exists"
else
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 1c58a3a8518e8759bf075b76b750d4f2df264fcd >/dev/null
  echo "✓ GitHub OIDC provider created"
fi

# 3. Role + policy
# GitHub's OIDC `sub` claim now embeds numeric IDs: repo:OWNER@OWNER_ID/REPO@REPO_ID:ref:...
# The trust policy accepts both the classic and the ID-qualified form, so we need the IDs.
OWNER="${REPO%%/*}"; NAME="${REPO##*/}"
if command -v gh >/dev/null 2>&1 && IDS="$(gh api "repos/$REPO" --jq '"\(.owner.id) \(.id)"' 2>/dev/null)"; then
  OWNER_ID="${IDS%% *}"; REPO_ID="${IDS##* }"
else
  OWNER_ID="$(curl -fsSL "https://api.github.com/users/$OWNER" | sed -n 's/.*"id": *\([0-9]*\),.*/\1/p' | head -1)"
  REPO_ID="$(curl -fsSL "https://api.github.com/repos/$REPO" | sed -n 's/.*"id": *\([0-9]*\),.*/\1/p' | head -1)"
fi
if [ -z "${OWNER_ID:-}" ] || [ -z "${REPO_ID:-}" ]; then
  echo "✗ Could not resolve GitHub owner/repo IDs for $REPO (needed for the OIDC trust policy)." >&2
  echo "  Install gh (and run gh auth login) or check the repo name, then re-run." >&2
  exit 1
fi
echo "✓ GitHub IDs: owner=$OWNER_ID repo=$REPO_ID"
sed -e "s#ACCOUNT_ID#$ACCOUNT_ID#g" \
    -e "s#GITHUB_OWNER_ID#$OWNER_ID#g" -e "s#GITHUB_REPO_ID#$REPO_ID#g" \
    -e "s#GITHUB_OWNER#$OWNER#g" -e "s#GITHUB_REPO#$NAME#g" \
    "$HERE/github-oidc-trust-policy.json" > /tmp/cw-trust.json
sed -e "s#BUCKET_NAME#$BUCKET#g" "$HERE/github-actions-iam-policy.json" > /tmp/cw-perms.json

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document file:///tmp/cw-trust.json
  echo "✓ Role exists (trust policy refreshed)"
else
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document file:///tmp/cw-trust.json --max-session-duration 10800 >/dev/null
  echo "✓ Role created"
fi
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name "${BUCKET}-rw" --policy-document file:///tmp/cw-perms.json
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"

cat <<MSG

Done. Set these in the GitHub repo (Settings → Secrets and variables → Actions → Variables):

  gh variable set S3_BUCKET       --repo $REPO --body "$BUCKET"
  gh variable set AWS_REGION      --repo $REPO --body "$REGION"
  gh variable set AWS_ROLE_ARN    --repo $REPO --body "$ROLE_ARN"
  gh variable set PUBLIC_BASE_URL --repo $REPO --body "https://$BUCKET.s3.$REGION.amazonaws.com"
$( [ -n "$PREFIX" ] && echo "  gh variable set S3_PREFIX       --repo $REPO --body \"$PREFIX\"" )

Then the secrets (values from your own vault, never commit them):

  gh secret set ANTHROPIC_API_KEY  --repo $REPO
  gh secret set ELEVENLABS_API_KEY --repo $REPO
  gh secret set OPENAI_API_KEY     --repo $REPO   # optional
  gh secret set SERPER_API_KEY     --repo $REPO   # optional
  gh secret set TELEGRAM_BOT_TOKEN --repo $REPO   # optional
  gh secret set TELEGRAM_CHAT_ID   --repo $REPO   # optional

Feed URL to submit to Spotify for Podcasters once the first episode is published:
  https://$BUCKET.s3.$REGION.amazonaws.com/${PFX}feed.xml
MSG
