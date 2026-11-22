#!/bin/bash
# For each remaining Wave 1 app: commit its fictional sample code, push it to a new private GitLab project,
# clone that project back to /opt/capstone/scans/<slug>-sample and register it in apps.json so the dashboard
# offers a Start scan button. Does NOT run atx. The GitLab token is read from Secrets Manager and handed to
# git through GIT_ASKPASS; it is never printed, and remotes are stored without credentials.
set -uo pipefail
K=/opt/capstone/capstone_kb/kb_aws
S=/opt/capstone/scans
GROUP=adavis912-group
APPS="CS-SURV2:survey-service FIN-RPT-LEGACY:finance-reporting HR-RECR1:recruiting-tracker MKT-WEB3:web-content OPS-ASSE4:asset-inventory OPS-PRIN1:print-mail OPS-VEND2:vendor-management"

RAW=$(aws secretsmanager get-secret-value --region us-east-1 --secret-id capstone/dashboard-gitlab-samples-token --query SecretString --output text) \
  || { echo "could not read capstone/dashboard-gitlab-samples-token"; exit 1; }
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
trap 'unset GL_TOKEN; rm -f "$ASK"' EXIT
g() { GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= "$@" 2>&1 | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'; return ${PIPESTATUS[0]}; }

ok=0; fail=0
for pair in $APPS; do
  APP=${pair%%:*}; SLUG=${pair#*:}; PROJ=$SLUG-sample; URL=https://gitlab.com/$GROUP/$PROJ
  DEST=$S/$PROJ
  echo "== $APP -> $GROUP/$PROJ"
  if [ -d "$DEST/.git" ]; then echo "   already cloned at $DEST, skipping"; continue; fi
  W=$(mktemp -d); cp -r "$K/sample_app/$SLUG/." "$W/"
  ( cd "$W" && git init -q -b main && git add -A && \
    git -c user.name="Capstone sample" -c user.email="sample@example.invalid" commit -qm "Add $APP sample legacy app (fictional) for the capstone

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YEGsoHEajuco5T4sjXZXaC" )
  # GitLab creates a private project on first push when the project does not exist yet.
  if ! (cd "$W" && g push -q "$URL.git" main); then echo "   PUSH FAILED for $APP"; fail=$((fail+1)); rm -rf "$W"; continue; fi
  rm -rf "$W"
  if ! g clone -q "$URL.git" "$DEST"; then echo "   CLONE FAILED for $APP"; fail=$((fail+1)); continue; fi
  BASE=$(git -C "$DEST" rev-parse --short HEAD)
  python3 - "$K/scans/apps.json" "$DEST" "$APP" "$GROUP/$PROJ" "$URL" "$BASE" <<'PY'
import json, sys
p, dest, app, proj, url, base = sys.argv[1:7]
d = json.load(open(p))
d[dest] = {"app": app, "repo": proj, "repo_url": url, "base_commit": base,
           "note": f"Stand-in source for {app} (fictional legacy code built for the capstone)."}
json.dump(d, open(p, "w"), indent=2)
PY
  echo "   pushed, cloned to $DEST at $BASE, registered"; ok=$((ok+1))
done
echo "DONE ok=$ok failed=$fail"
