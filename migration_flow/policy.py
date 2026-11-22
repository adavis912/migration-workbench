"""Reviewer's deterministic policy checks on a change set (Terraform + GitLab CI + notes).

Runs before the Reviewer's Claude review. Any failure here rejects the change set, whatever the model
thinks. Each check has an ID so rejections, the run log and the write-up can cite it.
"""
import io, re

import hcl2
import yaml

POLICIES = {
    "TF-PARSE": "Terraform files parse",
    "EC2-IMDSV2": "Every EC2 instance / launch template requires IMDSv2 (http_tokens = required)",
    "EC2-EBS-ENC": "Root and data volumes are encrypted with the customer-managed KMS key",
    "EC2-NO-PUBLIC-IP": "No public IP addresses; instances use var.private_subnet_ids",
    "EC2-SSM": "Instances have an instance profile with AmazonSSMManagedInstanceCore (no SSH/RDP keys)",
    "SG-NO-WORLD": "No security-group ingress from 0.0.0.0/0 or ::/0",
    "SG-NO-ADMIN-PORTS": "No ingress on 22 (SSH) or 3389 (RDP); admin access is through SSM",
    "RDS-SAFE": "RDS: encrypted with the KMS key, not public, deletion protection, backups >= 7 days, managed master password",
    "CACHE-VALKEY": "ElastiCache uses Valkey with at-rest and in-transit encryption",
    "CACHE-AUTH": "ElastiCache requires client authentication (RBAC user group with IAM users; no shared token in code)",
    "ALB-TLS": "Load balancer: HTTPS listener (ACM cert, TLS 1.2+), port 80 only redirects, HTTPS to targets, access logs on",
    "NO-SECRETS": "No hardcoded passwords, keys or tokens in any file",
    "SPEC-SERVERS": "Every active server in the target spec is built, and excluded servers are not",
    "SPEC-OS": "AMI lookups match the target OS (or use imported_ami_ids where no public AMI exists)",
    "SPEC-DB": "Databases are built only where the spec says (RDS engine matches; Oracle@AWS / TBD are not built)",
    "SPEC-CACHE": "Cache target matches the spec",
    "CI-STRUCTURE": "Pipeline has validate, plan and apply stages and passes the plan file to apply",
    "CI-OIDC": "Pipeline authenticates to AWS with GitLab OIDC (id_tokens), not static access keys",
    "CI-PROD-MANUAL": "Production apply is manual and limited to the default branch",
    "CI-PINNED-IMAGE": "Terraform image is pinned to a version (no :latest)",
    "DEPLOY-ARTIFACT-ACCESS": "If the pipeline deploys artifacts from S3, the instance role can read them (s3:GetObject)",
    "CI-STAGE-ORDER": "Pipeline stages run in the standard order: source -> build -> publish -> (infra validate/plan/apply) -> deploy -> test -> cutover",
    "CI-SOURCE-GATES": "Source stage runs secret, SAST and IaC scanning (Gitleaks, Semgrep, Checkov; njsscan for Node.js)",
    "CI-BUILD-SCA": "Build stage resolves dependencies reproducibly and runs SCA and unit tests for the app's build profile (vendor package checksum for COTS)",
    "CI-PUBLISH-SCAN": "Artifact is published to the artifact bucket (or ECR) and scanned (Trivy) before deploy",
    "CI-TEST-SUITE": "Test stage runs integration, DAST (OWASP ZAP) and performance (k6 or JMeter) tests against staging",
    "CI-GATES-BLOCK": "Security scanners are blocking gates (no allow_failure: true on scan jobs)",
    "CI-WEIGHTED-CUTOVER": "Cutover shifts traffic 5% -> 25% -> 100% with manual gates and has a rollback job that sets the AWS weight to 0",
    "TF-WEIGHTED-ROUTING": "Terraform has weighted Route 53 records (AWS weight = var.aws_traffic_weight), a health check and a CloudWatch alarm for auto-rollback",
    "NOTES-FLAGS": "MIGRATION_NOTES.md lists every spec flag ID verbatim",
    "CI-SCHEMA": "GitLab accepts the pipeline: every script line is a string, and needs only reference defined jobs in the same or an earlier stage",
}

