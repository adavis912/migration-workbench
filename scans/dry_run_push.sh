#!/bin/bash
# Switch one app's GitLab pipeline to DRY-RUN mode (every stage runs; nothing is applied, deployed or routed)
# and push to main, which starts the pipeline.        sudo bash dry_run_push.sh <APP-ID>
set -uo pipefail
APP=${1:?usage: dry_run_push.sh <APP-ID>}
declare -A SLUG=( [CS-SURV2]=survey-service [FIN-RPT-LEGACY]=finance-reporting [HR-RECR1]=recruiting-tracker
                  [MKT-WEB3]=web-content [OPS-ASSE4]=asset-inventory [OPS-PRIN1]=print-mail [OPS-VEND2]=vendor-management )
[ -n "${SLUG[$APP]:-}" ] || { echo "unknown app $APP"; exit 1; }
PROJ=adavis912-group/${SLUG[$APP]}-sample
HERE=$(cd "$(dirname "$0")" && pwd)
RAW=$(aws secretsmanager get-secret-value --region us-east-1 --secret-id capstone/dashboard-gitlab-samples-token --query SecretString --output text) \
  || { echo "could not read the GitLab token secret"; exit 1; }
GL_TOKEN=$(printf '%s' "$RAW" | grep -oE 'glpat-[A-Za-z0-9_.-]+' | head -1); unset RAW; export GL_TOKEN
W=$(mktemp -d); ASK=$(mktemp); chmod 700 "$ASK"
printf '#!/bin/sh\ncase "$1" in Username*) echo oauth2;; *) printf "%%s" "$GL_TOKEN";; esac\n' > "$ASK"
trap 'unset GL_TOKEN; rm -f "$ASK"; rm -rf "$W"' EXIT
g() { GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c safe.directory='*' "$@" 2>&1 | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'; return ${PIPESTATUS[0]}; }
g clone -q "https://gitlab.com/$PROJ.git" "$W/app" || { echo "clone failed"; exit 1; }
cd "$W/app"
/opt/capstone/venv/bin/python "$HERE/dry_run_mode.py" ci/generated.gitlab-ci.yml .gitlab-ci.yml > .gitlab-ci.yml.new || { echo "could not build the dry-run pipeline"; exit 1; }
mv .gitlab-ci.yml.new .gitlab-ci.yml
g add .gitlab-ci.yml >/dev/null
g -c user.name="Migration Workbench" -c user.email="workbench@example.invalid" commit -qm "Pipeline: dry-run mode, every stage on

Scans, validate and Terraform plans run for real (read-only role). Apply jobs plan and stop.
Build, publish, deploy, test and cutover jobs print what they would do; nothing changes in AWS.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YEGsoHEajuco5T4sjXZXaC" || { echo "nothing to commit"; exit 1; }
g push -q origin main || { echo "PUSH FAILED"; exit 1; }
echo "   pushed $(git -c safe.directory='*' rev-parse --short HEAD) to https://gitlab.com/$PROJ/-/pipelines"
echo DONE
