"""Data for the Discovery page: applications, servers, databases and dependencies from the discovery inventory.

Reads the synthetic portfolio CSVs (fictional Harborview Insurance data) and adds what a migration team checks
during discovery: proposed AWS target and instance size, old operating systems, servers that look decommissioned,
CMDB-vs-discovery conflicts, and dependencies reconciled across network traffic and the CMDB.

Owner interview notes are deliberately NOT read here: they are free text, some contain planted prompt-injection
text and a plaintext password, and they belong in the agent's retrieval path, not on a web page.
The answer key is never read.
"""
import csv, datetime as dt, json, os

import target_spec

DATA = os.environ.get("DISCOVERY_DATA", "/opt/capstone/capstone_kb/synthetic_portfolio/data")
WAVE_PLAN = os.environ.get("WAVE_PLAN", os.path.join(os.path.dirname(os.path.abspath(__file__)), "waves.json"))


def wave_plan():
    """Wave assignments from the wave planner (waves.json): app -> wave number or "blocked"."""
    try:
        wp = json.load(open(WAVE_PLAN))
    except (OSError, ValueError):
        wp = {"waves": [], "blocked": {"apps": []}}
    assign = {a: w["wave"] for w in wp.get("waves", []) for a in w["apps"]}
    assign.update({a: "blocked" for a in wp.get("blocked", {}).get("apps", [])})
    return wp, assign


WAVE1 = [a for a, w in wave_plan()[1].items() if w == 1]
OLD_OS = {"RHEL 6": "End of life", "RHEL 7": "End of life", "Oracle Linux 7": "End of life",
          "Windows Server 2012 R2": "End of life", "Windows Server 2016": "Extended support ends Jan 2027"}
PORTS = {"443": "HTTPS", "80": "HTTP", "1521": "Oracle DB", "1414": "IBM MQ", "3306": "MySQL", "1433": "SQL Server",
         "27017": "MongoDB", "9092": "Kafka", "9200": "Elasticsearch", "6379": "Redis", "22": "SSH", "445": "SMB"}
STALE_RECORD_DAYS, NOT_SEEN_DAYS = 90, 30
_cache = {"key": None, "value": None}


def _read(name):
    with open(os.path.join(DATA, name), newline="") as f:
        return list(csv.DictReader(f))


def _date(s):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _num(v, default=0):
    try:
        return float(v) if "." in str(v) else int(v)
    except (TypeError, ValueError):
        return default


