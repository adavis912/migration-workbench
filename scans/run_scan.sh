#!/bin/bash
# Run an AWS Transform scan on a repo that is registered to a portfolio app in apps.json.
#   bash run_scan.sh /opt/capstone/scans/<repo> [AWS/comprehensive-codebase-analysis]
# Static analysis only (build command "noop"); about $0.035 per agent-minute. The dashboard picks the result up
# within 2 minutes and shows it on the Assessment Phase page under that app.
set -uo pipefail
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 PATH=$HOME/.local/bin:$PATH
REPO=$(readlink -f "${1:?usage: run_scan.sh <repo path> [transformation]}")
NAME=${2:-AWS/comprehensive-codebase-analysis}
HERE=$(dirname "$(readlink -f "$0")")
APP=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], {}).get("app", ""))' "$HERE/apps.json" "$REPO")
[ -n "$APP" ] || { echo "$REPO is not in $HERE/apps.json. Add it with its app ID first."; exit 1; }
cd "$REPO" && [ -z "$(git status --porcelain)" ] || { echo "Repo has uncommitted changes; commit or stash first."; exit 1; }
LOG=/opt/capstone/scans/scan-$(basename "$REPO")-$(date +%Y%m%d-%H%M%S).log
nohup atx custom def exec -n "$NAME" -p "$REPO" -c "noop" -x -t > "$LOG" 2>&1 &
echo "Started $NAME on $REPO for $APP (pid $!). Log: $LOG"
