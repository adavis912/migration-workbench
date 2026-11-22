#!/usr/bin/env python3
"""Switch an app's GitLab pipeline wrapper from plan-only to DRY-RUN mode: every stage runs.

    dry_run_mode.py <generated.gitlab-ci.yml> <current root .gitlab-ci.yml>  > new root .gitlab-ci.yml

  * Source scans, Terraform validate and every Terraform plan run for real (read-only AWS role).
  * Terraform apply jobs run init + a real `terraform plan` of what would be applied, then stop: "DRY RUN".
  * Build, publish, deploy, test and cutover jobs run and print the commands they would execute. Nothing is
    uploaded, deployed or routed. Rollback jobs only run if an earlier job fails, as in the real pipeline.
The existing wrapper's variables and fixes (checkov image, .terraform_job) are kept; only the
"switched off" blocks are replaced.
"""
import json
import re
import sys

import yaml


class Ref(list):
    pass


class L(yaml.SafeLoader):
    pass


L.add_constructor("!reference", lambda l, n: Ref(l.construct_sequence(n)))
L.add_multi_constructor("!", lambda l, s, n: None)

gen_path, cur_path = sys.argv[1:3]
g = yaml.load(open(gen_path), Loader=L)
cur = open(cur_path).read()
MARK = "\n# ---------------------------------------------------------------- DRY-RUN overrides"
cur = cur.split(MARK)[0].rstrip("\n") + "\n"   # re-running replaces the earlier dry-run section

RESERVED = {"stages", "variables", "default", "workflow", "include", "image", "services", "cache", "before_script", "after_script"}
jobs = [k for k, v in g.items() if k not in RESERVED and not k.startswith(".") and isinstance(v, dict)]
first_stage = (g.get("stages") or [None])[0]
real = {k for k in jobs if k in ("gitleaks", "semgrep", "checkov") or g[k].get("stage") == first_stage
        or "validate" in k or ("plan" in k and "apply" not in k)}
apply_jobs = [k for k in jobs if "apply" in k]
rollback = [k for k in jobs if "rollback" in k]
ON_MAIN = [{"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"}]


def q(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def item(x):
    if isinstance(x, Ref):
        return "!reference [" + ", ".join(x) + "]"
    return yaml.safe_dump(x, default_flow_style=True, width=10_000).strip().removesuffix("...").strip()


def script_lines(job):
    s = g[job].get("script") or []
    return [x for x in s if isinstance(x, str)]


# 1. Drop the "switched off" blocks and any earlier needs fixes from the current wrapper.
new = re.sub(r"(?m)^[A-Za-z0-9_.-]+:\n  rules:\n    - when: never\n\n?", "", cur)
new = re.sub(r"(?m)^[A-Za-z0-9_.-]+:\n  needs: \[[^\n]*\]\n\n?", "", new)
new = new.replace("# PILOT MODE", "# DRY-RUN MODE (was pilot mode)", 1)
if "AWS_ROLE_ARN_PRODUCTION:" not in new:
    m = re.search(r"\n  AWS_ROLE_ARN_STAGING: ([^\n]+)\n", new)
    if m:
        new = new.replace(m.group(0), m.group(0) + f"  AWS_ROLE_ARN_PRODUCTION: {m.group(1)}\n", 1)
out = [new.rstrip("\n"), "",
       "# ---------------------------------------------------------------- DRY-RUN overrides",
       "# Every stage runs. Terraform plans are real (read-only role); apply jobs plan and stop;",
       "# build, publish, deploy, test and cutover jobs print the commands they would run.", ""]

# 2. Plans (staging + production) and validate: make sure they run on main.
for k in sorted(real):
    if "plan" in k and "staging" not in k:
        out += [f"{k}:", "  rules:", "    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", ""]

# 3. Apply jobs: keep their AWS sign-in and init, replace apply with a real plan.
for k in apply_jobs:
    refs = [x for x in (g[k].get("script") or []) if isinstance(x, Ref) and "init" in "".join(x)]
    lines = [f"    - {item(r)}" for r in refs]
    lines += ["    - " + json.dumps("terraform plan -input=false -out=tfplan-dryrun"),
              "    - " + json.dumps("echo " + q("DRY RUN - terraform apply skipped. The plan above is exactly what " + k + " would apply."))]
    out += [f"{k}:", "  rules:", "    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", "  allow_failure: false",
            "  artifacts:", "    paths: [tfplan-dryrun]", "    expire_in: 1 day", "  script:", *lines, ""]

# Cutover steps run in order: each one needs the previous step (one human click per step).
cut = sorted([k for k in jobs if "cutover" in k and k not in rollback and re.search(r"\d+", k)],
             key=lambda k: int(re.search(r"(\d+)", k).group(1)))
prev_step = {cut[i]: cut[i - 1] for i in range(1, len(cut))}

# 4. Everything else: run, print what would happen, change nothing.
for k in jobs:
    if k in real or k in apply_jobs:
        continue
    stage = g[k].get("stage", "")
    cmds = script_lines(k)[:8] or ["(commands come from a template)"]
    echo = ["    - " + json.dumps("echo " + q("DRY RUN [" + stage + "] " + k + " - nothing is built, uploaded, deployed or routed.")),
            "    - " + json.dumps("echo " + q("Commands this job would run -"))]
    echo += ["    - " + json.dumps("echo " + q("  " + c.strip().splitlines()[0][:160])) for c in cmds]
    if k in rollback:
        rules = ["    - when: on_failure"]
    elif "cutover" in k:   # traffic steps stay human-approved, one click per step, even in a dry run
        rules = ["    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH", "      when: manual", "  allow_failure: false"]
        if k in prev_step:
            rules += [f"  needs: [{prev_step[k]}]"]
    else:
        rules = ["    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"]
    out += [f"{k}:", "  image: alpine:3.20", "  before_script: []", "  after_script: []", "  services: []",
            "  dependencies: []", "  cache: []", "  rules:", *rules, "  script:", *echo, ""]

print("\n".join(out).rstrip() + "\n")
print("real: " + ", ".join(sorted(real)), file=sys.stderr)
print("apply (dry-run plan): " + ", ".join(apply_jobs), file=sys.stderr)
print("simulated: " + ", ".join(k for k in jobs if k not in real and k not in apply_jobs), file=sys.stderr)
