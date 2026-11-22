"""GitHub Actions provider for the GitHub edition of the workbench.

Turns a workflow's latest run into the same shape collector.live_detail() returns for GitLab, so the
App Pipelines page shows GitHub runs with no other change. Read-only: it only calls GET endpoints.
The read token comes from Secrets Manager (capstone-gh/github-read-token) and is never logged.
"""
import json, re, urllib.request

API = "https://api.github.com"
WEB = "https://github.com"
REPO = "adavis912/migration-pilots"
# app_id -> workflow file in REPO. Apps not listed have no GitHub pipeline yet.
WORKFLOWS = {"CS-SURV2": "cs-surv2.yml", "FIN-RPT-LEGACY": "fin-rpt-legacy.yml"}
MODE = "Dry run on GitHub Actions: every stage runs; nothing is applied, deployed or routed"

# Every job the generated workflow defines, in order, with its stage and whether a person must approve it.
JOBS = [("gitleaks", "source_scan", False), ("semgrep", "source_scan", False), ("checkov", "source_scan", False),
        ("build_java", "build", False),
        ("trivy_scan_artifact", "publish", False), ("publish_artifact", "publish", False),
        ("tf_validate", "infra_validate", False),
        ("tf_plan_staging", "infra_plan", False), ("tf_plan_production", "infra_plan", False),
        ("tf_apply_staging", "infra_apply", False), ("tf_apply_production", "infra_apply", False),
        ("deploy_staging", "deploy", False), ("deploy_production", "deploy", False),
        ("integration_tests", "test", False), ("dast_zap", "test", False), ("perf_k6", "test", False),
        ("cutover_5pct", "cutover", True), ("cutover_25pct", "cutover", True), ("cutover_100pct", "cutover", True),
        ("rollback_auto", "cutover", False), ("rollback_manual", "cutover", False)]

_TOKEN_RE = re.compile(r"(github_pat_[A-Za-z0-9_]+|ghp_[A-Za-z0-9]+)")
_DONE_RUNS = {}   # run id -> run row, finished runs only
_PLANS = {}       # plan job id -> {"add","change","destroy"}


def token_from(v):
    m = _TOKEN_RE.search(v or "")
    return m.group(1) if m else None


def _get(path, token):
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "migration-workbench"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=h), timeout=15) as r:
        return json.load(r)


def _job_status(j):
    if not j:
        return None
    s, c = j.get("status"), j.get("conclusion")
    if s == "completed":
        return {"success": "passed", "failure": "failed", "cancelled": "failed", "timed_out": "failed",
                "skipped": "skipped", "neutral": "passed"}.get(c, "failed")
    if s == "in_progress":
        return "running"
    if s == "waiting":
        return "manual"          # waiting for a required reviewer on its environment
    return "queued"              # queued, pending, requested


def _run_status(r):
    if r["status"] == "completed":
        return "passed" if r.get("conclusion") == "success" else "failed"
    if r["status"] == "waiting":
        return "pending"         # shown as "Awaiting approval"
    return "running"


def _secs(a, b):
    import datetime as dt
    try:
        f = lambda x: dt.datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ")
        return max(0, round((f(b) - f(a)).total_seconds()))
    except Exception:
        return None


def _plan(job, token):
    if not job or _job_status(job) != "passed":
        return None
    if job["id"] in _PLANS:
        return _PLANS[job["id"]]
    try:
        for a in _get(f"/repos/{REPO}/check-runs/{job['id']}/annotations", token):
            m = re.search(r"Plan: (\d+) to add, (\d+) to change, (\d+) to destroy", a.get("message", ""))
            if m:
                _PLANS[job["id"]] = {"add": int(m.group(1)), "change": int(m.group(2)), "destroy": int(m.group(3))}
                return _PLANS[job["id"]]
    except Exception:
        pass
    return None


