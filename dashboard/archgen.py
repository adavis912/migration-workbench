"""Draw an application's current-state (OCI) and future-state (AWS) architecture from discovery data.

The facts come from discovery.build(): the app profile, its servers, databases and dependencies, and the
target each server and database gets from migration_flow/target_spec.py (the same rules the pipelines use).
Nothing is invented: every box is a server, database, service or dependency in that data, or a standard
landing-zone service the target spec always adds (Route 53 weighted cutover, internal ALB, Secrets Manager,
KMS, Systems Manager, CloudWatch, S3 artifacts, GitLab OIDC role).

Output per app, under ARCH_OUT/<APP>/: current.svg, future.svg, current.eraserdiagram, future.eraserdiagram
(the same diagram as Eraser source, for anyone who wants to restyle it in Eraser) and meta.json.
Standard library only.
"""
import datetime as dt
import html
import json
import os
import re
import urllib.error
import urllib.request

ARCH_OUT = os.environ.get("ARCH_OUT", "/var/lib/capstone-dashboard/arch")
APP_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,30}$")
ERASER_RENDER = "https://app.eraser.io/api/render/elements"   # Eraser DSL -> diagram (no AI)

# AWS architecture-icon category colours, used for the small badge on each box.
COL = {"compute": "#ED7100", "network": "#8C4FFF", "database": "#C925D1", "storage": "#7AA116", "security": "#DD344C",
       "mgmt": "#E7157B", "devtools": "#2E73B8", "oci": "#C74634", "people": "#5B6577", "ext": "#6A7385"}
PORTS = {"80": "HTTP 80", "443": "HTTPS 443", "8080": "HTTP 8080", "8443": "HTTPS 8443", "8009": "AJP 8009",
         "1521": "Oracle 1521", "1433": "SQL Server 1433", "3306": "MySQL 3306", "27017": "MongoDB 27017",
         "6379": "Redis 6379", "9092": "Kafka 9092", "9200": "Elasticsearch 9200", "1414": "MQ 1414", "22": "SFTP 22",
         "5432": "PostgreSQL 5432", "9042": "Cassandra 9042"}
DASH = ' stroke-dasharray="5 4"'
TIER = {"web": 0, "app": 1, "batch": 1, "etl": 1, "oke-node": 1, "mq": 2, "search": 2, "db": 3}


def _short(host):
    return host.split(".")[0]


# ------------------------------------------------------------------ model
class D:
    """A diagram: nodes in columns, groups drawn around sets of nodes, and edges."""

    def __init__(self, title, subtitle):
        self.title, self.subtitle = title, subtitle
        self.nodes, self.groups, self.edges, self.notes = {}, [], [], []

    def node(self, nid, col, title, lines=(), kind="compute", badge="", eraser_icon=""):
        self.nodes[nid] = {"id": nid, "col": col, "title": title, "lines": [l for l in lines if l], "kind": kind,
                           "badge": badge, "icon": eraser_icon, "order": len(self.nodes)}
        return nid

    def edge(self, a, b, label="", dashed=False):
        if a in self.nodes and b in self.nodes and a != b and not any(e[:2] == (a, b) for e in self.edges):
            self.edges.append((a, b, label, dashed))

    def group(self, label, members, style, eraser_icon=""):
        members = [m for m in members if m in self.nodes]
        if members:
            self.groups.append({"label": label, "members": members, "style": style, "icon": eraser_icon})


def _app(data, app_id):
    return next((a for a in data["apps"] if a["id"] == app_id), None)


def _deps(data, app_id):
    """[(other_app, direction, label)] from the reconciled dependency list."""
    out = []
    for e in data["deps"]:
        if app_id not in (e["src"], e["dst"]) or e["src"] == e["dst"] or "?" in (e["src"], e["dst"]):
            continue
        other, direction = (e["dst"], "out") if e["src"] == app_id else (e["src"], "in")
        bits = sorted({x["service"] for x in e["net"]})
        if e.get("cmdb") and not bits:
            bits = [e["cmdb"]["type"]]
        if e.get("shared_db"):
            bits.append("shared DB " + e["shared_db"])
        out.append((other, direction, ", ".join(bits)[:40]))
    return sorted(set(out))


