#!/usr/bin/env bash
# Build the pilot repo for CLM-FRAU1 and push it to GitLab. Token is read from
# Secrets Manager and handed to git through GIT_ASKPASS; it is never printed.
set -euo pipefail
APP=~/capstone_kb/kb_aws/migration_flow/runs/wave-1/apps/CLM-FRAU1
R=/tmp/pilot_repo
rm -rf "$R" && mkdir -p "$R/ci"
cp "$APP"/*.tf "$APP"/MIGRATION_NOTES.md "$APP"/terraform.tfvars.example "$R"/
cp "$APP"/.gitlab-ci.yml "$R"/ci/generated.gitlab-ci.yml
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SUBNETS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC --query 'Subnets[0:2].SubnetId' --output json | tr -d ' \n')
cat > "$R/.gitlab-ci.yml" <<EOF
# PILOT MODE: runs source_scan, infra_validate and infra_plan_staging only.
# The full generated pipeline lives in ci/generated.gitlab-ci.yml; every job
# that builds, publishes, applies, deploys, tests or cuts over is switched off here.
include:
  - local: ci/generated.gitlab-ci.yml

variables:
  AWS_ROLE_ARN: "arn:aws:iam::111122223333:role/capstone-gitlab-plan"
  TF_STATE_BUCKET: "capstone-tfstate-111122223333"
  TF_LOCK_TABLE: "capstone-tf-locks"
  TF_VAR_vpc_id: "$VPC"
  TF_VAR_private_subnet_ids: '$SUBNETS'
  TF_VAR_onprem_cidrs: '["10.200.0.0/16"]'
  TF_VAR_kms_key_arn: "arn:aws:kms:us-east-1:111122223333:key/00000000-0000-0000-0000-000000000000"
  TF_VAR_artifact_bucket_name: "capstone-pilot-artifacts-placeholder"
  TF_VAR_route53_zone_id: "Z00000000PILOTPLACEHOLDER"
  TF_VAR_app_dns_name: "clm-frau1.pilot.example.internal"
  TF_VAR_legacy_endpoint: "legacy-clm-frau1.example.internal"

.pilot_off:
  rules:
    - when: never
EOF
for j in build publish_scan publish_staging publish_production infra_plan_production \
         infra_apply_staging infra_apply_production deploy_staging deploy_production \
         integration_tests zap_dast k6_perf cutover_5 cutover_25 cutover_100 \
         cutover_rollback cutover_rollback_manual; do
  printf '\n%s:\n  rules:\n    - when: never\n' "$j" >> "$R/.gitlab-ci.yml"
done
cat > "$R/README.md" <<'EOF'
# Capstone migration pilot: CLM-FRAU1

Agent-generated Terraform and GitLab CI for one Wave 1 application, run in
pilot mode: source scans, terraform validate and a staging plan only.
Nothing is applied or deployed. AWS access is through GitLab OIDC to a
read-only plan role.
EOF
cd "$R"
git init -q -b main
git config user.name "adavis912"
git config user.email "adavis912@users.noreply.gitlab.com"
git add -A
git commit -q -m "Pilot: CLM-FRAU1 generated Terraform and pipeline (plan-only)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YEGsoHEajuco5T4sjXZXaC"
RAW=$(aws secretsmanager get-secret-value --secret-id capstone/gitlab-push-token --query SecretString --output text)
GL_TOKEN=$(printf '%s' "$RAW" | python3 -c 'import sys,json
s=sys.stdin.read().strip()
try:
    d=json.loads(s); print(next(iter(d.values())) if isinstance(d,dict) else s)
except Exception: print(s)')
unset RAW
ASK=$(mktemp); printf '#!/bin/sh\ncase "$1" in Username*) echo oauth2;; *) printf "%%s" "$GL_TOKEN";; esac\n' > "$ASK"; chmod 700 "$ASK"
export GL_TOKEN
GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= push -q https://gitlab.com/adavis912-group/capstone-migration-pilot.git main 2>&1 | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'
rc=${PIPESTATUS[0]}
unset GL_TOKEN; rm -f "$ASK"
echo "push exit=$rc"; git log --oneline | head -1; ls -A
