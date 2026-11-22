"""Deterministic target specification for one application (mapping_rules.yaml applied in code).

The Builder agent does not decide WHAT an application becomes on AWS - this module does, from the
inventory record returned by the inventory MCP server's get_app tool. Claude then writes Terraform and
GitLab CI that implement this spec, and the Reviewer checks the code against it. Keeping the mapping in
code means a model can't quietly change a target (e.g. put an Exadata database on RDS).
"""

CICD_TARGET = "GitLab CI/CD + Terraform + Claude (AI-assisted code generation)"

# AMI lookups for the operating systems this portfolio needs. None = no supported public AMI on AWS:
# the server must be imported as-is (AWS Application Migration Service / VM Import), so the Terraform
# takes the AMI ID as a variable instead of looking one up.
AMI_LOOKUP = {
    "RHEL 9": {"owner": "309956199498", "name": "RHEL-9.*_HVM-*-x86_64-*"},
    "RHEL 8": {"owner": "309956199498", "name": "RHEL-8.*_HVM-*-x86_64-*"},
    "Windows Server 2022": {"owner": "amazon", "name": "Windows_Server-2022-English-Full-Base-*"},
    "Windows Server 2019": {"owner": "amazon", "name": "Windows_Server-2019-English-Full-Base-*"},
    "Windows Server 2016": {"owner": "amazon", "name": "Windows_Server-2016-English-Full-Base-*"},
    "RHEL 6": None,
    "RHEL 7": None,
}
EOL_OS = {"RHEL 6", "RHEL 7", "Windows Server 2016", "Windows Server 2012 R2"}


def instance_type(vcpu, memory_gb):
    """Like-for-like sizing: family from the memory:vCPU ratio, size from vCPUs (right-size after a month of CloudWatch data)."""
    vcpu, mem = int(vcpu), int(memory_gb)
    ratio = mem / max(vcpu, 1)
    family = "c7i" if ratio <= 2 else "m7i" if ratio <= 4 else "r7i"
    size = {1: "large", 2: "large", 4: "xlarge", 8: "2xlarge", 16: "4xlarge", 32: "8xlarge"}.get(vcpu, "xlarge")
    return f"{family}.{size}"


def server_target(p, s):
    """Mirror of mapping_rules.yaml for one server. Returns (target, target_os, flags)."""
    flags, role, os_ = [], s["role"], s["os"]
    if role == "oke-node": return "Amazon EKS", "EKS-managed node image", flags
    if role == "db":
        e = p.get("database_engine", "")
        if e == "MySQL": return "Amazon RDS for MySQL", "n/a (managed)", flags
        if e == "SQL Server": return "Amazon RDS for SQL Server", "n/a (managed)", flags
        if e == "Oracle":
            flags.append("oracle_target_decision_needed")
            return "Oracle Database@AWS or Amazon RDS for Oracle (TBD)", "Oracle Linux (unchanged) if self-managed", flags
        if e in ("MongoDB", "Cassandra"):
            if p["app_type"] == "COTS":
                flags.append("vendor_must_confirm_os")
                return f"{e} on EC2", os_, flags
            return f"{e} on EC2", "RHEL 9", flags
    if role == "search": return "Elasticsearch on EC2", "RHEL 9", flags
    if p["app_type"] == "COTS":
        flags.append("vendor_must_confirm_os")
        if os_ in ("RHEL 6", "Windows Server 2016"): flags.append("end_of_life_os_carried_over")
        if os_.startswith("Oracle Linux"): flags.append("rule_conflict_cots_keep_os_vs_oel_app_to_rhel9")
        return "Amazon EC2", os_, flags
    if os_.startswith("Windows"): return "Amazon EC2", "Windows Server 2022", flags
    return "Amazon EC2", "RHEL 9", flags


def db_target(d):
    if d["engine"] == "Oracle":
        if "Exadata" in d["oci_service"] or "Autonomous" in d["oci_service"]: return "Oracle Database@AWS"
        return "Oracle Database@AWS or Amazon RDS for Oracle (TBD - needs human decision)"
    return {"MySQL": "Amazon RDS for MySQL", "SQL Server": "Amazon RDS for SQL Server",
            "MongoDB": "MongoDB on EC2", "Cassandra": "Cassandra on EC2"}[d["engine"]]


