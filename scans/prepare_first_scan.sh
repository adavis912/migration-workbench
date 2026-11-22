#!/bin/bash
# Prepare the first AWS Transform code scan on the migration workbench.
# Clones the sample app with the read-only token from Secrets Manager (never printed) and checks the CLI.
set -uo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 PATH=$HOME/.local/bin:$PATH
W=/opt/capstone/scans; REPO=$W/claims-intake-sample
mkdir -p "$W"
if [ ! -d "$REPO/.git" ]; then
  RAW=$(aws secretsmanager get-secret-value --secret-id capstone/gitlab-sample-read-token --query SecretString --output text 2>/dev/null) \
    || { echo "Secret capstone/gitlab-sample-read-token not found yet - store the token first."; exit 1; }
  GL_TOKEN=$(printf '%s' "$RAW" | python3 -c 'import sys,json
s=sys.stdin.read().strip()
try:
    d=json.loads(s); print(next(iter(d.values())) if isinstance(d,dict) else s)
except Exception: print(s)')
  unset RAW; export GL_TOKEN
  ASK=$(mktemp); printf '#!/bin/sh\ncase "$1" in Username*) echo oauth2;; *) printf "%%s" "$GL_TOKEN";; esac\n' > "$ASK"; chmod 700 "$ASK"
  GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= clone -q https://gitlab.com/adavis912-group/claims-intake-sample.git "$REPO" 2>&1 | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'
  rm -f "$ASK"; unset GL_TOKEN
fi
cd "$REPO" || exit 1
echo "repo: $(git log --oneline -1) | uncommitted files: $(git status --porcelain | wc -l) | files: $(git ls-files | wc -l)"
echo "atx: $(atx --version 2>&1 | head -1)"
echo "identity: $(aws sts get-caller-identity --query Arn --output text)"
echo "--- AWS-managed transformations matching 'analysis':"
atx custom def list 2>&1 | grep -iE 'analysis|codebase|error|denied' | head -10
