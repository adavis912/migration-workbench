"""Copy every AWS Transform (atx) scan job where the dashboard can read it, tagged with its portfolio app.

Runs as root from capstone-scan-sync.timer every 2 minutes.
Source: each job under /home/ssm-user/.aws/atx/custom/<job>/ plus the files the scan committed to its result
branch (atx-result-staging-<job>) in the scanned repo. The repo -> app mapping comes from apps.json.
Target: /opt/capstone/scans/results/<job>/ with scan.json (app, repo, transformation), status.json, artifacts/,
repo-changes/ and repo-commits.txt. Jobs whose repo is not in apps.json are skipped (never guessed).
"""
import glob, json, os, re, shutil, subprocess, sys, time

SRC_ROOT = "/home/ssm-user/.aws/atx/custom"
OUT_ROOT = "/opt/capstone/scans/results"
REGISTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apps.json")
TEXT = (".md", ".json", ".html", ".txt", ".csv", ".mmd")
MAX = 2 * 1024 * 1024
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def git(repo, *args):
    r = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", repo, *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def tail(path, n):
    try:
        with open(path, errors="replace") as f:
            return [ANSI.sub("", l.rstrip())[:240] for l in f.readlines()[-n:] if l.strip()]
    except OSError:
        return []


def find_repo(job_dir, job_id, registry):
    for repo in registry:   # 1) the scan's own result branch exists in this repo
        if os.path.isdir(os.path.join(repo, ".git")) and git(repo, "rev-parse", "--verify", "-q", f"atx-result-staging-{job_id}"):
            return repo
    blob = ""               # 2) job still running: the repo path appears in its metadata or logs
    for p in [os.path.join(job_dir, "metadata.json")] + glob.glob(os.path.join(job_dir, "logs", "*.log")):
        try:
            blob += open(p, errors="replace").read(400_000)
        except OSError:
            pass
    return next((r for r in registry if r in blob), None)


def sync_job(job_dir, registry, running_cmds):
    job_id = os.path.basename(job_dir.rstrip("/"))
    repo = find_repo(job_dir, job_id, registry)
    if not repo:
        return None
    reg = registry[repo]
    out = os.path.join(OUT_ROOT, job_id)
    os.makedirs(os.path.join(out, "artifacts"), exist_ok=True)
    os.makedirs(os.path.join(out, "repo-changes"), exist_ok=True)
    art = os.path.join(job_dir, "artifacts")
    for dp, _, fs in os.walk(art):
        for f in fs:
            p = os.path.join(dp, f)
            if f.lower().endswith(TEXT) and os.path.getsize(p) < MAX:
                dst = os.path.join(out, "artifacts", os.path.relpath(p, art))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(p, dst)
    branch = f"atx-result-staging-{job_id}"
    commits = ""
    if git(repo, "rev-parse", "--verify", "-q", branch):
        commits = git(repo, "log", "--oneline", f"{reg['base_commit']}..{branch}") or ""
        for f in (git(repo, "diff", "--name-only", reg["base_commit"], branch) or "").split("\n"):
            if not f.strip():
                continue
            data = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", repo, "show", f"{branch}:{f}"], capture_output=True).stdout
            if data and len(data) < MAX:
                dst = os.path.join(out, "repo-changes", f)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, "wb").write(data)
    open(os.path.join(out, "repo-commits.txt"), "w").write(commits)
    try:
        meta = json.load(open(os.path.join(job_dir, "metadata.json")))
    except Exception:
        meta = {}
    logs = sorted(glob.glob(os.path.join(job_dir, "logs", "*.log")), key=os.path.getmtime)
    running = any(repo in c for c in running_cmds) and not commits
    transformation = next((m for m in re.findall(r"AWS/[\w-]+", json.dumps(meta))), None) or reg.get("transformation", "")
    if not transformation:
        for l in logs:
            m = re.search(r"AWS/[a-z0-9-]+", open(l, errors="replace").read(200_000))
            if m:
                transformation = m.group(0)
                break
    json.dump({"job": job_id, "app": reg["app"], "repo": reg["repo"], "repo_url": reg.get("repo_url", ""),
               "commit": reg["base_commit"], "branch": branch, "note": reg.get("note", ""),
               "transformation": transformation or "AWS Transform custom", "tool": "AWS Transform custom CLI (atx)",
               "mode": "Static analysis only (build command: noop). Results are committed to a separate branch; main is not changed.",
               "price_per_min": 0.035}, open(os.path.join(out, "scan.json"), "w"), indent=1)
    json.dump({"job": job_id, "running": running,
               "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getctime(job_dir))),
               "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max([os.path.getmtime(job_dir)] + [os.path.getmtime(l) for l in logs]))),
               "conversation_tail": tail(logs[-1], 40) if logs else [], "metadata": meta},
              open(os.path.join(out, "status.json"), "w"), indent=1)
    return out


def main():
    registry = {k: v for k, v in json.load(open(REGISTRY)).items() if not k.startswith("_")}
    ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True).stdout.split("\n")
    running_cmds = [c for c in ps if "atx custom def exec" in c]
    done = [o for j in sorted(glob.glob(os.path.join(SRC_ROOT, "*/"))) if (o := sync_job(j, registry, running_cmds))]
    if done:
        latest = os.path.join(OUT_ROOT, "latest")
        if os.path.islink(latest) or not os.path.exists(latest):
            tmp = latest + ".new"
            if os.path.lexists(tmp):
                os.remove(tmp)
            os.symlink(max(done, key=os.path.getmtime), tmp)
            os.replace(tmp, latest)
    subprocess.run(["chmod", "-R", "a+rX", OUT_ROOT])
    print(f"synced {len(done)} scan job(s)")


if __name__ == "__main__":
    sys.exit(main())
