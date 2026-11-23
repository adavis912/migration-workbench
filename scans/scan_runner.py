"""Start AWS Transform scans that were requested from the dashboard. Runs as root every minute (capstone-scan-runner.timer).

For each queued request (oldest first) it re-checks everything the web server checked, then starts atx as ssm-user in
its own systemd unit (capstone-atx-<id>), so the scan keeps running after this script exits. One scan at a time.
The result appears on the Assessment Phase page through the normal sync (sync_scan_results.py).
"""
import datetime as dt, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE = "/var/lib/capstone-dashboard/scan-queue"
STATUS = "/var/lib/capstone-dashboard/scan-status"
REGISTRY = os.environ.get("SCAN_REGISTRY", os.path.join(HERE, "apps.json"))
LOGDIR = "/opt/capstone/scans"
USER, HOME = "ssm-user", "/home/ssm-user"
ATX = f"{HOME}/.local/bin/atx"
ALLOWED = {"AWS/comprehensive-codebase-analysis", "AWS/modernization-readiness-analysis", "AWS/agentic-readiness-analysis"}
DAILY_LIMIT = int(os.environ.get("SCAN_DAILY_LIMIT", "5"))


def write_status(req, state, **extra):
    os.makedirs(STATUS, exist_ok=True)
    os.chmod(STATUS, 0o755)
    req = dict(req, state=state, updated_at=dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), **extra)
    tmp = os.path.join(STATUS, f".{req['id']}.tmp")
    json.dump(req, open(tmp, "w"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, os.path.join(STATUS, f"{req['id']}.json"))
    return req


def atx_running():
    return subprocess.run(["pgrep", "-f", "atx custom def exec"], capture_output=True).returncode == 0


def as_user(*cmd, cwd=None):
    return subprocess.run(["runuser", "-u", USER, "--", *cmd], cwd=cwd, capture_output=True, text=True)


LEDGER = "/var/lib/capstone-scan-runner/started.log"   # root-only: the web service cannot edit the daily count


def started_today():
    today = dt.datetime.utcnow().strftime("%Y-%m-%d")
    try:
        return sum(1 for l in open(LEDGER) if l.startswith(today))
    except OSError:
        return 0


def record_start(req, unit):
    os.makedirs(os.path.dirname(LEDGER), mode=0o700, exist_ok=True)
    with open(LEDGER, "a") as f:
        f.write(f"{dt.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} {req['id']} {req['app']} {req['transformation']} {unit}\n")


def refresh_running():
    """Mark started scans finished once their unit has stopped."""
    for f in os.listdir(STATUS) if os.path.isdir(STATUS) else []:
        p = os.path.join(STATUS, f)
        try:
            r = json.load(open(p))
        except Exception:
            continue
        if r.get("state") == "running" and r.get("unit"):
            active = subprocess.run(["systemctl", "is-active", "--quiet", r["unit"]]).returncode == 0
            if not active:
                res = subprocess.run(["systemctl", "show", "-p", "Result", "--value", r["unit"]], capture_output=True, text=True).stdout.strip()
                write_status(r, "finished" if res in ("success", "") else "failed", result=res or "unit gone")


def main():
    refresh_running()
    if not os.path.isdir(QUEUE):
        return 0
    reqs = sorted(f for f in os.listdir(QUEUE) if f.endswith(".json"))
    if not reqs:
        return 0
    reg = {v["app"]: k for k, v in json.load(open(REGISTRY)).items() if not k.startswith("_") and isinstance(v, dict)}
    for name in reqs:
        path = os.path.join(QUEUE, name)
        try:
            req = json.load(open(path))
            assert isinstance(req, dict) and req.get("id") and req["id"] + ".json" == name
        except Exception:
            os.remove(path)
            continue
        app, tr = req.get("app"), req.get("transformation")
        reason = None
        if app not in reg:
            reason = "app has no registered repository"
        elif tr not in ALLOWED:
            reason = "transformation not allowed"
        elif started_today() >= DAILY_LIMIT:
            reason = f"daily limit of {DAILY_LIMIT} scans reached"
        if reason:
            write_status(req, "rejected", reason=reason)
            os.remove(path)
            continue
        if atx_running():
            write_status(req, "queued", reason="waiting for the current scan to finish")
            return 0   # leave it queued; try again next minute
        repo = reg[app]
        # Scan the default branch: a previous scan leaves the repo on its result branch.
        branch = (as_user("git", "-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").stdout.strip() or "origin/main").split("/", 1)[-1]
        co = as_user("git", "-C", repo, "checkout", "-q", branch)
        dirty = as_user("git", "-C", repo, "status", "--porcelain").stdout.strip()
        if co.returncode != 0 or dirty:
            write_status(req, "rejected", reason="repository is not clean on its default branch")
            os.remove(path)
            continue
        unit = f"capstone-atx-{req['id']}"
        log = os.path.join(LOGDIR, f"scan-{app}-{req['id']}.log")
        r = subprocess.run(["systemd-run", f"--unit={unit}", f"--uid={USER}", f"--gid={USER}", "--collect",
                            f"--working-directory={repo}", f"--setenv=HOME={HOME}", "--setenv=AWS_REGION=us-east-1",
                            "--setenv=AWS_DEFAULT_REGION=us-east-1", f"--setenv=PATH={HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin",
                            "-p", f"StandardOutput=append:{log}", "-p", f"StandardError=append:{log}", "-p", "RuntimeMaxSec=4h",
                            ATX, "custom", "def", "exec", "-n", tr, "-p", repo, "-c", "noop", "-x", "-t"],
                           capture_output=True, text=True)
        os.remove(path)
        if r.returncode != 0:
            write_status(req, "failed", reason=(r.stderr or r.stdout).strip()[:300])
        else:
            record_start(req, unit)
            write_status(req, "running", unit=unit, log=log, started_at=dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