def _intra_flows(data, app_id, hosts):
    return [f for f in data["flows"] if f["src"] == app_id and f["dst"] == app_id
            and f["src_host"] in hosts and f["dst_host"] in hosts]


def _wave_of(data, app_id):
    a = _app(data, app_id)
    return a.get("wave") if a else None


def build_models(data, app_id):
    a = _app(data, app_id)
    if not a:
        raise ValueError(f"{app_id} is not in the discovery inventory")
    servers = sorted([s for s in data["servers"] if s["app"] == app_id], key=lambda s: (TIER.get(s["role"], 1), s["host"]))
    live = [s for s in servers if not any(f["k"] == "not_seen" for f in s["flags"])]
    gone = [s for s in servers if s not in live]
    dbs = [d for d in data["databases"] if d["app"] == app_id]
    deps = _deps(data, app_id)
    bu_users = f"{a['bu']} users"
    return _current(data, a, servers, dbs, deps, bu_users), _future(data, a, live, gone, dbs, deps, bu_users)


def _current(data, a, servers, dbs, deps, bu_users):
    d = D(f"{a['id']} · {a['name']} — current state (Oracle Cloud)",
          f"{a['type']}{' · ' + a['cots'] if a['cots'] else ''} · {a['runtime']}{' · ' + a['jdk'] if a['jdk'] else ''} · deployed on {a['deployment']}")
    d.node("users", 0, bu_users, ["on-premises / OCI"], "people", "USR", "users")
    ups = [o for o, dirn, _ in deps if dirn == "in"]
    for o in ups:
        d.node("dep:" + o, 0, o, [_name(data, o), "calls this app"], "ext", "APP", "server")
    d.node("cicd", 0, "Current CI/CD", [a["cicd"]], "devtools", "CI", "jenkins")
    hosts = {s["host"] for s in servers}
    db_hosts = {s["host"] for s in servers if s["role"] == "db"}
    for s in servers:
        if s["role"] == "db" and dbs:
            continue   # shown as the database node below
        col = 1 if s["role"] == "web" else 2
        flags = [f["t"].split(":")[0] for f in s["flags"] if f["k"] in ("old_os", "not_seen")]
        d.node("s:" + s["host"], col, _short(s["host"]),
               [f"{s['role']} · {s['service']}", f"{s['os']}", f"{int(s['vcpu'])} vCPU · {int(s['mem'])} GB"] + [("⚠ " + flags[0])[:30]] if flags else
               [f"{s['role']} · {s['service']}", f"{s['os']}", f"{int(s['vcpu'])} vCPU · {int(s['mem'])} GB"],
               "oci", "VM", "oracle")
    for db in dbs:
        d.node("db:" + db["id"], 3, f"{db['engine']} {db['version']}",
               [db["service"], f"{int(db['size']):,} GB", ("host " + ", ".join(_short(h) for h in sorted(db_hosts))) if db_hosts else ""],
               "database", "DB", "oracle")
    if a["cache"]:
        d.node("cache", 3, a["cache"], ["cache / search"], "database", "MEM", "redis")
    if a["web"] and not any(s["role"] == "web" for s in servers):
        pass
    d.node("vault", 3, a["secrets"] or "Secrets", ["secrets store"], "security", "KEY", "oracle")
    for o, dirn, lab in deps:
        if dirn == "out":
            d.node("dep:" + o, 4, o, [_name(data, o), lab], "ext", "APP", "server")

    webs = [n for n in d.nodes if n.startswith("s:") and d.nodes[n]["col"] == 1]
    apps = [n for n in d.nodes if n.startswith("s:") and d.nodes[n]["col"] == 2]
    front = webs or apps
    for n in front:
        d.edge("users", n, "HTTPS" if webs else "HTTP")
    flows = _intra_flows(data, a["id"], hosts)
    if flows:
        for f in flows:
            src, dst = "s:" + f["src_host"], "s:" + f["dst_host"]
            if f["dst_host"] in db_hosts and dbs:
                dst = "db:" + dbs[0]["id"]
            d.edge(src, dst, f["service"])
    else:
        for w in webs:
            for p in [n for n in apps if "mq" not in n]:
                d.edge(w, p, "HTTP")
    for p in apps:
        role = next((s["role"] for s in servers if "s:" + s["host"] == p), "")
        if role in ("mq", "search"):
            continue
        for db in dbs:
            d.edge(p, "db:" + db["id"], _db_port(db["engine"]) or db["engine"])
        for q in apps:
            if any(s["role"] in ("mq", "search") and "s:" + s["host"] == q for s in servers):
                d.edge(p, q, "Kafka 9092" if "mq" in q else "search")
        if "cache" in d.nodes:
            d.edge(p, "cache", "6379")
        d.edge(p, "vault", "secrets", dashed=True)
    for o, dirn, lab in deps:
        tgt = (apps or webs or [None])[0]
        if tgt:
            if dirn == "out":
                d.edge(tgt, "dep:" + o, lab)
            else:
                d.edge("dep:" + o, (webs or apps)[0], lab)
    for n in front:
        d.edge("cicd", n, "manual deploy", dashed=True)
    vcn = [n for n in d.nodes if n.startswith(("s:", "db:")) or n == "cache"]
    d.group("VCN · private subnets", vcn, "vpc", "oracle")
    d.group("OCI tenancy", vcn + ["vault"], "region", "oracle")
    d.notes = [f["t"] for f in a["findings"]][:6]
    return d


