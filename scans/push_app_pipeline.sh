#!/bin/bash
# Add one Wave 1 app's generated Terraform and pipeline to its GitLab project, in PILOT (plan-only) mode,
# and push to main, which starts its pipeline.        sudo bash push_app_pipeline.sh <APP-ID>
# Layout matches the pilot: *.tf at the repo root, the full generated pipeline in ci/generated.gitlab-ci.yml,
# and a root .gitlab-ci.yml that includes it and switches off every job except the source scans,
# infra_validate and infra_plan_staging. Nothing is applied, deployed or cut over.
# The app's existing code is kept; the script stops if a generated file would overwrite one of its files.
# The GitLab token comes from Secrets Manager via GIT_ASKPASS and is never printed or stored.
set -uo pipefail
APP=${1:?usage: push_app_pipeline.sh <APP-ID>}
GROUP=adavis912-group
declare -A SLUG=( [CS-SURV2]=survey-service [FIN-RPT-LEGACY]=finance-reporting [HR-RECR1]=recruiting-tracker
                  [MKT-WEB3]=web-content [OPS-ASSE4]=asset-inventory [OPS-PRIN1]=print-mail [OPS-VEND2]=vendor-management )
[ -n "${SLUG[$APP]:-}" ] || { echo "unknown app $APP"; exit 1; }
PROJ=$GROUP/${SLUG[$APP]}-sample
GEN=/opt/capstone/capstone_kb/kb_aws/migration_flow/runs/wave-1/apps/$APP
PY=/opt/capstone/venv/bin/python
[ -f "$GEN/.gitlab-ci.yml" ] && [ -f "$GEN/main.tf" ] || { echo "no generated files for $APP in $GEN"; exit 1; }

RAW=$(aws secretsmanager get-secret-value --region us-east-1 --secret-id capstone/dashboard-gitlab-samples-token --query SecretString --output text) \
  || { echo "could not read the GitLab token secret"; exit 1; }
GL_TOKEN=$(printf '%s' "$RAW" | grep -oE 'glpat-[A-Za-z0-9_.-]+' | head -1); unset RAW; export GL_TOKEN
[ -n "$GL_TOKEN" ] || { echo "token not found in secret"; exit 1; }
W=$(mktemp -d); ASK=$(mktemp); chmod 700 "$ASK"
printf '#!/bin/sh\ncase "$1" in Username*) echo oauth2;; *) printf "%%s" "$GL_TOKEN";; esac\n' > "$ASK"
trap 'unset GL_TOKEN; rm -f "$ASK"; rm -rf "$W"' EXIT
g() { GIT_ASKPASS="$ASK" GIT_TERMINAL_PROMPT=0 git -c credential.helper= -c safe.directory='*' "$@" 2>&1 | sed 's/glpat-[A-Za-z0-9_.-]*/[redacted]/g'; return ${PIPESTATUS[0]}; }

echo "== $APP -> $PROJ"
g clone -q "https://gitlab.com/$PROJ.git" "$W/app" || { echo "clone failed"; exit 1; }
g clone -q --depth 1 "https://gitlab.com/$GROUP/capstone-migration-pilot.git" "$W/pilot" || { echo "pilot clone failed"; exit 1; }
if [ -f "$W/app/ci/generated.gitlab-ci.yml" ]; then echo "STOP: $PROJ already has a generated pipeline"; exit 1; fi

# Generated files go in; refuse to overwrite anything the app already has.
cd "$GEN"
FILES=$(ls *.tf .terraform.lock.hcl MIGRATION_NOTES.md terraform.tfvars.example 2>/dev/null)
for f in $FILES .gitlab-ci.yml; do
  [ -e "$W/app/$f" ] && { echo "STOP: $f already exists in $PROJ"; exit 1; }
done
mkdir -p "$W/app/ci"
cp $FILES "$W/app/"
cp .gitlab-ci.yml "$W/app/ci/generated.gitlab-ci.yml"

