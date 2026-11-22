"""Data for the Assessment Phase page: AWS Transform code-scan status and generated documents.

Reads what scans/sync_scan_results.sh copies to /opt/capstone/scans/results/latest. Markdown documents are
rendered to HTML here with everything escaped first, so scan output can never inject markup or script.
"""
import html, json, os, re

RESULTS_ROOT = os.environ.get("SCAN_RESULTS_ROOT", "/opt/capstone/scans/results")
JOB_RE = re.compile(r"^[\w.-]+$")
# Scans synced before scan.json existed were all of the claims-intake sample, the stand-in code for CLM-INTAKE.
LEGACY = {"app": "CLM-INTAKE", "repo": "adavis912-group/claims-intake-sample",
          "repo_url": "https://gitlab.com/adavis912-group/claims-intake-sample", "commit": "c92b266",
          "transformation": "AWS/comprehensive-codebase-analysis", "tool": "AWS Transform custom CLI (atx)",
          "mode": "Static analysis only (build command: noop). Source code is not changed on the main branch.",
          "note": "Stand-in source for CLM-INTAKE (fictional legacy code built for the capstone).", "price_per_min": 0.035}
TEXT_EXT = (".md", ".txt", ".json", ".csv", ".mmd", ".html")


def _inline(s):
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?!\w)", r"<em>\1</em>", s)
    return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)


def markdown(text):
    """Small, safe Markdown subset: headings, lists, tables, code fences, quotes, paragraphs."""
    out, lines, i = [], html.escape(text).split("\n"), 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + "\n".join(lines[i + 1:j]) + "</code></pre>")
            i = j + 1
            continue
        m = re.match(r"(#{1,6})\s+(.*)", ln)
        if m:
            lvl = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if ln.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in ln.strip().strip("|").split("|")]
            rows, j = [], i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            t = "<div class=\"tw\"><table><thead><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table></div>"
            out.append(t)
            i = j
            continue
        if re.match(r"\s*([-*+]|\d+\.)\s+", ln):
            tag = "ol" if re.match(r"\s*\d+\.", ln) else "ul"
            items = []
            while i < len(lines) and re.match(r"\s*([-*+]|\d+\.)\s+", lines[i]):
                items.append(re.sub(r"\s*([-*+]|\d+\.)\s+", "", lines[i], count=1))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x)}</li>" for x in items) + f"</{tag}>")
            continue
        if ln.startswith("&gt;"):
            out.append(f"<blockquote>{_inline(ln[4:].strip())}</blockquote>")
            i += 1
            continue
        if ln.strip():
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"(#{1,6}\s|```|\s*([-*+]|\d+\.)\s|\s*\|)", lines[i]):
                para.append(lines[i])
                i += 1
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            continue
        i += 1
    return "\n".join(out)


def _job_dir(job):
    if not job or not JOB_RE.match(job) or job == "latest":
        return None
    d = os.path.join(RESULTS_ROOT, job)
    return d if os.path.isdir(d) and not os.path.islink(d) else None


def documents(job):
    base, docs = _job_dir(job), []
    if not base:
        return docs
    for sub in ("artifacts", "repo-changes"):
        root = os.path.join(base, sub)
        for dp, _, fs in os.walk(root):
            for f in sorted(fs):
                if not f.lower().endswith(TEXT_EXT):
                    continue
                p = os.path.join(dp, f)
                docs.append({"path": os.path.relpath(p, base), "name": f, "size": os.path.getsize(p), "where": sub})
    return sorted(docs, key=lambda d: (not d["name"].lower().startswith(("readme", "executive", "summary", "index")), d["path"]))


def read_doc(job, rel):
    """Rendered HTML for one document of one scan job, or None if it is not a known document."""
    base = _job_dir(job)
    if not base or rel not in {d["path"] for d in documents(job)}:
        return None
    p = os.path.realpath(os.path.join(base, rel))
    if not p.startswith(os.path.realpath(base) + os.sep):
        return None
    text = open(p, errors="replace").read()[:400_000]
    if p.endswith(".md"):
        return markdown(text)
    if p.endswith(".json"):
        try:
            text = json.dumps(json.loads(text), indent=2)
        except ValueError:
            pass
    return "<pre><code>" + html.escape(text) + "</code></pre>"