def _future(data, a, live, gone, dbs, deps, bu_users):
    d = D(f"{a['id']} · {a['name']} — future state (AWS us-east-1)",
          f"Rehost on AWS · cutover by Route 53 weighted routing 5% → 25% → 100% · built by GitLab CI/CD + Terraform")
    d.node("users", 0, bu_users, ["on-premises / OCI"], "people", "USR", "users")
    d.node("gitlab", 0, "GitLab CI/CD", ["scan > build > publish", "plan/apply > deploy > cutover"], "devtools", "CI", "gitlab")
    d.node("legacy", 0, "Legacy OCI endpoint", ["until 100% cutover"], "oci", "OCI", "oracle")
    for o, dirn, _ in deps:
        if dirn == "in":
            d.node("dep:" + o, 0, o, [_name(data, o), _dep_where(data, o)], "ext", "APP", "server")
    d.node("r53", 1, "Route 53 private zone", ["weighted 5% > 25% > 100%"], "network", "R53", "aws-route-53")
    d.node("oidc", 1, "GitLab OIDC role", ["no stored keys"], "security", "IAM", "aws-iam")

    web_s = [s for s in live if s["role"] == "web"]
    app_s = [s for s in live if s["role"] not in ("web", "db")]
    db_s = [s for s in live if s["role"] == "db"]
    has_http = bool(web_s or app_s)
    if has_http:
        d.node("alb", 2, "Internal ALB", ["HTTPS 443, TLS 1.2+", "port 80 redirects"], "network", "ELB", "aws-elastic-load-balancing")
        d.node("cw", 1, "CloudWatch alarm", ["unhealthy hosts > rollback"], "mgmt", "CW", "aws-cloudwatch")
    d.node("ssm", 1, "Systems Manager", ["deploy + admin, no SSH"], "mgmt", "SSM", "aws-systems-manager")
    d.node("s3art", 1, "S3 artifacts", ["Trivy-scanned builds"], "storage", "S3", "aws-s3")
    for s in web_s + app_s:
        col = 3 if s["role"] == "web" else 4
        if s["target"] == "Amazon EKS":
            d.node("s:" + s["host"], col, _short(s["host"]), ["Amazon EKS node group", f"{int(s['vcpu'])} vCPU · {int(s['mem'])} GB"], "compute", "EKS", "aws-eks")
            continue
        os_note = s["target_os"] + (" (vendor)" if "vendor_must_confirm_os" in s["rule_flags"] else "")
        d.node("s:" + s["host"], col, _short(s["host"]),
               [f"{s['instance'] or 'EC2'} · {s['role']}", os_note[:30], f"{int(s['vcpu'])} vCPU · {int(s['mem'])} GB"], "compute", "EC2", "aws-ec2")
    for db in dbs:
        t = db["target"]
        kind_badge = ("RDS", "aws-rds") if t.startswith("Amazon RDS") else ("ODB", "oracle") if "Oracle Database@AWS" == t else \
                     ("EC2", "aws-ec2") if t.endswith(" on EC2") else ("DB?", "aws-rds")
        lines = [t if len(t) <= 30 else t[:29] + "…", f"{db['engine']} {db['version']} · {int(db['size']):,} GB"]
        if db["decision"]:
            lines.append("⚠ target needs a decision")
        if t.endswith(" on EC2") and db_s:
            lines.append(f"{db_s[0]['instance']} · {db_s[0]['target_os'][:18]}")
        d.node("db:" + db["id"], 5, f"{db['engine']} · {db['id']}", lines, "database", kind_badge[0], kind_badge[1])
    if not dbs:
        for s in db_s:
            d.node("s:" + s["host"], 5, _short(s["host"]), [s["target"], s["instance"]], "database", "DB", "aws-rds")
    cache = (a["cache"] or "").lower()
    if "redis" in cache:
        d.node("cache", 5, "ElastiCache for Valkey", ["Multi-AZ, TLS, IAM auth"], "database", "MEM", "aws-elasticache")
    elif cache:
        d.node("cache", 5, f"{a['cache']} on EC2", ["search tier"], "compute", "EC2", "aws-ec2")
    if (a["messaging"] or "") and not any(s["role"] == "mq" for s in live):
        d.node("mq", 5, a["messaging"], ["messaging (target per spec)"], "network", "MQ", "aws-mq")
    d.node("sm", 5, "Secrets Manager", [f"from {a['secrets'] or 'current store'}"], "security", "SM", "aws-secrets-manager")
    d.node("kms", 5, "KMS key (customer)", ["EBS, database, secrets"], "security", "KMS", "aws-kms")
    if has_http:
        d.node("s3log", 1, "S3 ALB access logs", [], "storage", "S3", "aws-s3")
    for o, dirn, lab in deps:
        if dirn == "out":
            d.node("dep:" + o, 6, o, [_name(data, o), _dep_where(data, o)], "ext", "APP", "server")

    webs = ["s:" + s["host"] for s in web_s]
    apps = ["s:" + s["host"] for s in app_s if s["role"] not in ("mq", "search")]
    mqs = ["s:" + s["host"] for s in app_s if s["role"] in ("mq", "search")]
    front = webs or apps
    d.edge("users", "r53")
    d.edge("legacy", "r53", "legacy weight")
    if has_http:
        d.edge("r53", "alb", "AWS weight")
        for n in front:
            d.edge("alb", n, "HTTPS 8443" if webs else "HTTPS")
        d.edge("alb", "s3log", "", dashed=True)
        d.edge("alb", "cw", "", dashed=True)
        d.edge("cw", "r53", "health", dashed=True)
    for w in webs:
        for p in apps:
            d.edge(w, p, "8080")
    for p in apps or webs:
        for q in mqs:
            d.edge(p, q, "Kafka 9092" if "mq" in q else "search")
        for db in dbs:
            d.edge(p, "db:" + db["id"], (_db_port(db["engine"]) + " TLS") if _db_port(db["engine"]) else "")
        if "cache" in d.nodes:
            d.edge(p, "cache", "6379 TLS")
        if "mq" in d.nodes:
            d.edge(p, "mq", "")
        d.edge(p, "sm", "", dashed=True)
    d.edge("sm", "kms", "", dashed=True)
    d.edge("gitlab", "oidc", "assume role")
    d.edge("gitlab", "s3art", "publish", dashed=True)
    d.edge("s3art", "ssm", "", dashed=True)
    for n in (front + [x for x in apps if x not in front])[:3]:
        d.edge("ssm", n, "deploy", dashed=True)
    for o, dirn, lab in deps:
        tgt = (apps or webs or [None])[0]
        if not tgt:
            continue
        if dirn == "out":
            d.edge(tgt, "dep:" + o, lab or "dependency")
        else:
            d.edge("dep:" + o, "r53", lab or "dependency")
    vpc = [n for n in d.nodes if n.startswith(("s:", "db:")) or n in ("alb", "cache", "mq")]
    d.group("VPC · private subnets", vpc, "vpc", "aws-vpc")
    d.group("AWS us-east-1", vpc + ["r53", "oidc", "ssm", "s3art", "sm", "kms", "cw", "s3log"], "region", "aws")
    notes = []
    for s in gone:
        notes.append(f"{_short(s['host'])} not built: not seen by discovery, confirm with the owner")
    for f in sorted({f for s in live for f in s["rule_flags"]}):
        notes.append({"vendor_must_confirm_os": "COTS: vendor must confirm the operating system on AWS",
                      "end_of_life_os_carried_over": "End-of-life OS carried over (vendor product): plan an upgrade",
                      "rule_conflict_cots_keep_os_vs_oel_app_to_rhel9": "Rule conflict: COTS keeps its OS vs Oracle Linux → RHEL 9",
                      "oracle_target_decision_needed": "Oracle database target needs a human decision"}.get(f, f.replace("_", " ")))
    if any(s["os"].startswith("RHEL 6") or s["os"] == "Windows Server 2016" for s in live) and a["type"] != "COTS":
        notes.append("Old OS replaced: servers rebuilt on the target OS, not lifted as-is")
    d.notes = notes[:6]
    return d


