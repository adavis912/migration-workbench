#!/bin/bash
# Wait for the CLM-FRAU1 ATX scan to finish, then push the Fraud Scoring sample (main + the ATX result branch)
# to GitLab and record the project URL in apps.json. The token is read from Secrets Manager and handed to git
# through GIT_ASKPASS; it is never printed or written to disk outside a 700 temp file that is removed.
set -uo pipefail
UNIT=${1:?usage: push_fraud_sample.sh <atx unit name>}
R=/opt/capstone/scans/fraud-scoring-sample
K=/opt/capstone/capstone_kb/kb_aws
PROJECT=adavis912-group/fraud-scoring-sample
URL=https://gitlab.com/$PROJECT
log() { echo "$(date -u +%FT%TZ) $*"; }

log "waiting for $UNIT to finish"
for i in $(seq 1 180); do                       # up to 3 hours
  systemctl is-active -q "$UNIT" || break
  sleep 60
done
systemctl is-active -q "$UNIT" && { log "scan still running after 3h; not pushing"; exit 1; }
log "scan finished: $(systemctl show -p Result --value "$UNIT" 2>/dev/null || echo unknown)"

cd "$R"
BRANCHES=$(git for-each-ref --format='%(refname:short)' refs/heads/ | grep -E '^(main|atx-result-staging-.*)$' | tr '\n' ' ')
log "branches to push: $BRANCHES"

RAW=$(aws secretsmanager get-secret-value --region us-east-1 --secret-id capstone/dashboard-gitlab-samples-token --query SecretString --output text) \
  || { log "could not read capstone/dashboard-gitlab-samples-token"; exit 1; }
GL_TOKEN=$(printf '%s' "$RAW" | python3 -c 'import sys,json,re
s=sys.stdin.read().strip()
m=re.search(r"glpat-[A-Za-z0-9_.-]+", s)
if m: print(m.group(0))
else:
    try:
        d=json.loads(s); print(next(iter(d.values())) if isinstance(d,dict) else s)
    except Exception: print(s)')
unset RAW; export GL_TOKEN
ASK=$(mktemp); chmod 700 "$ASK"
printf '#!/bin/sh\ncase "$1" in Username*) echo oauth2;; *) printf "%%s" "$GL_TOKEN";; esac\n' > "$ASK"
# GitLab creates a private project on first push if it does not exist yet.
GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= push "$URL.git" $BRANCHES 2>&1 \
  | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'
rc=${PIPESTATUS[0]}
unset GL_TOKEN; rm -f "$ASK"
log "push exit=$rc"
[ "$rc" = 0 ] || exit "$rc"

python3 - "$K/scans/apps.json" "$R" "$PROJECT" "$URL" <<'PY'
import json, sys
p, repo, project, url = sys.argv[1:5]
d = json.load(open(p))
d[repo].update(repo=project, repo_url=url,
               note="Stand-in source for CLM-FRAU1 (fictional legacy code built for the capstone).")
json.dump(d, open(p, "w"), indent=2)
PY
log "apps.json updated: CLM-FRAU1 -> $URL"