PIPELINE_STAGES = ["source", "build", "publish", "deploy", "test", "cutover"]

# Required tools per build profile (pipeline standard). Matched case-insensitively in .gitlab-ci.yml.
PROFILES = {
    "nodejs": {"source": ["gitleaks", "semgrep", "checkov", "njsscan"], "build": ["npm ci", "retire", "npm run build", "npm test"]},
    "java":   {"source": ["gitleaks", "semgrep", "checkov"], "build": ["dependency-check", "test"]},
    "cots":   {"source": ["gitleaks", "semgrep", "checkov"], "build": ["sha256sum"]},
}
SCANNERS = ("gitleaks", "semgrep", "checkov", "njsscan", "retire", "dependency-check", "trivy", "zap")


def build_profile(spec):
    """nodejs | java | cots - decided from the spec in code, not by the model."""
    if spec.get("app_type") == "COTS": return "cots"
    rt = (spec.get("runtime") or {}).get("app_runtime", "")
    return "nodejs" if "node" in rt.lower() else "java"


SECRET_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS access key ID"),
    (r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+=]{30,}", "AWS secret key"),
    (r"(?i)\b(password|passwd|secret|token|api_key)\b\s*=\s*\"[^\"$]{4,}\"", "literal secret value"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
]


def _s(v):
    """hcl2 keeps quotes on string literals; strip them."""
    if isinstance(v, str) and len(v) >= 2 and v[0] == v[-1] == '"': return v[1:-1]
    return v


def _resources(tf):
    for block in tf.get("resource", []):
        for rtype, named in block.items():
            for name, body in named.items():
                yield _s(rtype), _s(name), body


def _datas(tf):
    for block in tf.get("data", []):
        for rtype, named in block.items():
            for name, body in named.items():
                yield _s(rtype), _s(name), body


class _GitLabLoader(yaml.SafeLoader):
    """SafeLoader that accepts GitLab's custom !reference tag (valid GitLab CI syntax)."""


_GitLabLoader.add_constructor("!reference", lambda loader, node: loader.construct_sequence(node))


def _locals(tf):
    out = {}
    for block in tf.get("locals", []):
        for k, v in block.items():
            if k != "__is_block__": out[_s(k)] = v
    return out


def _resolves_to(expr, needle, locs, depth=0):
    """True if expr mentions needle directly or through local values (up to 3 levels)."""
    text = str(expr)
    if needle in text: return True
    if depth >= 3: return False
    import re as _re
    return any(_resolves_to(locs[name], needle, locs, depth + 1)
               for name in _re.findall(r"local\.([A-Za-z0-9_]+)", text) if name in locs)


def _job(ci, name, seen=None):
    """Job with `extends` templates merged (job keys win), as GitLab does for top-level keys."""
    seen = seen or set()
    job = ci.get(name) or {}
    if not isinstance(job, dict) or name in seen: return job if isinstance(job, dict) else {}
    seen.add(name)
    ext = job.get("extends") or []
    merged = {}
    for parent in ([ext] if isinstance(ext, str) else ext):
        merged.update(_job(ci, parent, seen))
    merged.update(job)
    return merged


def _blocks(body, key):
    v = body.get(key, [])
    return v if isinstance(v, list) else [v]


def _truthy(v):
    return v is True or _s(v) in ("true", True)