def _db_port(engine):
    return {"Oracle": "1521", "SQL Server": "1433", "MySQL": "3306", "MongoDB": "27017", "Cassandra": "9042", "PostgreSQL": "5432"}.get(engine, "")


def _name(data, app_id):
    a = _app(data, app_id)
    return (a["name"] if a else "outside the portfolio")[:28]


def _dep_where(data, app_id):
    w = _wave_of(data, app_id)
    if w is None:
        return "stays on OCI for now"
    if w == 1:
        return "moves in Wave 1 too"
    return f"on OCI until Wave {w}" if isinstance(w, int) else f"{w}: hybrid link needed"


# ------------------------------------------------------------------ SVG
NW, COLW, GAP, PADX, TOP = 212, 300, 22, 40, 110


def _node_h(n):
    return 34 + 15 * len(n["lines"])


def layout(d):
    used = sorted({n["col"] for n in d.nodes.values()})   # close up empty columns (e.g. no web tier)
    remap = {c: i for i, c in enumerate(used)}
    for n in d.nodes.values():
        n["col"] = remap[n["col"]]
    cols = {}
    for n in sorted(d.nodes.values(), key=lambda n: n["order"]):
        cols.setdefault(n["col"], []).append(n)
    heights = {c: sum(_node_h(n) for n in ns) + GAP * (len(ns) - 1) for c, ns in cols.items()}
    H = max(heights.values()) if heights else 100
    ncols = max(cols) + 1 if cols else 1
    for c, ns in cols.items():
        y = TOP + 40 + (H - heights[c]) / 2
        for n in ns:
            n["x"], n["y"], n["w"], n["h"] = PADX + c * COLW, y, NW, _node_h(n)
            y += n["h"] + GAP
    notes_h = 24 + 18 * len(d.notes) if d.notes else 0
    return PADX * 2 + (ncols - 1) * COLW + NW, TOP + 40 + H + 60 + notes_h, TOP + 40 + H + 40


