#!/bin/bash
# Run the first AWS Transform scan: comprehensive codebase analysis of the sample app.
# Static analysis only (build command "noop"). Billed at about $0.035 per agent-minute.
# Runs in the background so a closed Session Manager window does not stop it. Log: /opt/capstone/scans/first-scan.log
set -uo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 PATH=$HOME/.local/bin:$PATH
REPO=/opt/capstone/scans/claims-intake-sample
NAME=${1:-AWS/comprehensive-codebase-analysis}
cd "$REPO"
[ -z "$(git status --porcelain)" ] || { echo "Repo has uncommitted changes; commit or stash first."; exit 1; }
nohup atx custom def exec -n "$NAME" -p "$REPO" -c "noop" -x -t > /opt/capstone/scans/first-scan.log 2>&1 &
echo "Started $NAME (pid $!). Follow with: tail -f /opt/capstone/scans/first-scan.log"