def build(today=None):
    files = ("applications.csv", "servers.csv", "databases.csv", "dependencies_network.csv", "dependencies_cmdb.csv")
    key = tuple(os.path.getmtime(os.path.join(DATA, f)) for f in files) + (str(today), os.path.getmtime(WAVE_PLAN) if os.path.exists(WAVE_PLAN) else 0)
    if _cache["key"] == key:
        return _cache["value"]
    today = today or dt.date.today()
    apps_raw, servers_raw, dbs_raw = _read(files[0]), _read(files[1]), _read(files[2])
    net_raw, cmdb_raw = _read(files[3]), _read(files[4])
    profiles = {a["app_id"]: a for a in apps_raw}
    wp, assign = wave_plan()
    host_app = {s["hostname"]: s["app_id"] for s in servers_raw}
    host_app.update({d["endpoint_host"]: d["app_id"] for d in dbs_raw if d["endpoint_host"]})

    # ---- servers
    servers = []
    for s in servers_raw:
        p = profiles.get(s["app_id"], {"app_type": "Homegrown"})
        vcpu, mem = _num(s["vcpu"]), _num(s["memory_gb"])
        seen = _date(s["last_seen"])
        flags = []
        if s["os"] in OLD_OS:
            flags.append(("old_os", f"{s['os']}: {OLD_OS[s['os']]}"))
        if not seen:
            flags.append(("not_seen", "Never reported by discovery"))
        elif (today - seen).days > NOT_SEEN_DAYS:
            flags.append(("not_seen", f"Not seen for {(today - seen).days} days: possibly decommissioned"))
        if s["discovery_agent"] != "Installed":
            flags.append(("no_agent", "No discovery agent: utilization and traffic unknown"))
        peak = _num(s["peak_cpu_pct"], None)
        if peak is not None and peak >= 85:
            flags.append(("hot", f"Peak CPU {peak}%: size up or check before cutover"))
        elif peak is not None and peak < 25 and s["role"] != "oke-node":
            flags.append(("idle", f"Peak CPU {peak}%: candidate to right-size down"))
        try:
            target, tos, tflags = target_spec.server_target(p, s)
        except Exception:
            target, tos, tflags = "Amazon EC2", "", []
        build_ec2 = target == "Amazon EC2" or target.endswith(" on EC2")
        servers.append({
            "id": s["server_id"], "host": s["hostname"], "app": s["app_id"], "role": s["role"], "service": s["oci_service"],
            "os": s["os"], "vcpu": vcpu, "mem": mem, "storage": _num(s["storage_gb"]),
            "cpu_avg": _num(s["avg_cpu_pct"], None), "cpu_peak": peak, "agent": s["discovery_agent"], "last_seen": s["last_seen"],
            "target": target, "target_os": tos,
            "instance": target_spec.instance_type(vcpu, mem) if build_ec2 and vcpu else "",
            "flags": [{"k": k, "t": t} for k, t in flags], "rule_flags": tflags,
        })

    # ---- databases
    databases = []
    for d in dbs_raw:
        target = target_spec.db_target(d)
        databases.append({
            "id": d["db_id"], "app": d["app_id"], "engine": d["engine"], "version": d["version"], "service": d["oci_service"],
            "size": _num(d["size_gb"]), "endpoint": d["endpoint_host"],
            "shared": [x.strip() for x in d["shared_with"].replace(";", ",").split(",") if x.strip()],
            "target": target, "decision": "TBD" in target or "decision" in target,
        })

    # ---- dependencies: network flows, CMDB records, and the two reconciled at app level
    flows = []
    for n in net_raw:
        sa, ta = host_app.get(n["source_host"], "?"), host_app.get(n["target_host"], "?")
        flows.append({"src_host": n["source_host"], "dst_host": n["target_host"], "src": sa, "dst": ta, "port": n["port"],
                      "service": PORTS.get(n["port"], "TCP " + n["port"]), "per_day": _num(n["avg_daily_connections"]),
                      "first": n["first_seen"], "last": n["last_seen"]})
    edges = {}
    for f in flows:
        if f["src"] == f["dst"]:
            continue
        e = edges.setdefault((f["src"], f["dst"]), {"src": f["src"], "dst": f["dst"], "net": [], "cmdb": None})
        e["net"].append({"service": f["service"], "port": f["port"], "per_day": f["per_day"]})
    for c in cmdb_raw:
        e = edges.setdefault((c["source_app"], c["target_app"]), {"src": c["source_app"], "dst": c["target_app"], "net": [], "cmdb": None})
        e["cmdb"] = {"type": c["dependency_type"], "frequency": c["frequency"], "by": c["documented_by"]}
    for d in databases:  # a shared database is a dependency even if nobody wrote it down
        for other in d["shared"]:
            e = edges.setdefault((other, d["app"]), {"src": other, "dst": d["app"], "net": [], "cmdb": None})
            e["shared_db"] = d["id"]
    deps = []
    for e in edges.values():
        e["seen_in"] = ("both" if e["net"] and e["cmdb"] else "network" if e["net"] else "cmdb" if e["cmdb"] else "shared_db")
        e["per_day"] = sum(x["per_day"] for x in e["net"])
        deps.append(e)
    deps.sort(key=lambda e: (e["src"], e["dst"]))

    # ---- applications
    by_app = {}
    for s in servers:
        by_app.setdefault(s["app"], []).append(s)
    apps = []
    for a in apps_raw:
        sv = by_app.get(a["app_id"], [])
        upd, fs, fe = _date(a["last_updated"]), _date(a["freeze_start"]), _date(a["freeze_end"])
        findings = []
        cmdb_n = _num(a["cmdb_server_count"])
        if cmdb_n != len(sv):
            findings.append(("conflict", f"CMDB lists {cmdb_n} servers, discovery found {len(sv)}"))
        if upd and (today - upd).days > STALE_RECORD_DAYS:
            findings.append(("stale", f"Inventory record last updated {(today - upd).days} days ago"))
        if (a["security_level"] == "Restricted" or a["pci_scope"] == "Y") and a["security_review"] != "Complete":
            findings.append(("security", f"{a['security_level']}{' / PCI' if a['pci_scope'] == 'Y' else ''} with security review: {a['security_review'] or 'none'}"))
        if fs and fe and fe >= today:
            findings.append(("freeze", f"Change freeze {a['freeze_start']} to {a['freeze_end']}" + (" (active now)" if fs <= today else "")))
        if a["secrets_store"].lower() == "config file":
            findings.append(("secrets", "Secrets kept in config files: move to Secrets Manager"))
        if a["app_type"] == "COTS":
            findings.append(("vendor", f"COTS ({a['cots_product'] or 'vendor product'}): vendor must confirm supported OS"))
        if a["deployment"].startswith("OKE"):
            findings.append(("eks", "Runs on OKE: target is Amazon EKS"))
        old = sorted({s["os"] for s in sv if s["os"] in OLD_OS})
        if old:
            findings.append(("old_os", "Old OS: " + ", ".join(old)))
        gone = [s["host"] for s in sv if any(f["k"] == "not_seen" for f in s["flags"])]
        if gone:
            findings.append(("not_seen", "Possibly decommissioned: " + ", ".join(gone)))
        if any(d["app"] == a["app_id"] and d["decision"] for d in databases):
            findings.append(("decision", "Oracle database target needs a human decision"))
        if any(a["app_id"] in d["shared"] or (d["app"] == a["app_id"] and d["shared"]) for d in databases):
            findings.append(("shared_db", "Shares a database with another app: move together or plan hybrid access"))
        apps.append({
            "id": a["app_id"], "name": a["app_name"], "bu": a["business_unit"], "owner": a["owner"], "type": a["app_type"],
            "cots": a["cots_product"], "security": a["security_level"], "pci": a["pci_scope"] == "Y",
            "tier": _num(a["criticality_tier"]), "rto": _num(a["rto_hours"]), "deployment": a["deployment"],
            "runtime": a["app_runtime"], "jdk": a["jdk"], "web": a["web_server"], "messaging": a["messaging"],
            "cache": a["cache_search"], "etl": a["etl_tools"], "db_engine": a["database_engine"], "db_service": a["database_service"],
            "cicd": a["current_cicd"], "secrets": a["secrets_store"], "freeze": [a["freeze_start"], a["freeze_end"]],
            "review": a["security_review"], "cmdb_servers": cmdb_n, "servers": len(sv), "updated": a["last_updated"],
            "wave": assign.get(a["app_id"]),
            "findings": [{"k": k, "t": t} for k, t in findings],
        })
    value = {"as_of": today.isoformat(), "wave1": [a for a, w in assign.items() if w == 1], "apps": apps, "servers": servers, "databases": databases,
             "deps": deps, "flows": flows,
             "source": "Synthetic discovery inventory (fictional Harborview Insurance data): agent-based server discovery, "
                       "30-day network traffic, CMDB and application register."}
    _cache.update(key=key, value=value)
    return value


def filter_catalog():
    """Apps and waves for the shared Wave / App filter on every page."""
    apps = build()["apps"]
    wp, _ = wave_plan()
    cat = [{"id": a["id"], "name": a["name"], "bu": a["bu"], "wave": a["wave"]} for a in apps]
    fmt = lambda d: dt.date.fromisoformat(d).strftime("%b %-d")
    out = []
    for w in sorted(wp.get("waves", []), key=lambda w: w["wave"]):
        n = sum(a["wave"] == w["wave"] for a in apps)
        out.append({"key": str(w["wave"]), "count": n, "status": w.get("status", "planned"),
                    "label": f"Wave {w['wave']} · {fmt(w['start'])} – {fmt(w['end'])}" + (" · approved" if w.get("status") == "approved" else "")})
    blocked = sum(a["wave"] == "blocked" for a in apps)
    if blocked:
        out.append({"key": "blocked", "label": "Blocked", "count": blocked, "reason": wp.get("blocked", {}).get("reason", "")})
    unplanned = sum(a["wave"] is None for a in apps)
    if unplanned:
        out.append({"key": "none", "label": "Not planned yet", "count": unplanned})
    return {"apps": cat, "waves": out}