def _esc(s):
    return html.escape(str(s), quote=True)


def render_svg(d):
    W, Hh, notes_y = layout(d)
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {Hh:.0f}" width="{W:.0f}" height="{Hh:.0f}" '
         f'font-family="IBM Plex Sans, Segoe UI, Helvetica, Arial, sans-serif" role="img" aria-label="{_esc(d.title)}">',
         '<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
         '<path d="M0,0 L10,5 L0,10 z" fill="#3b4558"/></marker></defs>',
         f'<rect width="100%" height="100%" fill="#ffffff"/>',
         f'<text x="{PADX}" y="38" font-size="20" font-weight="600" fill="#172033">{_esc(d.title)}</text>',
         f'<text x="{PADX}" y="62" font-size="12.5" fill="#5b6577">{_esc(d.subtitle)}</text>']
    # groups: outer first so inner draws on top
    pad = {"region": 22, "vpc": 12}
    styles = {"region": ("#fdfaf3", "#e3a547"), "vpc": ("#f6f8fe", "#4c6fe0")}
    for g in sorted(d.groups, key=lambda g: 0 if g["style"] == "region" else 1):
        ns = [d.nodes[m] for m in g["members"]]
        p = pad[g["style"]]
        inner = g["style"] == "region" and any(h["style"] == "vpc" and set(h["members"]) & set(g["members"]) for h in d.groups)
        x0, y0 = min(n["x"] for n in ns) - p, min(n["y"] for n in ns) - p - (48 if inner else 22)
        x1, y1 = max(n["x"] + n["w"] for n in ns) + p, max(n["y"] + n["h"] for n in ns) + p
        fill, stroke = styles[g["style"]]
        o.append(f'<rect x="{x0:.0f}" y="{y0:.0f}" width="{x1 - x0:.0f}" height="{y1 - y0:.0f}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.3"/>')
        o.append(f'<text x="{x0 + 12:.0f}" y="{y0 + 16:.0f}" font-size="11" font-weight="600" letter-spacing=".06em" fill="{stroke}">{_esc(g["label"].upper())}</text>')
    # edges under nodes
    labels = []
    for a, b, lab, dashed in d.edges:
        A, B = d.nodes[a], d.nodes[b]
        ay, by = A["y"] + A["h"] / 2, B["y"] + B["h"] / 2
        if A["col"] < B["col"]:
            x1, x2 = A["x"] + A["w"], B["x"]
            dx = max(40, (x2 - x1) / 2)
            path = f"M{x1:.0f},{ay:.0f} C{x1 + dx:.0f},{ay:.0f} {x2 - dx:.0f},{by:.0f} {x2:.0f},{by:.0f}"
        elif A["col"] > B["col"]:
            x1, x2 = A["x"], B["x"] + B["w"]
            dx = max(40, (x1 - x2) / 2)
            path = f"M{x1:.0f},{ay:.0f} C{x1 - dx:.0f},{ay:.0f} {x2 + dx:.0f},{by:.0f} {x2:.0f},{by:.0f}"
        else:
            x1 = A["x"] + A["w"]
            path = f"M{x1:.0f},{ay:.0f} C{x1 + 48:.0f},{ay:.0f} {x1 + 48:.0f},{by:.0f} {x1:.0f},{by:.0f}"
            x2 = x1 + 36
        o.append(f'<path d="{path}" fill="none" stroke="#3b4558" stroke-width="1.2"{DASH if dashed else ""} marker-end="url(#ar)"/>')
        if lab:
            if A["col"] == B["col"]:
                mx, my = x1 + 30, (ay + by) / 2
            elif abs(A["col"] - B["col"]) == 1:
                mx, my = (x1 + x2) / 2, (ay + by) / 2
            elif dashed:   # long secondary edge: label in the gap next to its source
                mx, my = (x1 + (COLW - NW) / 2, ay) if A["col"] < B["col"] else (x1 - (COLW - NW) / 2, ay)
            else:   # long edge: label sits in the free gap next to the target
                mx, my = (x2 - (COLW - NW) / 2, by) if A["col"] < B["col"] else (x2 + (COLW - NW) / 2, by)
            labels.append((mx, my, lab))
    for n in sorted(d.nodes.values(), key=lambda n: n["order"]):
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        c = COL.get(n["kind"], "#6a7385")
        o.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{w}" height="{h}" rx="8" fill="#ffffff" stroke="#c9d0dc"/>')
        o.append(f'<rect x="{x + 10:.0f}" y="{y + 10:.0f}" width="30" height="20" rx="4" fill="{c}"/>')
        o.append(f'<text x="{x + 25:.0f}" y="{y + 24:.0f}" font-size="9.5" font-weight="700" fill="#ffffff" text-anchor="middle">{_esc(n["badge"][:4])}</text>')
        o.append(f'<text x="{x + 48:.0f}" y="{y + 24:.0f}" font-size="12.5" font-weight="600" fill="#172033">{_esc(_clip(n["title"], 23))}</text>')
        for i, line in enumerate(n["lines"]):
            warn = line.startswith("⚠")
            o.append(f'<text x="{x + 12:.0f}" y="{y + 48 + 15 * i:.0f}" font-size="11" fill="{"#9a5b00" if warn else "#4a5466"}">{_esc(_clip(line, 31))}</text>')
    seen = set()
    for mx, my, lab in labels:
        if (round(mx), round(my), lab) in seen:
            continue
        seen.add((round(mx), round(my), lab))
        tw = 6.1 * len(lab) + 10
        o.append(f'<rect x="{mx - tw / 2:.0f}" y="{my - 9:.0f}" width="{tw:.0f}" height="16" rx="3" fill="#ffffff" fill-opacity=".92"/>')
        o.append(f'<text x="{mx:.0f}" y="{my + 3:.0f}" font-size="10.5" fill="#3b4558" text-anchor="middle">{_esc(lab)}</text>')
    if d.notes:
        o.append(f'<text x="{PADX}" y="{notes_y:.0f}" font-size="11.5" font-weight="600" fill="#172033">Notes from discovery</text>')
        for i, t in enumerate(d.notes):
            o.append(f'<text x="{PADX}" y="{notes_y + 18 * (i + 1):.0f}" font-size="11.5" fill="#4a5466">• {_esc(t)}</text>')
    o.append("</svg>")
    return "\n".join(o)