def live_detail(app, me, token, security_gates, gate_jobs, stages_meta):
    wf = WORKFLOWS.get(app)
    if not wf:
        return None
    runs = _get(f"/repos/{REPO}/actions/workflows/{wf}/runs?per_page=8", token).get("workflow_runs", [])
    if not runs:
        return None
    latest = runs[0]
    jl = _get(f"/repos/{REPO}/actions/runs/{latest['id']}/jobs?per_page=100", token).get("jobs", [])
    jobs = {j["name"]: j for j in jl}
    active = latest["status"] != "completed"
    stages = []
    for key, name, std in stages_meta:
        js = []
        for n, st, manual in JOBS:
            if st != key:
                continue
            j = jobs.get(n)
            s = _job_status(j) or ("queued" if active else "off")
            d = _secs(j.get("started_at"), j.get("completed_at")) if j and j.get("completed_at") else None
            js.append({"n": n, "s": s, "d": d, "id": j["id"] if j else None, "url": j.get("html_url") if j else None,
                       "gate": n in gate_jobs, "manual": manual})
        stages.append({"key": key, "name": name, "std": std, "jobs": js})

    rows = []
    for r in runs:
        if r["id"] in _DONE_RUNS:
            rows.append(_DONE_RUNS[r["id"]])
            continue
        rj = jl if r["id"] == latest["id"] else _get(f"/repos/{REPO}/actions/runs/{r['id']}/jobs?per_page=100", token).get("jobs", [])
        bad = [j["name"] for j in rj if _job_status(j) == "failed"]
        ok = sum(_job_status(j) == "passed" for j in rj)
        s = _run_status(r)
        why = ("Failed: " + ", ".join(bad)) if bad else ("Waiting for a person to approve the next cutover step." if s == "pending"
               else f"Running: {ok} of {len(rj)} jobs done." if s == "running" else f"{ok} jobs passed.")
        row = {"id": r["id"], "s": s, "t": r["created_at"], "msg": r.get("display_title") or "", "why": why, "url": r["html_url"]}
        if r["status"] == "completed":
            _DONE_RUNS[r["id"]] = row
        rows.append(row)

    gates = [{"group": "Security gates"}]
    for g, tool, stage, names in security_gates:
        tool = tool.replace("GitLab OIDC", "GitHub OIDC")
        found = [jobs[n] for n in names if n in jobs]
        ss = [_job_status(j) for j in found]
        s = ("failed" if "failed" in ss else "passed" if found and all(x == "passed" for x in ss)
             else "running" if "running" in ss else "manual" if "manual" in ss else "queued" if "queued" in ss
             else "skipped" if found else ("queued" if active else "off"))
        detail = "; ".join(f"{j['name']} {_job_status(j)}" for j in found) or ("Not reached yet in this run." if active else "Not run in this run.")
        gates.append({"name": g, "tool": tool, "stage": stage, "s": s, "detail": detail})
    gates += [{"group": "Review and approval gates"},
              {"name": "Agent review: policy checks", "tool": "Reviewer agent · policy-as-code", "stage": "Before commit",
               "s": "failed" if me["fails"] else "passed",
               "detail": f"{me['policy'][0]} of {me['policy'][1]} pass" + (f"; fails {', '.join(me['fails'])}." if me["fails"] else ".")},
              {"name": "Cutover approvals", "tool": "GitHub environment · required reviewer", "stage": "Cutover",
               "s": "manual" if any(_job_status(jobs.get(n)) == "manual" for n in ("cutover_5pct", "cutover_25pct", "cutover_100pct")) else
                    "passed" if _job_status(jobs.get("cutover_100pct")) == "passed" else "pending",
               "detail": "Each traffic step waits for a reviewer to approve its environment in GitHub."}]

    plan = _plan(jobs.get("tf_plan_staging"), token)
    dur = _secs(latest.get("run_started_at") or latest["created_at"], latest["updated_at"]) or 0
    return {"pipeline": {"id": latest["id"], "status": _run_status(latest), "sha": latest["head_sha"][:8],
                         "branch": latest.get("head_branch") or "", "duration": dur, "finished": latest["updated_at"],
                         "commit": latest.get("display_title") or "", "url": latest["html_url"],
                         "commitUrl": f"{WEB}/{REPO}/commit/{latest['head_sha']}"},
            "stages": stages, "gates": gates, "runs": rows, "mode": MODE, "plan": plan, "base": f"{WEB}/{REPO}"}