# Root pipeline: the pilot's variables with this app's names, and every job switched off except the plan-only set.
"$PY" - "$W/pilot/.gitlab-ci.yml" "$W/app/ci/generated.gitlab-ci.yml" "$APP" > "$W/app/.gitlab-ci.yml" <<'PY' || { echo "could not build the pilot-mode pipeline"; exit 1; }
import re, sys, yaml
class L(yaml.SafeLoader): pass
L.add_multi_constructor("!", lambda l, s, n: None)   # GitLab tags such as !reference
pilot, gen, app = sys.argv[1:4]
p = yaml.load(open(pilot), Loader=L)
g = yaml.load(open(gen), Loader=L)
v = dict(p.get("variables") or {})
host = app.lower()
v["TF_VAR_app_dns_name"] = f"{host}.pilot.example.internal"
v["TF_VAR_legacy_endpoint"] = f"legacy-{host}.example.internal"
reserved = {"stages", "variables", "default", "workflow", "include", "image", "services", "cache", "before_script", "after_script"}
jobs = [k for k, j in g.items() if k not in reserved and not k.startswith(".") and isinstance(j, dict)]
first_stage = (g.get("stages") or [None])[0]
scans = {"gitleaks", "semgrep", "checkov"}
is_validate = lambda k: "validate" in k
is_plan_stg = lambda k: "plan" in k and "staging" in k
kept = [k for k in jobs if k in scans or g[k].get("stage") == first_stage or is_validate(k) or is_plan_stg(k)]
off = [k for k in jobs if k not in kept]
if not any(is_validate(k) for k in kept) or not any(is_plan_stg(k) for k in kept):
    sys.exit("generated pipeline has no validate / staging-plan job; jobs: " + ", ".join(jobs))
# A kept job may 'need' a job that is now switched off; keep only the needs that still exist.
needs_fix = {}
for k in kept:
    n = g[k].get("needs")
    if isinstance(n, list):
        names = [x if isinstance(x, str) else (x or {}).get("job") for x in n]
        if any(nm not in kept for nm in names if nm):
            needs_fix[k] = [x for x, nm in zip(n, names) if nm in kept]
out = ["# PILOT MODE for %s: runs the source scans, infra_validate and infra_plan_staging only." % app,
       "# The full generated pipeline lives in ci/generated.gitlab-ci.yml; every job that builds, publishes,",
       "# applies, deploys, tests or cuts over is switched off here. Nothing is applied or deployed.",
       "include:", "  - local: ci/generated.gitlab-ci.yml", "", "variables:"]
for k, val in v.items():
    out.append("  %s: %s" % (k, yaml.safe_dump(val, default_flow_style=True).strip().removesuffix("...").strip()))
for k in off:
    out += ["", "%s:" % k, "  rules:", "    - when: never"]
for k, n in needs_fix.items():
    out += ["", "%s:" % k, "  needs: %s" % yaml.safe_dump(n, default_flow_style=True).strip()]
print("\n".join(out))
print("kept: " + ", ".join(kept), file=sys.stderr)
PY

cd "$W/app"
g add -A >/dev/null
g -c user.name="Migration Workbench" -c user.email="workbench@example.invalid" commit -qm "Add generated Terraform and pipeline for $APP (pilot mode, plan-only)

Agent-generated infrastructure and GitLab CI from the Wave 1 build. Runs source scans,
terraform validate and a staging plan only; nothing is applied or deployed.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01YEGsoHEajuco5T4sjXZXaC" || { echo "commit failed"; exit 1; }
g push -q origin main || { echo "PUSH FAILED"; exit 1; }
echo "   pushed $(git -c safe.directory='*' rev-parse --short HEAD) to https://gitlab.com/$PROJ (pipeline starts on push)"
echo "   pipelines: https://gitlab.com/$PROJ/-/pipelines"
echo DONE
