#!/bin/bash
# Push the Migration Workbench code (kb_aws) to gitlab.com/adavis912-group/migration-workbench (main),
# then keep a clean clone at /opt/capstone/migration-workbench for future commits.
#   bash push_workbench.sh [/tmp/wb_README.md]
# Copies a filtered snapshot: no Terraform state or tfvars, no caches, no generated runs/output, no keys.
# Stops before committing if anything that looks like a credential is found.
# The GitLab token comes from Secrets Manager via GIT_ASKPASS; it is never printed or stored in the remote.
set -uo pipefail
SRC=/opt/capstone/capstone_kb/kb_aws
DEST=/opt/capstone/migration-workbench
URL=https://gitlab.com/adavis912-group/migration-workbench
README=${1:-/tmp/wb_README.md}

W=$(mktemp -d)
rsync -a --no-owner --no-group \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '.terraform/' --exclude '.terraform.lock.hcl' \
  --exclude '*.tfstate' --exclude '*.tfstate.*' --exclude '*.tfvars' --exclude 'tf_outputs*.json' \
  --exclude 'loader/build/' --exclude 'migration_flow/runs/' --exclude 'wave_planner/out/' \
  --exclude 'dashboard/arch/' --exclude '.venv/' --exclude 'venv/' --exclude 'node_modules/' \
  --exclude '*.log' --exclude '*.b64' --exclude '*.xz' --exclude '*.tar' --exclude '*.tgz' \
  --exclude '*.pem' --exclude '*.key' --exclude '.env' --exclude '.git/' \
  "$SRC/" "$W/"
[ -s "$README" ] && cp "$README" "$W/README.md" && cp "$README" "$W/dashboard/README.md"

cat > "$W/.gitignore" <<'EOF'
__pycache__/
*.pyc
.terraform/
.terraform.lock.hcl
*.tfstate
*.tfstate.*
*.tfvars
!*.tfvars.example
tf_outputs*.json
loader/build/
migration_flow/runs/
wave_planner/out/
dashboard/arch/
.venv/
venv/
node_modules/
*.log
*.pem
*.key
.env
EOF

echo "== credential check"
HITS=$(grep -rIlE 'glpat-[A-Za-z0-9_-]{15,}|sk-ant-[A-Za-z0-9_-]{10,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=[:space:]]{40}' "$W" || true)
if [ -n "$HITS" ]; then echo "STOP: possible credentials in:"; echo "$HITS" | sed "s#$W/##"; rm -rf "$W"; exit 1; fi
echo "   none found"
echo "== files: $(find "$W" -type f | wc -l), size: $(du -sh "$W" | cut -f1)"

RAW=$(aws secretsmanager get-secret-value --region us-east-1 --secret-id capstone/dashboard-gitlab-samples-token --query SecretString --output text) \
  || { echo "could not read capstone/dashboard-gitlab-samples-token"; rm -rf "$W"; exit 1; }
GL_TOKEN=$(printf '%s' "$RAW" | grep -oE 'glpat-[A-Za-z0-9_.-]+' | head -1); unset RAW; export GL_TOKEN
[ -n "$GL_TOKEN" ] || { echo "token not found in secret"; rm -rf "$W"; exit 1; }
ASK=$(mktemp); chmod 700 "$ASK"
printf '#!/bin/sh\ncase "$1" in Username*) echo oauth2;; *) printf "%%s" "$GL_TOKEN";; esac\n' > "$ASK"
trap 'unset GL_TOKEN; rm -f "$ASK"; rm -rf "$W"' EXIT
g() { GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= "$@" 2>&1 | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'; return ${PIPESTATUS[0]}; }

( cd "$W" && git init -q -b main && git add -A && \
  git -c user.name="Migration Workbench" -c user.email="workbench@example.invalid" commit -qm "Migration Workbench: dashboard, agents, knowledge base, wave planner and infrastructure

Initial import of the capstone Migration Workbench (fictional Harborview Insurance portfolio).
Excludes Terraform state and tfvars, caches, generated runs and output.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YEGsoHEajuco5T4sjXZXaC" ) || { echo "commit failed"; exit 1; }

echo "== push"
(cd "$W" && g push -q "$URL.git" main) || { echo "PUSH FAILED"; exit 1; }
echo "   pushed $(git -C "$W" rev-parse --short HEAD) to $URL"

if [ -d "$DEST/.git" ]; then echo "   $DEST already exists, leaving it"; else
  g clone -q "$URL.git" "$DEST" && echo "   cloned to $DEST (remote has no credentials)"; fi
echo DONE
