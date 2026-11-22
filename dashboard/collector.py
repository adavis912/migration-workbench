"""Builds the live status the pipeline page shows: GitLab pipelines, Wave 1 run records, policy re-checks.

Runs inside the dashboard server every 5 minutes. Every source is optional: if GitLab or a record is
unavailable, that part falls back to the last good value instead of breaking the page.
Secrets (GitLab read token, Eraser API token) are read from Secrets Manager and never logged.
"""
import csv, glob, json, os, sys, time, urllib.parse, urllib.request

import boto3
import yaml

ROOT = os.environ.get("CAPSTONE", "/opt/capstone/capstone_kb")
FLOW = os.path.join(ROOT, "kb_aws", "migration_flow")
PORTFOLIO = os.path.join(ROOT, "synthetic_portfolio")
RUNS = os.path.join(FLOW, "runs", "wave-1")
REGION = os.environ.get("AWS_REGION", "us-east-1")
GITLAB = "https://gitlab.com"
# app_id -> GitLab project path. Apps not listed have not been pushed yet.
PROJECTS = {"CLM-FRAU1": "adavis912-group/capstone-migration-pilot",
            "CS-SURV2": "adavis912-group/survey-service-sample",
            "FIN-RPT-LEGACY": "adavis912-group/finance-reporting-sample"}
PILOT_MODE = {"CLM-FRAU1": "Pilot: plan only (nothing is applied or deployed)",
              "CS-SURV2": "Dry run: every stage runs; nothing is applied, deployed or routed",
              "FIN-RPT-LEGACY": "Dry run: every stage runs; nothing is applied, deployed or routed"}

sys.path.insert(0, FLOW)
import policy  # noqa: E402
from target_spec import build_spec  # noqa: E402

STAGES = [
    ("source_scan", "Source scan", "Stage 1 · Source gates"), ("build", "Build", "Stage 2 · Build profile"),
    ("publish", "Publish", "Stage 3 · Artifact"), ("infra_validate", "Infra validate", "Stage 4a · Terraform"),
    ("infra_plan", "Infra plan", "Stage 4b · Terraform"), ("infra_apply", "Infra apply", "Stage 4c · Terraform"),
    ("deploy", "Deploy", "Stage 4d · EC2 rehydration (SSM)"), ("test", "Test", "Stage 5 · Against staging"),
    ("cutover", "Cutover", "Stage 6 · Weighted Route 53")]
GATE_JOBS = {"gitleaks", "semgrep", "checkov", "njsscan", "build", "build_java", "publish_scan", "trivy_scan_artifact",
             "trivy_bundle_scan", "infra_validate", "tf_validate", "terraform_validate", "integration_tests", "zap_dast",
             "dast_zap", "k6_perf", "perf_k6"}
SECURITY_GATES = [  # (gate, tool, stage label, job names)
    ("Secret scanning", "Gitleaks", "Source scan", ["gitleaks"]),
    ("Static code analysis (SAST)", "Semgrep", "Source scan", ["semgrep"]),
    ("Infrastructure-as-code scan", "Checkov", "Source scan", ["checkov"]),
    ("Dependency scan (SCA) / package check", "Build profile tools", "Build", ["build", "build_java"]),
    ("Artifact vulnerability scan", "Trivy", "Publish", ["publish_scan", "trivy_scan_artifact", "trivy_bundle_scan"]),
    ("Terraform validate", "Terraform fmt + validate", "Infra validate", ["infra_validate", "tf_validate", "terraform_validate"]),
    ("Terraform plan (staging)", "GitLab OIDC → read-only plan role", "Infra plan", ["infra_plan_staging", "tf_plan_staging", "plan_staging"]),
    ("Dynamic scan (DAST)", "OWASP ZAP baseline", "Test", ["zap_dast", "dast_zap"]),
    ("Performance test", "k6", "Test", ["k6_perf", "perf_k6"]),
    ("Cutover health alarm", "CloudWatch + Route 53", "Cutover", ["cutover_5", "cutover_25", "cutover_100", "cutover_5pct", "cutover_25pct", "cutover_100pct"])]
POLICY_TXT = {
    "ALB-TLS": "Load balancer is not TLS end to end. Rebuild with the updated Builder.",
    "CACHE-AUTH": "Valkey cache has no client authentication. Rebuild with IAM sign-in.",
    "CI-SCHEMA": "A job needs a job from a later stage, or a script line is not a string; GitLab would reject the full pipeline."}