def _clip(s, n):
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ Eraser source
def render_eraser(d):
    def name(nid):
        return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 _-]", " ", d.nodes[nid]["title"])).strip() + ("" if not nid.startswith(("dep:", "s:", "db:")) else "")

    lines = ["cloud-architecture-diagram", f"title {d.title}", "direction right", ""]
    grouped = set()
    for g in sorted(d.groups, key=lambda g: 0 if g["style"] == "region" else 1):
        pass
    region = next((g for g in d.groups if g["style"] == "region"), None)
    vpc = next((g for g in d.groups if g["style"] == "vpc"), None)

    def decl(nid, indent=""):
        n = d.nodes[nid]
        label = "\\n".join([n["title"]] + n["lines"]).replace('"', "'")
        return f'{indent}{name(nid)} [icon: {n["icon"] or "server"}, label: "{label}"]'

    for nid in d.nodes:
        if not ((region and nid in region["members"]) or (vpc and nid in vpc["members"])):
            lines.append(decl(nid))
    if region:
        lines.append(f'{re.sub(r"[^A-Za-z0-9 -]", " ", region["label"])} [icon: {region["icon"]}] {{')
        for nid in region["members"]:
            if not (vpc and nid in vpc["members"]):
                lines.append(decl(nid, "  "))
        if vpc:
            lines.append(f'  {re.sub(r"[^A-Za-z0-9 -]", " ", vpc["label"])} [icon: {vpc["icon"]}] {{')
            lines += [decl(nid, "    ") for nid in vpc["members"]]
            lines.append("  }")
        lines.append("}")
    lines.append("")
    for a, b, lab, dashed in d.edges:
        lines.append(f'{name(a)} {"-->" if dashed else ">"} {name(b)}' + (f": {lab}" if lab else ""))
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ files
class EraserError(Exception):
    pass


