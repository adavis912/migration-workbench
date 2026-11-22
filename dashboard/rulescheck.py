"""Rules check: a one-page report that sets the project's known rules against what an AWS Transform scan found.

Inputs (all already on the workbench, nothing invented):
  - the target mapping rules (synthetic_portfolio/mapping_rules.yaml) and the pipeline policy (migration_flow/policy.py),
  - the app's Discovery record and targets (discovery.build()),
  - the documents the scan wrote (assessment.documents()).
Claude reads them and returns a fixed JSON shape in which every finding quotes the scan document it came from.
Each quote is then checked against that document here; findings whose quote cannot be found are dropped and
counted, so the page cannot show a finding the scan did not make.

Output per scan job: RULES_OUT/<job>/report.json and report.md. Standard library + the anthropic SDK.
"""
import datetime as dt
import json
import os
import re

import assessment

RULES_OUT = os.environ.get("RULES_OUT", "/var/lib/capstone-dashboard/rules")
MODEL = os.environ.get("RULES_MODEL", "claude-sonnet-5-5")
PRICE = {"input": 3.0, "output": 15.0}          # USD per million tokens, for the cost line (estimate)
MAX_DOC_CHARS, MAX_TOTAL_CHARS = 40_000, 220_000
HERE = os.path.dirname(os.path.abspath(__file__))
MAPPING = os.environ.get("MAPPING_RULES", os.path.join(
    os.environ.get("DISCOVERY_DATA", "/opt/capstone/capstone_kb/synthetic_portfolio/data"), "..", "mapping_rules.yaml"))

# Target-mapping and inventory rules, by ID (the IDs are what the report rows cite).
MAP_RULES = {
    "MAP-OS": "Homegrown apps move to RHEL 9 (Windows Server 2022); COTS apps keep their current OS unless the vendor confirms a newer one",
    "MAP-JDK": "Keep the same JDK version (Oracle JDK 1.8 stays 1.8; OpenJDK 17 stays 17)",
    "MAP-RUNTIME": "App runtimes and web servers move like-for-like to EC2 (Tomcat, Jetty, Spring Boot, Node.js, Apache httpd)",
    "MAP-MESSAGING": "Messaging moves like-for-like to EC2 (Kafka, IBM MQ, RabbitMQ...)",
    "MAP-CACHE": "Redis moves to Amazon ElastiCache for Valkey (managed); Elasticsearch to EC2",
    "MAP-SECRETS": "OCI Vault moves to AWS Secrets Manager; application secrets are read from Secrets Manager",
    "MAP-DB": "Database targets: Oracle Exadata/Autonomous -> Oracle Database@AWS; other Oracle -> decision needed; SQL Server/MySQL -> RDS; MongoDB/Cassandra -> EC2",
    "MAP-STORAGE": "Block storage -> EBS, file shares -> EFS, objects -> S3",
    "MAP-CICD": "Jenkins + manual deploy is replaced by GitLab CI/CD + Terraform",
    "INV-STACK": "The stack in the Discovery inventory (runtime, JDK, web server, messaging, cache, database) matches the code",
    "INV-DEPS": "Every external system the code connects to is in the Discovery dependency list",
}
STATUS = {"confirms": "✅ Confirms", "needs_change": "⚠️ Needs code change", "blocks_gate": "❌ Blocks a pipeline gate",
          "not_in_inventory": "🆕 Not in inventory"}
VERDICT = {"ready": "Ready", "ready_with_fixes": "Ready with fixes", "blocked": "Blocked"}


def policies():
    try:
        import policy   # migration_flow is on sys.path (collector puts it there)
        return dict(policy.POLICIES)
    except Exception:
        return {}