_secrets = boto3.client("secretsmanager", region_name=REGION)


def secret(name):
    """Secret value or None. A JSON secret returns its first value."""
    try:
        s = _secrets.get_secret_value(SecretId=name)["SecretString"].strip()
    except Exception:
        return None
    try:
        d = json.loads(s)
        return next(iter(d.values())) if isinstance(d, dict) else s
    except ValueError:
        return s


def gitlab(path, token):
    req = urllib.request.Request(GITLAB + "/api/v4" + path, headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


class _Loader(yaml.SafeLoader):
    pass


_Loader.add_constructor("!reference", lambda l, n: l.construct_sequence(n))


def ci_jobs(ci_text):
    """[(job, stage, manual)] from a generated .gitlab-ci.yml, following extends for the stage."""
    c = yaml.load(ci_text, Loader=_Loader) or {}
    out = []
    for k, v in c.items():
        if not isinstance(v, dict) or k.startswith(".") or k in ("variables", "workflow", "default", "stages"):
            continue
        ext = [v["extends"]] if isinstance(v.get("extends"), str) else (v.get("extends") or [])
        stage = v.get("stage") or next((c.get(e, {}).get("stage") for e in ext if c.get(e, {}).get("stage")), "test")
        rules = str(v.get("rules", "")) + str(v.get("when", "")) + "".join(str(c.get(e, {}).get("rules", "")) for e in ext)
        out.append((k, stage, "manual" in rules))
    return out


def files_of(app):
    d = os.path.join(RUNS, "apps", app)
    return {n: open(os.path.join(d, n)).read() for n in os.listdir(d) if os.path.isfile(os.path.join(d, n))} if os.path.isdir(d) else {}


def inventory():
    D = PORTFOLIO + "/data/"
    A = {r["app_id"]: r for r in csv.DictReader(open(D + "applications.csv"))}
    S = list(csv.DictReader(open(D + "servers.csv")))
    DB = list(csv.DictReader(open(D + "databases.csv")))
    return {a: {"app_id": a, "found": True, "profile": dict(p, flags=[]), "servers": [s for s in S if s["app_id"] == a],
                "databases": [d for d in DB if d["app_id"] == a]} for a, p in A.items()}


def _glpat(v):
    import re as _re
    m = _re.search(r"glpat-[A-Za-z0-9_.-]+", v or "")
    return m.group(0) if m else (v or "").strip() or None


def _samples_token():
    """Group token for the sample projects (fallback when the read token does not cover a project)."""
    import re as _re
    v = secret("capstone/dashboard-gitlab-samples-token") or ""
    m = _re.search(r"glpat-[A-Za-z0-9_.-]+", v)
    return m.group(0) if m else None


def _gl(path, tokens):
    last = None
    for tok in tokens:
        try:
            return gitlab(path, tok)
        except Exception as e:  # 401/403/404 with this token: try the next one
            last = e
    raise last


_DONE = ("success", "failed", "canceled", "skipped")
_RUN_CACHE = {}     # (project, pipeline id) -> run row, only for finished pipelines (they never change)
_COMMITS = {}       # sha -> commit title
_PLANS = {}         # plan job id -> {"add","change","destroy"}


def _commit_title(q, sha, tokens):
    if sha not in _COMMITS:
        _COMMITS[sha] = _gl(f"/projects/{q}/repository/commits/{sha}", tokens)["title"]
    return _COMMITS[sha]


def live_detail(app, proj, me, tokens):
    """Latest pipeline, stage/job grid, recent runs and gate table for one app's GitLab project."""
    q = urllib.parse.quote(proj, safe="")
    pipes = _gl(f"/projects/{q}/pipelines?per_page=8", tokens)
    if not pipes:
        return None
    latest = _gl(f"/projects/{q}/pipelines/{pipes[0]['id']}", tokens)
    jobs = {j["name"]: j for j in _gl(f"/projects/{q}/pipelines/{pipes[0]['id']}/jobs?per_page=100", tokens)}
    commit = {"title": _commit_title(q, latest["sha"], tokens)}
    status = {"success": "passed", "failed": "failed", "skipped": "skipped", "running": "running",
              "pending": "queued", "manual": "manual", "created": "queued", "canceled": "failed"}
    defined = ci_jobs(files_of(app).get(".gitlab-ci.yml", "")) or [(n, j["stage"], False) for n, j in jobs.items()]
    stages = []
    for key, name, std in STAGES:
        js = []
        for n, st, manual in defined:
            if st != key:
                continue
            j = jobs.get(n)
            js.append({"n": n, "s": status.get(j["status"], "pending") if j else "off", "d": round(j["duration"]) if j and j.get("duration") else None,
                       "id": j["id"] if j else None, "gate": n in GATE_JOBS, "manual": manual})
        stages.append({"key": key, "name": name, "std": std, "jobs": js})
    runs = []
    for p in pipes:
        key = (proj, p["id"])
        if key in _RUN_CACHE:
            runs.append(_RUN_CACHE[key])
            continue
        pj = list(jobs.values()) if p["id"] == latest["id"] else _gl(f"/projects/{q}/pipelines/{p['id']}/jobs?per_page=100", tokens)
        bad = [j["name"] for j in pj if j["status"] == "failed"]
        running = p["status"] not in _DONE
        why = ("Failed: " + ", ".join(bad)) if bad else ("Waiting for a person to approve the next cutover step." if p["status"] in ("manual", "blocked") else ("GitLab rejected the pipeline file." if not pj and p["status"] == "failed"
                                                         else f"Running: {sum(j['status'] == 'success' for j in pj)} of {len(pj)} jobs done." if running
                                                         else f"{sum(j['status'] == 'success' for j in pj)} jobs passed."))
        row = {"id": p["id"], "s": "passed" if p["status"] == "success" else ("failed" if p["status"] in ("failed", "canceled")
                                                                         else "pending" if p["status"] in ("manual", "blocked") else "running"),
               "t": p["created_at"], "msg": _commit_title(q, p["sha"], tokens), "why": why}
        if not running:
            _RUN_CACHE[key] = row
        runs.append(row)
    gates = [{"group": "Security gates"}]
    for g, tool, stage, names in SECURITY_GATES:
        found = [jobs[n] for n in names if n in jobs]
        s = ("failed" if any(j["status"] == "failed" for j in found) else "passed" if found and all(j["status"] == "success" for j in found)
             else "running" if any(j["status"] == "running" for j in found) else "queued" if any(j["status"] in ("pending", "created") for j in found)
             else "skipped" if found else "off")
        detail = "; ".join(f"{j['name']} {j['status']}" + (f" in {round(j['duration'])}s" if j.get("duration") else "") for j in found) or "Not run in this pipeline."
        gates.append({"name": g, "tool": tool, "stage": stage, "s": s, "detail": detail})
    approvals_dir = os.path.join(RUNS, "approvals", "owners", "wave-1")
    gates += [{"group": "Review and approval gates"},
              {"name": "Agent review: policy checks", "tool": "Reviewer agent · policy-as-code", "stage": "Before commit",
               "s": "failed" if me["fails"] else "passed",
               "detail": f"{me['policy'][0]} of {me['policy'][1]} pass" + (f"; fails {', '.join(me['fails'])}." if me["fails"] else ".")},
              {"name": "Wave approval", "tool": "Migration lead", "stage": "Before build",
               "s": "passed" if os.path.exists(os.path.join(RUNS, "approvals", "wave-1.json")) else "pending", "detail": "Wave 1 plan approval."},
              {"name": "App owner: build approval", "tool": me["owner"], "stage": "Before build",
               "s": "passed" if me.get("owner_build") else "pending", "detail": "Approval for the built target spec."},
              {"name": "App owner: cutover approval", "tool": me["owner"], "stage": "Cutover",
               "s": "passed" if os.path.exists(os.path.join(approvals_dir, f"{app}.cutover.json")) else "pending", "detail": "Required before any traffic moves."}]
    plan = None
    pjob = next((jobs[n] for n in ("infra_plan_staging", "tf_plan_staging", "plan_staging") if n in jobs and jobs[n]["status"] == "success"), None)
    if pjob and pjob["id"] in _PLANS:
        plan = _PLANS[pjob["id"]]
    elif pjob:
        try:
            req = urllib.request.Request(f"{GITLAB}/api/v4/projects/{q}/jobs/{pjob['id']}/trace", headers={"PRIVATE-TOKEN": tokens[0]})
            for tok in tokens:
                req = urllib.request.Request(f"{GITLAB}/api/v4/projects/{q}/jobs/{pjob['id']}/trace", headers={"PRIVATE-TOKEN": tok})
                try:
                    with urllib.request.urlopen(req, timeout=15) as r:
                        tr = r.read().decode("utf-8", "replace")
                    break
                except Exception:
                    tr = ""
            import re as _re
            tr = _re.sub(r"\x1b\[[0-9;]*m", "", tr)   # terraform colours its summary line
            m = _re.search(r"Plan: (\d+) to add, (\d+) to change, (\d+) to destroy", tr)
            if m:
                plan = {"add": int(m.group(1)), "change": int(m.group(2)), "destroy": int(m.group(3))}
                _PLANS[pjob["id"]] = plan
        except Exception:
            plan = None
    st = latest["status"]
    return {"pipeline": {"id": latest["id"], "status": "passed" if st == "success" else "failed" if st in ("failed", "canceled")
                         else "pending" if st in ("manual", "blocked") else "running",
                         "sha": latest["sha"][:8], "branch": latest["ref"], "duration": latest.get("duration") or 0,
                         "finished": latest.get("finished_at") or latest["created_at"], "commit": commit["title"]},
            "stages": stages, "gates": gates, "runs": runs, "mode": PILOT_MODE.get(app, ""), "plan": plan,
            "base": f"{GITLAB}/{proj}"}


def build(snapshot):
    """Return {'data': ..., 'apps': ...} in the page's shape, starting from the last good snapshot."""
    inv = inventory()
    data = json.loads(json.dumps(snapshot["data"]))
    apps = []
    approvals_dir = os.path.join(RUNS, "approvals", "owners", "wave-1")
    for f in sorted(glob.glob(os.path.join(RUNS, "records", "*.json"))):
        rec = json.load(open(f))
        a = rec.get("app_id") or os.path.basename(f)[:-5]
        if a not in inv:
            continue
        prof = inv[a]["profile"]
        spec = build_spec(inv[a])
        fs = files_of(a)
        fails, ran = policy.check(spec, fs) if fs else (["no files"], [])
        fail_ids = sorted({x.split(":")[0] for x in fails})
        counts = {}
        for _, st, _ in ci_jobs(fs[".gitlab-ci.yml"]) if ".gitlab-ci.yml" in fs else []:
            counts[st] = counts.get(st, 0) + 1
        runtime = prof.get("app_runtime", "")
        if prof.get("cots_product"):
            runtime = f"{prof['cots_product']} on {runtime}"
        apps.append({"id": a, "name": prof["app_name"], "bu": prof["business_unit"], "owner": rec.get("owner") or prof["owner"],
                     "profile": policy.build_profile(spec), "runtime": runtime,
                     "wave": "held" if rec.get("stage") in ("held", "blocked") else "ready",
                     "hold": rec.get("hold_reason") or "", "policy": [len(ran) - len(fail_ids), len(ran)], "fails": fail_ids,
                     "owner_build": os.path.exists(os.path.join(approvals_dir, f"{a}.build.json")),
                     "jobs": counts})

    if not apps:  # no run records on this machine: keep the last good app list
        apps = json.loads(json.dumps(snapshot["apps"]))
    # Live GitLab detail for every app that has a project. The pilot fills the page's main data; the others go in data["live"].
    tokens = [x for x in (_glpat(secret("capstone/gitlab-read-token")), _samples_token()) if x]
    app = "CLM-FRAU1"
    for a, proj in PROJECTS.items():
        me = next((x for x in apps if x["id"] == a), None)
        if not me or not tokens:
            continue
        try:
            d = live_detail(a, proj, me, tokens)
        except Exception as e:  # GitLab unreachable or no access: keep the page working
            print(f"collector: live detail for {a} skipped: {type(e).__name__}", file=sys.stderr)
            continue
        if not d:
            continue
        if a == app:
            plan = d.pop("plan", None)
            data.update(d)
            if plan:
                data["plan"] = plan
        else:
            data.setdefault("live", {})[a] = d
    data["issues"] = [[f + ".", POLICY_TXT.get(f, "See the policy report.")] for f in next((x for x in apps if x["id"] == app), {"fails": []})["fails"]] + \
        [i for i in snapshot["data"]["issues"] if not i[0].startswith("Stage order")]
    data["asOf"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return {"data": data, "apps": apps}


if __name__ == "__main__":
    snap = json.load(open(os.path.join(os.path.dirname(__file__), "snapshot.json")))
    out = build(snap)
    print(json.dumps({"apps": [(a["id"], a["policy"], a["fails"], a["wave"]) for a in out["apps"]],
                      "pipeline": out["data"]["pipeline"], "asOf": out["data"]["asOf"]}, indent=1))
