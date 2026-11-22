#!/bin/bash
# Let one Wave 1 app's GitLab project (main branch only) sign in to the read-only plan role.
#   bash trust_app_project.sh <APP-ID>
# Adds "project_path:<group>/<project>:ref_type:branch:ref:main" to the role's gitlab.com:sub list;
# everything else in the trust policy is kept. Prints the resulting list.
set -euo pipefail
APP=${1:?usage: trust_app_project.sh <APP-ID>}
declare -A SLUG=( [CS-SURV2]=survey-service [FIN-RPT-LEGACY]=finance-reporting [HR-RECR1]=recruiting-tracker
                  [MKT-WEB3]=web-content [OPS-ASSE4]=asset-inventory [OPS-PRIN1]=print-mail [OPS-VEND2]=vendor-management )
[ -n "${SLUG[$APP]:-}" ] || { echo "unknown app $APP"; exit 1; }
SUB="project_path:adavis912-group/${SLUG[$APP]}-sample:ref_type:branch:ref:main"
T=$(mktemp)
aws iam get-role --role-name capstone-gitlab-plan --query 'Role.AssumeRolePolicyDocument' --output json > "$T"
python3 - "$T" "$SUB" <<'PY'
import json, sys
p, sub = sys.argv[1], sys.argv[2]
d = json.load(open(p))
c = d["Statement"][0]["Condition"]["StringEquals"]
cur = c["gitlab.com:sub"]
cur = [cur] if isinstance(cur, str) else list(cur)
if sub not in cur:
    cur.append(sub)
c["gitlab.com:sub"] = cur
json.dump(d, open(p, "w"))
PY
aws iam update-assume-role-policy --role-name capstone-gitlab-plan --policy-document "file://$T"
rm -f "$T"
aws iam get-role --role-name capstone-gitlab-plan --query 'Role.AssumeRolePolicyDocument.Statement[0].Condition.StringEquals."gitlab.com:sub"' --output text | tr '\t' '\n'
