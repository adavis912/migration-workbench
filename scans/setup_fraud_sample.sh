#!/bin/bash
# Create the local Fraud Scoring sample repo on the workbench and register it to CLM-FRAU1. No remote, no GitLab.
set -euo pipefail
K=/opt/capstone/capstone_kb/kb_aws
R=/opt/capstone/scans/fraud-scoring-sample
[ -d "$R/.git" ] && { echo "$R already exists"; exit 1; }
cp -r "$K/sample_app/fraud-scoring" "$R"
cd "$R"
git init -q -b main
git -c user.name="Capstone sample" -c user.email="sample@example.invalid" add -A
git -c user.name="Capstone sample" -c user.email="sample@example.invalid" commit -qm "Add Fraud Scoring sample legacy app (fictional) for CLM-FRAU1"
BASE=$(git rev-parse --short HEAD)
python3 - "$K/scans/apps.json" "$R" "$BASE" <<'PY'
import json, sys
p, repo, base = sys.argv[1:4]
d = json.load(open(p))
d[repo] = {"app": "CLM-FRAU1", "repo": "fraud-scoring-sample (local repository on the workbench)", "repo_url": "",
           "base_commit": base, "note": "Stand-in source for CLM-FRAU1 (fictional legacy code built for the capstone); not in GitLab."}
json.dump(d, open(p, "w"), indent=2)
PY
echo "created $R at $BASE and registered it to CLM-FRAU1"