def check(spec, files):
    """Returns (failures, passed) - failures is a list of 'POLICY-ID: detail'."""
    fails, ran = [], set()

    def fail(pid, detail): fails.append(f"{pid}: {detail}")

    main, outputs = files.get("main.tf", ""), files.get("outputs.tf", "")
    ci_text, notes = files.get(".gitlab-ci.yml", ""), files.get("MIGRATION_NOTES.md", "")
    ran.add("TF-PARSE")
    try:
        tf = hcl2.load(io.StringIO(main))
        hcl2.load(io.StringIO(outputs or ""))
    except Exception as e:
        fail("TF-PARSE", f"main.tf/outputs.tf do not parse: {str(e)[:300]}")
        return fails, []
    res = list(_resources(tf))
    locs = _locals(tf)
    by_type = {}
    for rtype, name, body in res: by_type.setdefault(rtype, []).append((name, body))

    # --- EC2
    ran |= {"EC2-IMDSV2", "EC2-EBS-ENC", "EC2-NO-PUBLIC-IP", "EC2-SSM"}
    computes = by_type.get("aws_instance", []) + by_type.get("aws_launch_template", [])
    for name, b in computes:
        md = _blocks(b, "metadata_options")
        if not md or _s(md[0].get("http_tokens")) != "required": fail("EC2-IMDSV2", f"{name} does not require IMDSv2")
        if _truthy(b.get("associate_public_ip_address")): fail("EC2-NO-PUBLIC-IP", f"{name} gets a public IP")
    for name, b in by_type.get("aws_instance", []):
        rbd = _blocks(b, "root_block_device")
        if not rbd or not _truthy(rbd[0].get("encrypted")) or "var.kms_key_arn" not in str(rbd[0].get("kms_key_id", "")):
            fail("EC2-EBS-ENC", f"{name} root volume not encrypted with var.kms_key_arn")
        for e in _blocks(b, "ebs_block_device"):
            if e and (not _truthy(e.get("encrypted")) or "var.kms_key_arn" not in str(e.get("kms_key_id", ""))):
                fail("EC2-EBS-ENC", f"{name} ebs_block_device not encrypted with var.kms_key_arn")
        if not _resolves_to(b.get("subnet_id", ""), "var.private_subnet_ids", locs): fail("EC2-NO-PUBLIC-IP", f"{name} subnet is not from var.private_subnet_ids")
        if not b.get("iam_instance_profile"): fail("EC2-SSM", f"{name} has no iam_instance_profile")
        if b.get("key_name"): fail("EC2-SSM", f"{name} uses an SSH key pair ({b.get('key_name')}); use SSM")
    for name, b in by_type.get("aws_ebs_volume", []):
        if not _truthy(b.get("encrypted")) or "var.kms_key_arn" not in str(b.get("kms_key_id", "")):
            fail("EC2-EBS-ENC", f"aws_ebs_volume.{name} not encrypted with var.kms_key_arn")
    if by_type.get("aws_instance") and "AmazonSSMManagedInstanceCore" not in main:
        fail("EC2-SSM", "no AmazonSSMManagedInstanceCore policy attachment")

    # --- security groups (inline, aws_security_group_rule, aws_vpc_security_group_ingress_rule)
    ran |= {"SG-NO-WORLD", "SG-NO-ADMIN-PORTS"}
    ingress = []
    for name, b in by_type.get("aws_security_group", []):
        ingress += [(f"aws_security_group.{name}", r) for r in _blocks(b, "ingress") if r]
    ingress += [(f"aws_security_group_rule.{n}", b) for n, b in by_type.get("aws_security_group_rule", []) if _s(b.get("type")) == "ingress"]
    ingress += [(f"aws_vpc_security_group_ingress_rule.{n}", b) for n, b in by_type.get("aws_vpc_security_group_ingress_rule", [])]
    for where, r in ingress:
        cidrs = [str(_s(c)) for c in (r.get("cidr_blocks") or [])] + [str(_s(c)) for c in (r.get("ipv6_cidr_blocks") or [])]
        cidrs += [str(_s(r.get(k))) for k in ("cidr_ipv4", "cidr_ipv6") if r.get(k)]
        if any(c in ("0.0.0.0/0", "::/0") for c in cidrs): fail("SG-NO-WORLD", f"{where} allows ingress from anywhere")
        try:
            lo, hi = int(_s(r.get("from_port", -1))), int(_s(r.get("to_port", -1)))
        except (TypeError, ValueError):
            continue
        for port in (22, 3389):
            if lo <= port <= hi and lo != -1: fail("SG-NO-ADMIN-PORTS", f"{where} opens port {port}")

    # --- RDS
    ran.add("RDS-SAFE")
    for name, b in by_type.get("aws_db_instance", []):
        problems = []
        if not _truthy(b.get("storage_encrypted")): problems.append("storage_encrypted")
        if "var.kms_key_arn" not in str(b.get("kms_key_id", "")): problems.append("kms_key_id")
        if _truthy(b.get("publicly_accessible")): problems.append("publicly_accessible")
        if not _truthy(b.get("deletion_protection")): problems.append("deletion_protection")
        try:
            if int(_s(b.get("backup_retention_period", 0))) < 7: problems.append("backup_retention_period<7")
        except (TypeError, ValueError):
            problems.append("backup_retention_period")
        if not _truthy(b.get("manage_master_user_password")): problems.append("manage_master_user_password")
        if b.get("password"): problems.append("literal password")
        if problems: fail("RDS-SAFE", f"aws_db_instance.{name}: {', '.join(problems)}")

    # --- ElastiCache
    ran.add("CACHE-VALKEY")
    for rtype in ("aws_elasticache_replication_group", "aws_elasticache_serverless_cache"):
        for name, b in by_type.get(rtype, []):
            if _s(b.get("engine", "")) != "valkey": fail("CACHE-VALKEY", f"{rtype}.{name} engine is not valkey")
            if rtype == "aws_elasticache_replication_group" and not (
                    _truthy(b.get("at_rest_encryption_enabled")) and _truthy(b.get("transit_encryption_enabled"))):
                fail("CACHE-VALKEY", f"{rtype}.{name} missing at-rest or in-transit encryption")
    if by_type.get("aws_elasticache_cluster"): fail("CACHE-VALKEY", "use a replication group or serverless cache, not aws_elasticache_cluster")
    # Found by the pilot's Checkov scan: encryption alone left the cache open to any client in the SG.
    ran.add("CACHE-AUTH")
    for name, b in by_type.get("aws_elasticache_replication_group", []):
        if not b.get("user_group_ids") and not b.get("auth_token"):
            fail("CACHE-AUTH", f"aws_elasticache_replication_group.{name} has no client authentication (user_group_ids with IAM users)")

    # --- load balancer: TLS end to end and access logs (found by the pilot's Semgrep/Checkov scans)
    ran.add("ALB-TLS")
    blocks = lambda v: v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])
    for name, b in by_type.get("aws_lb", []):
        if _s(b.get("load_balancer_type", "application")) != "application": continue
        if not any(_truthy(x.get("enabled", True)) and x.get("bucket") for x in blocks(b.get("access_logs"))):
            fail("ALB-TLS", f"aws_lb.{name} has no access_logs to var.alb_log_bucket_name")
    listeners = by_type.get("aws_lb_listener", [])
    if by_type.get("aws_lb") and not any(_s(b.get("protocol", "")) == "HTTPS" and b.get("certificate_arn")
                                         and "TLS13" in str(b.get("ssl_policy", "")) for _, b in listeners):
        fail("ALB-TLS", "no HTTPS listener with var.acm_certificate_arn and a TLS 1.2+/1.3 ssl_policy")
    for name, b in listeners:
        if _s(b.get("protocol", "")) == "HTTP" and not all(_s(a.get("type", "")) == "redirect" for a in blocks(b.get("default_action"))):
            fail("ALB-TLS", f"aws_lb_listener.{name} serves plain HTTP instead of redirecting to HTTPS")
    for name, b in by_type.get("aws_lb_target_group", []):
        if _s(b.get("protocol", "")) != "HTTPS":
            fail("ALB-TLS", f"aws_lb_target_group.{name} forwards to the servers without TLS (protocol must be HTTPS)")

    # --- secrets
    ran.add("NO-SECRETS")
    for fname, text in files.items():
        for pat, what in SECRET_PATTERNS:
            if re.search(pat, text): fail("NO-SECRETS", f"{fname} contains a {what}")

    # --- spec fidelity
    ran |= {"SPEC-SERVERS", "SPEC-OS", "SPEC-DB", "SPEC-CACHE"}
    for s in spec["servers"]:
        if s["build"] == "ec2" and s["server_id"] not in main:
            fail("SPEC-SERVERS", f"{s['server_id']} ({s['role']}) is not built")
    for s in spec["excluded_servers"]:
        if s["server_id"] in main: fail("SPEC-SERVERS", f"{s['server_id']} is excluded ({s['reason']}) but appears in main.tf")
    ami_filters = " ".join(str(b) for t, n, b in _datas(tf) if t == "aws_ami")
    for s in spec["servers"]:
        if s["build"] != "ec2": continue
        if s["ami_lookup"]:
            if s["ami_lookup"]["name"] not in ami_filters:
                fail("SPEC-OS", f"{s['server_id']} needs an aws_ami lookup for {s['target_os']} ({s['ami_lookup']['name']})")
        elif "imported_ami_ids" not in main:
            fail("SPEC-OS", f"{s['server_id']} ({s['target_os']}) has no public AMI; must use var.imported_ami_ids")
    rds = by_type.get("aws_db_instance", [])
    want_rds = [d for d in spec["databases"] if d["build"] == "rds"]
    engines = [_s(b.get("engine", "")) for _, b in rds]
    for d in want_rds:
        if not any(str(e).startswith(d["rds_engine"].split("-")[0]) for e in engines):
            fail("SPEC-DB", f"{d['db_id']} should be {d['target']} (engine {d['rds_engine']})")
    if len(rds) > len(want_rds):
        fail("SPEC-DB", f"{len(rds)} aws_db_instance resources but the spec has {len(want_rds)} RDS databases")
    if any("oracle" in str(e) for e in engines):
        fail("SPEC-DB", "Oracle databases are not built here (Oracle Database@AWS handoff or a pending human decision)")
    has_valkey = bool(by_type.get("aws_elasticache_replication_group") or by_type.get("aws_elasticache_serverless_cache"))
    if (spec["cache"] == "Amazon ElastiCache for Valkey") != has_valkey:
        fail("SPEC-CACHE", f"spec cache is {spec['cache'] or 'none'} but ElastiCache present={has_valkey}")

    # --- GitLab CI
    ran |= {"CI-STRUCTURE", "CI-OIDC", "CI-PROD-MANUAL", "CI-PINNED-IMAGE"}
    try:
        ci = yaml.load(ci_text, Loader=_GitLabLoader) or {}
        if not isinstance(ci, dict): raise ValueError("top level is not a mapping")
    except Exception as e:
        fail("CI-STRUCTURE", f".gitlab-ci.yml does not parse: {str(e)[:200]}")
        ci = {}
    if ci:
        stages = [str(s).lower() for s in ci.get("stages", [])]
        for need in ("validate", "plan", "apply"):
            if not any(need in s for s in stages): fail("CI-STRUCTURE", f"no '{need}' stage")
        jobs = {k: v for k, v in ci.items() if isinstance(v, dict) and ("script" in v or "extends" in v or "trigger" in v)}
        if "tfplan" not in ci_text and "-out" not in ci_text: fail("CI-STRUCTURE", "plan output is not saved and passed to apply")
        if "id_tokens" not in ci_text: fail("CI-OIDC", "no id_tokens (GitLab OIDC) for AWS authentication")
        if re.search(r"AWS_SECRET_ACCESS_KEY\s*:", ci_text): fail("CI-OIDC", "static AWS_SECRET_ACCESS_KEY variable")
        jobs = {n: _job(ci, n) for n in jobs}
        prod = [(n, j) for n, j in jobs.items() if "prod" in n.lower() or "prod" in str(j.get("environment", "")).lower()]
        applies = [(n, j) for n, j in prod if "apply" in n.lower() or "deploy" in n.lower() or "promote" in n.lower()]
        if not applies: fail("CI-PROD-MANUAL", "no production apply/deploy job")
        is_manual = lambda j: "manual" in str(j.get("rules", "")) + str(j.get("when", ""))
        for n, j in applies:
            rules_text = str(j.get("rules", "")) + str(j.get("when", "")) + str(j.get("only", ""))
            needs = [x if isinstance(x, str) else x.get("job", "") for x in (j.get("needs") or [])]
            gated_by = [x for x in needs if x in jobs and is_manual(jobs[x]) and (x, jobs[x]) in prod]
            if not is_manual(j) and not gated_by:
                fail("CI-PROD-MANUAL", f"job {n} is not manual and does not need a manual production job")
            if "CI_DEFAULT_BRANCH" not in rules_text and "main" not in rules_text: fail("CI-PROD-MANUAL", f"job {n} is not limited to the default branch")
        images = []
        for k, v in ci.items():
            if isinstance(v, dict) and "image" in v:
                img = v["image"]
                images.append(str(img.get("name", "") if isinstance(img, dict) else img))
        if "image" in ci: images.append(str(ci["image"].get("name", "") if isinstance(ci["image"], dict) else ci["image"]))
        tf_images = [i for i in images if "terraform" in i or "opentofu" in i]
        if not tf_images: fail("CI-PINNED-IMAGE", "no terraform image")
        for i in tf_images:
            if ":" not in i.split("/")[-1] or i.endswith(":latest"): fail("CI-PINNED-IMAGE", f"image {i} is not pinned")
        # GitLab's own schema rules (found by the live pilot: both made GitLab reject the pipeline)
        ran.add("CI-SCHEMA")
        stage_of = lambda j: str(j.get("stage", "test"))
        for k, v in ci.items():
            if not isinstance(v, dict): continue
            for f in ("script", "before_script", "after_script"):
                for s in (v.get(f) or []) if isinstance(v.get(f), list) else []:
                    if not isinstance(s, (str, list)):
                        fail("CI-SCHEMA", f"{k}.{f} has a non-string line {str(s)[:80]} (quote lines containing ': ')")
        for n, j in jobs.items():
            for x in j.get("needs") or []:
                optional = isinstance(x, dict) and x.get("optional")
                x = x if isinstance(x, str) else x.get("job", "")
                if x not in jobs:
                    if optional: continue
                    fail("CI-SCHEMA", f"{n} needs undefined job {x}")
                elif stage_of(jobs[x]) in stages and stage_of(j) in stages and \
                        stages.index(stage_of(jobs[x])) > stages.index(stage_of(j)):
                    fail("CI-SCHEMA", f"{n} ({stage_of(j)}) needs {x}, which runs in a later stage ({stage_of(jobs[x])})")

    # --- deploy path: what the pipeline pulls onto the servers, the instance role must be able to read
    ran.add("DEPLOY-ARTIFACT-ACCESS")
    if re.search(r"s3://|aws s3 (cp|sync)", ci_text) and "s3:GetObject" not in main:
        fail("DEPLOY-ARTIFACT-ACCESS", "pipeline copies artifacts from S3 onto the instances, but the instance role has no s3:GetObject")

    # --- pipeline standard (six stages, gates, weighted cutover)
    ran |= {"CI-STAGE-ORDER", "CI-SOURCE-GATES", "CI-BUILD-SCA", "CI-PUBLISH-SCAN", "CI-TEST-SUITE",
            "CI-GATES-BLOCK", "CI-WEIGHTED-CUTOVER", "TF-WEIGHTED-ROUTING"}
    low = ci_text.lower()
    prof = PROFILES[build_profile(spec)]
    if ci:
        stages = [str(s).lower() for s in ci.get("stages", [])]
        pos = []
        for want in PIPELINE_STAGES:
            idx = [i for i, s in enumerate(stages) if want in s]
            if not idx: fail("CI-STAGE-ORDER", f"no '{want}' stage"); pos.append(None)
            else: pos.append(idx[0])
        found = [p for p in pos if p is not None]
        if found != sorted(found): fail("CI-STAGE-ORDER", f"stages out of order: {stages}")
        dep = next((i for i, s in enumerate(stages) if "deploy" in s), None)
        for infra in ("validate", "plan", "apply"):
            i = next((k for k, s in enumerate(stages) if infra in s), None)
            if i is not None and dep is not None and i > dep: fail("CI-STAGE-ORDER", f"'{infra}' runs after deploy")
        all_jobs = {n: _job(ci, n) for n, v in ci.items() if isinstance(v, dict) and not n.startswith(".")}
        for n, j in all_jobs.items():
            body = (str(j.get("script", "")) + str(j.get("image", ""))).lower()
            if any(t in body for t in SCANNERS) and j.get("allow_failure") in (True, "true"):
                fail("CI-GATES-BLOCK", f"scan job {n} has allow_failure: true")
    for tool in prof["source"]:
        if tool not in low: fail("CI-SOURCE-GATES", f"no {tool} in the source stage")
    for step in prof["build"]:
        if step not in low: fail("CI-BUILD-SCA", f"build profile {build_profile(spec)} requires '{step}'")
    if not re.search(r"aws s3 cp .*(artifact_bucket|\$\{?artifact)|ecr", low):
        fail("CI-PUBLISH-SCAN", "artifact is not published to the artifact bucket or ECR")
    if "trivy" not in low: fail("CI-PUBLISH-SCAN", "published artifact is not scanned with trivy")
    if "zap" not in low: fail("CI-TEST-SUITE", "no DAST (OWASP ZAP) test")
    if not re.search(r"\bk6\b|jmeter", low): fail("CI-TEST-SUITE", "no performance test (k6 or JMeter)")
    if "integration" not in low: fail("CI-TEST-SUITE", "no integration test job")
    # Resolve each job's script with its variables ($WEIGHT, ${WEIGHT}) so templated cutover jobs count.
    resolved = {}
    if ci:
        glob_vars = ci.get("variables", {}) if isinstance(ci.get("variables"), dict) else {}
        for n in ci:
            if not isinstance(ci[n], dict) or n.startswith("."): continue
            j = _job(ci, n)
            text = " ".join(str(j.get(k, "")) for k in ("before_script", "script", "after_script"))
            vars_ = {**glob_vars, **(j.get("variables") or {})}
            for k, v in vars_.items():
                v = v.get("value", "") if isinstance(v, dict) else v
                text = re.sub(r"\$\{?" + re.escape(str(k)) + r"\}?(?![A-Za-z0-9_])", str(v), text)
            resolved[n] = (j, text)
    alltext = ci_text + " " + " ".join(t for _, t in resolved.values())
    for w in ("5", "25", "100"):
        if not re.search(rf"aws_traffic_weight\s*=\s*{w}\b", alltext):
            fail("CI-WEIGHTED-CUTOVER", f"no cutover step setting aws_traffic_weight={w}")
    if not re.search(r"aws_traffic_weight\s*=\s*0\b", alltext) or "rollback" not in low:
        fail("CI-WEIGHTED-CUTOVER", "no rollback job setting aws_traffic_weight=0")
    for n, (j, text) in resolved.items():
        if re.search(r"aws_traffic_weight\s*=\s*(5|25|100)\b", text) and "manual" not in str(j.get("rules", "")) + str(j.get("when", "")):
            fail("CI-WEIGHTED-CUTOVER", f"cutover job {n} is not manual")
    r53 = by_type.get("aws_route53_record", [])
    weighted = [b for _, b in r53 if _blocks(b, "weighted_routing_policy") and _blocks(b, "weighted_routing_policy")[0]]
    if len(weighted) < 2: fail("TF-WEIGHTED-ROUTING", "needs two weighted Route 53 records (legacy + AWS)")
    if weighted and not any("var.aws_traffic_weight" in str(b) for b in weighted):
        fail("TF-WEIGHTED-ROUTING", "AWS weighted record does not use var.aws_traffic_weight")
    if not by_type.get("aws_route53_health_check"): fail("TF-WEIGHTED-ROUTING", "no aws_route53_health_check")
    if not by_type.get("aws_cloudwatch_metric_alarm"): fail("TF-WEIGHTED-ROUTING", "no aws_cloudwatch_metric_alarm for auto-rollback")

    # --- notes
    ran.add("NOTES-FLAGS")
    for f in spec["flags"]:
        if f not in notes: fail("NOTES-FLAGS", f"flag {f} not listed in MIGRATION_NOTES.md")

    failed_ids = {x.split(":")[0] for x in fails}
    return fails, sorted(ran - failed_ids)