def _load(path, default):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def scans():
    """Every synced scan job, newest first, each tagged with the portfolio app it belongs to."""
    out = []
    try:
        jobs = os.listdir(RESULTS_ROOT)
    except OSError:
        jobs = []
    for job in jobs:
        base = _job_dir(job)
        if not base:
            continue
        meta = _load(os.path.join(base, "scan.json"), None) or dict(LEGACY, job=job)
        st = _load(os.path.join(base, "status.json"), {})
        try:
            commits = [c for c in open(os.path.join(base, "repo-commits.txt")).read().split("\n") if c.strip()]
        except OSError:
            commits = []
        docs = documents(job)
        state = "running" if st.get("running") else ("complete" if docs else ("not_started" if not st else "ended"))
        out.append({"job": job, "scan": meta, "status": st, "state": state, "documents": docs, "commits": commits})
    return sorted(out, key=lambda x: x["status"].get("started", ""), reverse=True)


def status():
    return {"scans": scans(), "controls": scan_controls()}


# ------------------------------------------------------------------ starting a scan from the page
# The web server never runs atx. It only drops a validated request file in QUEUE; the root timer
# capstone-scan-runner (scans/scan_runner.py) picks it up, re-validates it, and starts atx as ssm-user.
import secrets as _secrets
import datetime as _dt

QUEUE = os.environ.get("SCAN_QUEUE", "/var/lib/capstone-dashboard/scan-queue")
REQ_STATUS = os.environ.get("SCAN_REQUEST_STATUS", "/var/lib/capstone-dashboard/scan-status")
REGISTRY = os.environ.get("SCAN_REGISTRY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scans", "apps.json"))
DAILY_LIMIT = int(os.environ.get("SCAN_DAILY_LIMIT", "5"))
TRANSFORMATIONS = {
    "AWS/comprehensive-codebase-analysis": "Full codebase analysis: architecture, dependencies, technical debt, migration notes",
    "AWS/modernization-readiness-analysis": "Readiness for modernization: blockers, effort and recommended path",
    "AWS/agentic-readiness-analysis": "Readiness for AI agents: interfaces and integration points",
}


def registry():
    """app_id -> repo info, from scans/apps.json. Only these apps can be scanned."""
    try:
        reg = json.load(open(REGISTRY))
    except (OSError, ValueError):
        return {}
    return {v["app"]: {"repo": v.get("repo", ""), "path": k} for k, v in reg.items() if not k.startswith("_") and isinstance(v, dict)}


def _requests():
    out = []
    for d, default in ((REQ_STATUS, None), (QUEUE, "queued")):
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if not n.endswith(".json"):
                continue
            r = _load(os.path.join(d, n), None)
            if isinstance(r, dict) and r.get("id"):
                if default:
                    r.setdefault("state", default)
                out.append(r)
    uniq = {}
    for r in out:   # a status file (written by the runner) wins over the queued copy
        if r["id"] not in uniq or r.get("state") != "queued":
            uniq[r["id"]] = r
    return sorted(uniq.values(), key=lambda r: r.get("requested_at", ""), reverse=True)


def scan_controls():
    today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
    reqs = _requests()
    used = sum(1 for r in reqs if r.get("requested_at", "").startswith(today) and r.get("state") != "rejected")
    return {"registered": registry(), "transformations": TRANSFORMATIONS, "requests": reqs[:20],
            "daily_limit": DAILY_LIMIT, "used_today": used,
            "busy": any(r.get("state") in ("queued", "starting", "running") for r in reqs) or any(s["state"] == "running" for s in scans())}


def request_scan(app, transformation, who="dashboard"):
    """Validate and queue a scan. Returns (http_status, message)."""
    reg = registry()
    if app not in reg:
        return 400, f"{app} has no source repository registered for scanning."
    if transformation not in TRANSFORMATIONS:
        return 400, "Unknown transformation."
    c = scan_controls()
    if c["busy"]:
        return 409, "A scan is already queued or running. One scan runs at a time."
    if c["used_today"] >= DAILY_LIMIT:
        return 429, f"Daily limit reached ({DAILY_LIMIT} scans per day)."
    now = _dt.datetime.utcnow()
    rid = now.strftime("%Y%m%d%H%M%S") + "-" + _secrets.token_hex(3)
    req = {"id": rid, "app": app, "transformation": transformation, "requested_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
           "requested_by": who, "state": "queued"}
    os.makedirs(QUEUE, exist_ok=True)
    tmp = os.path.join(QUEUE, f".{rid}.tmp")
    with open(tmp, "w") as f:
        json.dump(req, f)
    os.replace(tmp, os.path.join(QUEUE, f"{rid}.json"))
    return 202, f"Scan queued for {app}. It starts within a minute."