def _eraser_render(code, token):
    """Send one cloud-architecture diagram to Eraser. Returns (png_bytes, create_file_url).
    The token goes only in the Authorization header and never into errors or logs."""
    body = json.dumps({"elements": [{"type": "diagram", "diagramType": "cloud-architecture-diagram", "code": code}],
                       "theme": "light", "background": True, "imageQuality": 2}).encode()
    req = urllib.request.Request(ERASER_RENDER, data=body, method="POST", headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = re.sub(r"\s+", " ", e.read(400).decode("utf-8", "replace"))[:200]
        except Exception:
            pass
        raise EraserError({400: "Eraser rejected the diagram", 401: "Eraser refused the API token",
                           403: "Eraser refused the API token", 503: "Eraser is busy, try again in a minute"}.get(e.code, f"Eraser error {e.code}")
                          + (f": {detail}" if detail and e.code == 400 else ""))
    except Exception as e:
        raise EraserError(f"Could not reach Eraser ({type(e).__name__})")
    url = res.get("imageUrl", "")
    if not url.startswith("https://"):
        raise EraserError("Eraser did not return an image")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            png = r.read(20_000_000)
    except Exception as e:
        raise EraserError(f"Could not download the image from Eraser ({type(e).__name__})")
    if not png.startswith(b"\x89PNG"):
        raise EraserError("Eraser did not return a PNG image")
    return png, res.get("createEraserFileUrl", "")


def _write(path, data):
    tmp = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + ".tmp")
    with open(tmp, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    os.replace(tmp, path)


def generate(data, app_id, who="dashboard", eraser_token=None):
    """Build both diagrams. With an Eraser token, Eraser draws them (PNG) from the Eraser DSL;
    the built-in SVG and the DSL are always saved too. Raises EraserError if Eraser fails (nothing is replaced)."""
    if not APP_RE.match(app_id or ""):
        raise ValueError("bad app id")
    cur, fut = build_models(data, app_id)
    files, links = {}, {}
    for name, d in (("current", cur), ("future", fut)):
        dsl = render_eraser(d)
        files[f"{name}.svg"], files[f"{name}.eraserdiagram"] = render_svg(d), dsl
        if eraser_token:
            files[f"{name}.png"], links[name] = _eraser_render(dsl, eraser_token)
    out = os.path.join(ARCH_OUT, app_id)
    os.makedirs(out, exist_ok=True)
    for fname, content in files.items():   # only after every render succeeded
        _write(os.path.join(out, fname), content)
    if not eraser_token:
        for name in ("current", "future"):
            try:
                os.remove(os.path.join(out, f"{name}.png"))
            except OSError:
                pass
    meta = {"app": app_id, "generated_at": dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"), "by": who,
            "renderer": "eraser" if eraser_token else "builtin", "eraser_links": links,
            "source": "Discovery inventory + target rules (migration_flow/target_spec.py)",
            "counts": {"current": len(cur.nodes), "future": len(fut.nodes)}}
    _write(os.path.join(out, "meta.json"), json.dumps(meta))
    return meta


def status():
    out = {}
    try:
        names = os.listdir(ARCH_OUT)
    except OSError:
        return out
    for n in names:
        p = os.path.join(ARCH_OUT, n, "meta.json")
        if APP_RE.match(n) and os.path.isfile(p):
            try:
                out[n] = json.load(open(p))
            except (OSError, ValueError):
                pass
    return out


def file_path(app_id, fname):
    """Path of a generated file, or None. Only the known file names are served."""
    if not APP_RE.match(app_id or "") or fname not in ("current.svg", "future.svg", "current.png", "future.png", "current.eraserdiagram", "future.eraserdiagram"):
        return None
    p = os.path.join(ARCH_OUT, app_id, fname)
    return p if os.path.isfile(p) else None