def build_spec(app):
    """app = get_app() result from the inventory MCP server. Returns the target spec dict."""
    p = app["profile"]
    servers, excluded = [], []
    for s in sorted(app["servers"], key=lambda s: s["server_id"]):
        if s.get("possibly_decommissioned"):
            excluded.append({"server_id": s["server_id"], "reason": "possibly decommissioned - confirm with owner before building"})
            continue
        target, tos, flags = server_target(p, s)
        entry = {"server_id": s["server_id"], "hostname": s["hostname"], "role": s["role"], "target": target,
                 "target_os": tos, "flags": flags}
        if target == "Amazon EC2" or target.endswith(" on EC2"):
            entry["build"] = "ec2"
            entry["instance_type"] = instance_type(s["vcpu"], s["memory_gb"])
            entry["root_volume_gb"] = 100 if tos.startswith("Windows") else 50
            entry["data_volume_gb"] = int(s["storage_gb"])
            entry["ami_lookup"] = AMI_LOOKUP.get(tos)
            if entry["ami_lookup"] is None:
                entry["ami_strategy"] = "imported_image_variable"
                flags.append("no_public_ami_import_with_mgn")
            if tos in EOL_OS: flags.append("end_of_life_os")
        else:
            entry["build"] = "managed_service"   # the DB server becomes the managed database below
        servers.append(entry)

    databases = []
    for d in app.get("databases", []):
        t = db_target(d)
        db = {"db_id": d["db_id"], "engine": d["engine"], "version": d.get("version", ""),
              "size_gb": int(d.get("size_gb") or 0), "target": t, "shared_with": d.get("shared_with") or ""}
        if t.startswith("Amazon RDS"):
            db["build"] = "rds"
            db["rds_engine"] = {"Amazon RDS for MySQL": "mysql", "Amazon RDS for SQL Server": "sqlserver-se"}[t]
        elif t.endswith(" on EC2"):
            db["build"] = "ec2_db_server"     # built from the db-role server above
        elif t == "Oracle Database@AWS":
            db["build"] = "handoff_odb"       # provisioned by the Oracle Database@AWS workstream, not this module
        else:
            db["build"] = "hold_decision_needed"
        databases.append(db)

    cache = p.get("cache_search", "")
    flags = sorted({f for s in servers for f in s["flags"]})
    if p.get("security_level") == "Restricted" or p.get("pci_scope") == "Y":
        flags.append("security_review_required" + ("" if p.get("security_review") == "Complete" else "_not_complete"))
    if "stale" in p.get("flags", []): flags.append("stale_record_over_90_days")
    if p.get("secrets_store") == "config file": flags.append("plaintext_secret_move_to_secrets_manager")
    if any(d["build"] == "hold_decision_needed" for d in databases): flags.append("database_target_decision_needed")
    if any(d["build"] == "handoff_odb" for d in databases): flags.append("oracle_database_at_aws_handoff")
    return {
        "app_id": app["app_id"], "app_name": p.get("app_name", ""), "business_unit": p.get("business_unit", ""),
        "app_type": p["app_type"], "cots_product": p.get("cots_product", ""),
        "security_level": p.get("security_level", ""), "pci_scope": p.get("pci_scope", ""),
        "criticality_tier": int(p.get("criticality_tier", 3)),
        "compute": "Amazon EKS" if p.get("deployment") == "OKE" else "Amazon EC2",
        "runtime": {"app_runtime": p.get("app_runtime", ""), "jdk": p.get("jdk", ""), "web_server": p.get("web_server", ""),
                    "rule": "like-for-like on EC2, same JDK version"},
        "servers": servers, "excluded_servers": excluded, "databases": databases,
        "cache": ("Amazon ElastiCache for Valkey" if cache == "Redis" else
                  "Elasticsearch on EC2" if cache == "Elasticsearch" else ""),
        "messaging": f"{p['messaging']} on EC2" if p.get("messaging") else "",
        "secrets": "AWS Secrets Manager", "cicd": CICD_TARGET, "flags": sorted(set(flags)),
    }
