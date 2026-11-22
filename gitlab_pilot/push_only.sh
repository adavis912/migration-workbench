#!/usr/bin/env bash
# Push the current commit of /tmp/pilot_repo. Token comes from Secrets Manager via GIT_ASKPASS; never printed.
set -uo pipefail
cd /tmp/pilot_repo
RAW=$(aws secretsmanager get-secret-value --secret-id capstone/gitlab-push-token --query SecretString --output text)
GL_TOKEN=$(printf '%s' "$RAW" | python3 -c 'import sys,json
s=sys.stdin.read().strip()
try:
    d=json.loads(s); print(next(iter(d.values())) if isinstance(d,dict) else s)
except Exception: print(s)')
unset RAW; export GL_TOKEN
ASK=$(mktemp); printf '#!/bin/sh\ncase "$1" in Username*) echo oauth2;; *) printf "%%s" "$GL_TOKEN";; esac\n' > "$ASK"; chmod 700 "$ASK"
GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= push -q https://gitlab.com/adavis912-group/capstone-migration-pilot.git main 2>&1 | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'
rc=${PIPESTATUS[0]}
unset GL_TOKEN; rm -f "$ASK"
echo "push exit=$rc"; git log --oneline | head -3