def catalog():
    c = dict(MAP_RULES)
    c.update(policies())
    return c


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[`*_>#|]", "", str(s))).strip().lower()


def _docs_text(job):
    docs, total = [], 0
    base = os.path.join(assessment.RESULTS_ROOT, job)
    for d in assessment.documents(job):
        if total >= MAX_TOTAL_CHARS:
            break
        p = os.path.realpath(os.path.join(base, d["path"]))
        if not p.startswith(os.path.realpath(base) + os.sep):
            continue
        try:
            t = open(p, errors="replace").read()[:MAX_DOC_CHARS]
        except OSError:
            continue
        t = t[: MAX_TOTAL_CHARS - total]
        total += len(t)
        docs.append({"path": d["path"], "text": t})
    return docs


def _app_context(data, app_id):
    a = next((x for x in data["apps"] if x["id"] == app_id), {}) or {}
    keep = ("id", "name", "type", "cots", "runtime", "jdk", "web", "messaging", "cache", "db_engine", "db_service",
            "cicd", "secrets", "security", "pci", "wave")
    ctx = {k: a.get(k) for k in keep}
    ctx["findings"] = [f["t"] for f in a.get("findings", [])]
    ctx["servers"] = [{"host": s["host"], "role": s["role"], "os": s["os"], "target": s["target"], "target_os": s["target_os"],
                       "instance": s["instance"], "rule_flags": s["rule_flags"]} for s in data["servers"] if s["app"] == app_id]
    ctx["databases"] = [{"id": d["id"], "engine": d["engine"], "version": d["version"], "target": d["target"]}
                        for d in data["databases"] if d["app"] == app_id]
    ctx["dependencies"] = [{"from": e["src"], "to": e["dst"], "via": [n["service"] for n in e["net"]],
                            "cmdb": (e.get("cmdb") or {}).get("type")} for e in data["deps"] if app_id in (e["src"], e["dst"])]
    return ctx


def _schema(rule_ids, doc_paths):
    ev = {"type": "object", "additionalProperties": False, "required": ["doc", "quote"],
          "properties": {"doc": {"type": "string", "enum": doc_paths},
                         "quote": {"type": "string", "description": "Exact words copied from that document, 4 to 25 words"}}}
    row = {"type": "object", "additionalProperties": False,
           "required": ["rule_id", "expected", "found", "status", "evidence"],
           "properties": {"rule_id": {"type": "string", "enum": rule_ids},
                          "expected": {"type": "string", "description": "What the rule or inventory expects, max 18 words"},
                          "found": {"type": "string", "description": "What the scan found, max 25 words"},
                          "status": {"type": "string", "enum": list(STATUS)},
                          "evidence": {"type": "array", "items": ev, "minItems": 1}}}
    act = {"type": "object", "additionalProperties": False, "required": ["action", "rule_ids"],
           "properties": {"action": {"type": "string", "description": "Plain-language action, max 25 words"},
                          "rule_ids": {"type": "array", "items": {"type": "string", "enum": rule_ids}, "minItems": 1}}}
    return {"type": "object", "additionalProperties": False, "required": ["verdict", "summary", "rows", "actions"],
            "properties": {"verdict": {"type": "string", "enum": list(VERDICT)},
                           "summary": {"type": "string", "description": "Two sentences, max 45 words"},
                           "rows": {"type": "array", "items": row, "minItems": 1},
                           "actions": {"type": "array", "items": act, "minItems": 1}}}


SYSTEM = """You compare an application's AWS Transform code-scan output with the migration program's known rules.
Write for a migration lead: short, specific, no filler. Only report what the scan documents actually say.
Every row must cite evidence: the exact path of a scan document and words copied verbatim from it (4 to 25 words,
no paraphrasing, no ellipses). If you cannot quote it, leave the row out.
Status meanings:
- confirms: the scan shows the code is consistent with the rule (include 1 to 3 of these, for the most important rules).
- needs_change: the code must change for the rule to hold on AWS (e.g. reads secrets from a local file while the rule says Secrets Manager).
- blocks_gate: a blocking pipeline gate (secrets scan, SAST, SCA/dependency check, IaC scan, TLS policy) would fail on what the scan found.
- not_in_inventory: the code uses a component or connects to a system that the Discovery inventory does not list.
Verdict: blocked if any row is blocks_gate; ready_with_fixes if any needs_change or not_in_inventory; else ready.
Pick the 6 to 12 rows that matter most for moving this app in its wave; prefer one row per rule.
Actions: at most 5, ordered by what unblocks the wave first."""


def generate(data, job, api_key, who="dashboard"):
    """Build the report for one scan job. Returns the report dict (also written to RULES_OUT/<job>/)."""
    if not job or not assessment.JOB_RE.match(job) or job == "latest":
        raise ValueError("bad job id")
    scan = next((s for s in assessment.scans() if s["job"] == job), None)
    if not scan:
        raise ValueError("unknown scan")
    if scan["state"] == "running":
        raise ValueError("The scan is still running.")
    docs = _docs_text(job)
    if not docs:
        raise ValueError("This scan has no documents to check.")
    app_id = scan["scan"].get("app", "")
    cat = catalog()
    rule_ids = sorted(cat)
    doc_paths = [d["path"] for d in docs]
    rules_txt = "\n".join(f"- {k}: {v}" for k, v in sorted(cat.items()))
    try:
        mapping_yaml = open(MAPPING).read()[:12_000]
    except OSError:
        mapping_yaml = ""
    user = (f"<rules>\n{rules_txt}\n</rules>\n<mapping_rules_yaml>\n{mapping_yaml}\n</mapping_rules_yaml>\n"
            f"<application inventory=\"Discovery record and AWS targets\">\n{json.dumps(_app_context(data, app_id), indent=1)}\n</application>\n"
            f"<scan job=\"{job}\" transformation=\"{scan['scan'].get('transformation', '')}\">\n"
            + "\n".join(f"<document path=\"{d['path']}\">\n{d['text']}\n</document>" for d in docs)
            + "\n</scan>\nWrite the rules check for this application.")

    import anthropic
    client = anthropic.Anthropic(api_key=api_key, max_retries=3)
    tin = tout = 0
    raw, max_tokens = None, 16000
    for _ in range(2):   # one retry with more room if the answer was cut off
        with client.messages.stream(model=MODEL, max_tokens=max_tokens, system=SYSTEM, messages=[{"role": "user", "content": user}],
                                    output_config={"format": {"type": "json_schema", "schema": _schema(rule_ids, doc_paths)}}) as s:
            msg = s.get_final_message()
        usage = getattr(msg, "usage", None)
        tin += getattr(usage, "input_tokens", 0) or 0
        tout += getattr(usage, "output_tokens", 0) or 0
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        if getattr(msg, "stop_reason", None) == "max_tokens":
            max_tokens = 32000
            continue
        raw = json.loads(text)
        break
    if raw is None:
        raise ValueError("Claude's answer was too long to finish. Try again.")

    # Keep only rows whose quotes really appear in the cited document.
    text_of = {d["path"]: _norm(d["text"]) for d in docs}
    rows, dropped = [], 0
    for r in raw.get("rows", [])[:14]:
        ev = [e for e in r.get("evidence", []) if e.get("doc") in text_of and len(_norm(e.get("quote", ""))) >= 12
              and _norm(e["quote"]) in text_of[e["doc"]]]
        if not ev or r.get("rule_id") not in cat or r.get("status") not in STATUS:
            dropped += 1
            continue
        rows.append(dict(r, evidence=ev[:2], rule=cat[r["rule_id"]]))
    if not rows:
        raise ValueError(f"None of Claude's {dropped} findings could be matched to a quote in the scan documents, so nothing was saved. Try again.")
    order = {"blocks_gate": 0, "needs_change": 1, "not_in_inventory": 2, "confirms": 3}
    rows.sort(key=lambda r: order[r["status"]])
    rows = rows[:12]
    statuses = {r["status"] for r in rows}
    verdict = "blocked" if "blocks_gate" in statuses else "ready_with_fixes" if statuses & {"needs_change", "not_in_inventory"} else "ready"
    kept_ids = {r["rule_id"] for r in rows}
    actions = [a for a in raw.get("actions", []) if set(a.get("rule_ids", [])) & kept_ids][:5]
    app = next((x for x in data["apps"] if x["id"] == app_id), {}) or {}
    report = {"job": job, "app": app_id, "app_name": app.get("name", ""), "wave": app.get("wave"),
              "scan_started": scan["status"].get("started", ""), "transformation": scan["scan"].get("transformation", ""),
              "verdict": verdict, "summary": raw.get("summary", ""), "rows": rows, "actions": actions,
              "dropped_rows": dropped, "documents_read": len(docs), "model": MODEL, "by": who,
              "generated_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
              "usage": {"input_tokens": tin, "output_tokens": tout,
                        "estimated_cost_usd": round(tin / 1e6 * PRICE["input"] + tout / 1e6 * PRICE["output"], 3)}}
    out = os.path.join(RULES_OUT, job)
    os.makedirs(out, exist_ok=True)
    for name, content in (("report.json", json.dumps(report, indent=1)), ("report.md", to_markdown(report))):
        tmp = os.path.join(out, "." + name + ".tmp")
        with open(tmp, "w") as f:
            f.write(content)
        os.replace(tmp, os.path.join(out, name))
    return report


def _cell(s):
    return re.sub(r"\s+", " ", str(s)).replace("|", "/").strip()


def to_markdown(r):
    when = r.get("scan_started", "")[:10]
    lines = [f"# Rules check · {r['app']} {r.get('app_name', '')}".rstrip(), "",
             f"**Verdict: {VERDICT[r['verdict']]}** · Wave {r.get('wave') or 'not planned'} · scan {r['job']}" + (f" ({when})" if when else ""), "",
             r.get("summary", ""), "",
             "| Rule | Expected | Found by AWS Transform | Status | Source |", "|---|---|---|---|---|"]
    for row in r["rows"]:
        src = "; ".join(f"`{os.path.basename(e['doc'])}`: \"{_cell(e['quote'])}\"" for e in row["evidence"])
        short = _cell(row.get("rule", ""))
        short = short if len(short) <= 60 else short[:59] + "…"
        lines.append(f"| **{row['rule_id']}** {short} | {_cell(row['expected'])} | {_cell(row['found'])} | {STATUS[row['status']]} | {src} |")
    if r["actions"]:
        lines += ["", "## Before the wave", ""]
        lines += [f"{i}. {_cell(a['action'])} ({', '.join(a['rule_ids'])})" for i, a in enumerate(r["actions"], 1)]
    u = r.get("usage", {})
    dropped = f", {r['dropped_rows']} unquotable row(s) dropped" if r.get("dropped_rows") else ""
    cost = f"{u.get('estimated_cost_usd', 0):.2f}"
    when_gen = r["generated_at"][:16].replace("T", " ")
    lines += ["", f"> Rules: target mapping rules, pipeline security policy and the Discovery record. Findings: {r['documents_read']} "
              f"AWS Transform documents. Drafted by Claude ({r['model']}); every row quotes the scan document it came from and the "
              f"quote was checked against that document{dropped}. Generated {when_gen} UTC, about ${cost}."]
    return "\n".join(lines) + "\n"


def status():
    """job -> short summary of its report, for the page."""
    out = {}
    try:
        names = os.listdir(RULES_OUT)
    except OSError:
        return out
    for n in names:
        p = os.path.join(RULES_OUT, n, "report.json")
        if assessment.JOB_RE.match(n) and os.path.isfile(p):
            try:
                r = json.load(open(p))
                out[n] = {"verdict": r["verdict"], "generated_at": r["generated_at"], "rows": len(r["rows"]),
                          "cost": r.get("usage", {}).get("estimated_cost_usd")}
            except (OSError, ValueError, KeyError):
                pass
    return out


def read(job, fmt="html"):
    if not job or not assessment.JOB_RE.match(job):
        return None
    p = os.path.join(RULES_OUT, job, "report.md")
    if not os.path.isfile(p):
        return None
    md = open(p).read()
    return md if fmt == "md" else assessment.markdown(md)
