"""Offline test of the build-stage flow: fake Claude, fake inventory (from the synthetic CSVs), fake terraform.

    python test_local.py /path/to/synthetic_portfolio

Checks: approval gate blocks building; policy checks pass a compliant change set and reject a bad one;
a rejected change set goes back to the Builder and is fixed; a terraform validate failure is retried;
move-group integrity holds a partner; ownership and stage-transition rules are enforced; every message
validates against the contract.
"""
import csv, json, os, shutil, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flow as F
import policy
from agents import Builder, Reviewer, Validation
from contracts import Message, Stage
from plan_store import OwnershipError, PlanStore
from target_spec import build_spec

PORTFOLIO = sys.argv[1] if len(sys.argv) > 1 else "/home/claude/synthetic_portfolio"


def inventory():
    D = PORTFOLIO + "/data/"
    A = {r["app_id"]: r for r in csv.DictReader(open(D + "applications.csv"))}
    S = list(csv.DictReader(open(D + "servers.csv"))); DB = list(csv.DictReader(open(D + "databases.csv")))
    apps = {a: {"app_id": a, "found": True, "profile": dict(p, flags=[]), "servers": [s for s in S if s["app_id"] == a],
                "databases": [d for d in DB if d["app_id"] == a]} for a, p in A.items()}
    mg = json.load(open(PORTFOLIO + "/answer_key/expected_move_groups.json"))
    mg = mg if isinstance(mg, list) else mg.get("groups", mg.get("move_groups"))
    groups = {g["group_id"]: {"group_id": g["group_id"], "apps": g["apps"]} for g in mg}
    return groups, apps


def good_files(spec):
    """A compliant change set generated from the spec (stands in for Claude)."""
    ec2 = [s for s in spec["servers"] if s["build"] == "ec2"]
    oses = {s["target_os"]: s["ami_lookup"] for s in ec2 if s["ami_lookup"]}
    tf = ["locals {", "  servers = {"]
    for s in ec2:
        ami = (f'data.aws_ami.os_{abs(hash(s["target_os"])) % 997}.id' if s["ami_lookup"]
               else f'var.imported_ami_ids["{s["server_id"]}"]')
        tf.append(f'    "{s["server_id"]}" = {{ role = "{s["role"]}", type = "{s["instance_type"]}", ami = {ami}, data_gb = {s["data_volume_gb"]} }}')
    tf += ["  }", "}"]
    for os_, lk in oses.items():
        tf += [f'data "aws_ami" "os_{abs(hash(os_)) % 997}" {{', "  most_recent = true", f'  owners = ["{lk["owner"]}"]',
               "  filter {", '    name   = "name"', f'    values = ["{lk["name"]}"]', "  }", "}"]
    tf += ['resource "aws_iam_role" "app" {', '  name = "${var.app_id}-ec2"',
           '  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Principal = { Service = "ec2.amazonaws.com" } }] })', "}",
           'resource "aws_iam_role_policy_attachment" "ssm" {', "  role       = aws_iam_role.app.name",
           '  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"', "}",
           'resource "aws_iam_role_policy" "artifacts" {', "  role = aws_iam_role.app.id",
           '  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${var.artifact_bucket_name}/${var.app_id}/*" }] })', "}",
           'resource "aws_iam_instance_profile" "app" {', '  name = "${var.app_id}-ec2"', "  role = aws_iam_role.app.name", "}",
           'resource "aws_security_group" "app" {', '  name   = "${var.app_id}-app"', "  vpc_id = var.vpc_id", "}",
           'resource "aws_vpc_security_group_ingress_rule" "app_from_onprem" {', "  security_group_id = aws_security_group.app.id",
           "  cidr_ipv4   = var.onprem_cidrs[0]", "  from_port   = 8080", "  to_port     = 8080", '  ip_protocol = "tcp"', "}",
           'resource "aws_instance" "server" {', "  for_each               = local.servers", "  ami                    = each.value.ami",
           "  instance_type          = each.value.type", "  subnet_id              = var.private_subnet_ids[0]",
           "  iam_instance_profile   = aws_iam_instance_profile.app.name", "  vpc_security_group_ids = [aws_security_group.app.id]",
           "  metadata_options {", '    http_tokens = "required"', "  }",
           "  root_block_device {", "    encrypted  = true", "    kms_key_id = var.kms_key_arn", "  }",
           "  ebs_block_device {", '    device_name = "/dev/sdf"', "    volume_size = each.value.data_gb", "    encrypted   = true",
           "    kms_key_id  = var.kms_key_arn", "  }", "  tags = { server_id = each.key, role = each.value.role }", "}",
           'resource "aws_secretsmanager_secret" "app" {', '  name       = "${var.app_id}/app"', "  kms_key_id = var.kms_key_arn", "}",
           'resource "aws_route53_health_check" "aws" {', '  fqdn = "alb.internal"', '  type = "HTTPS"', "}",
           'resource "aws_cloudwatch_metric_alarm" "rollback" {', '  alarm_name = "${var.app_id}-5xx"', "}",
           'resource "aws_route53_record" "legacy" {', "  zone_id = var.route53_zone_id", "  name = var.app_dns_name",
           '  type = "CNAME"', "  ttl = 60", '  set_identifier = "legacy"', "  records = [var.legacy_endpoint]",
           "  weighted_routing_policy {", "    weight = 100 - var.aws_traffic_weight", "  }", "}",
           'resource "aws_route53_record" "aws" {', "  zone_id = var.route53_zone_id", "  name = var.app_dns_name",
           '  type = "CNAME"', "  ttl = 60", '  set_identifier = "aws"', '  records = ["alb.internal"]',
           "  health_check_id = aws_route53_health_check.aws.id",
           "  weighted_routing_policy {", "    weight = var.aws_traffic_weight", "  }", "}"]
    for d in spec["databases"]:
        if d["build"] == "rds":
            tf += [f'resource "aws_db_instance" "db" {{', f'  engine = "{d["rds_engine"]}"', '  instance_class = "db.m7i.xlarge"',
                   "  allocated_storage = 100", "  storage_encrypted = true", "  kms_key_id = var.kms_key_arn",
                   "  publicly_accessible = false", "  deletion_protection = true", "  backup_retention_period = 7",
                   "  manage_master_user_password = true", '  username = "admin"', "}"]
    if spec["cache"] == "Amazon ElastiCache for Valkey":
        tf += ['resource "aws_elasticache_replication_group" "cache" {', '  replication_group_id = "${lower(var.app_id)}-cache"',
               '  description = "app cache"', '  engine = "valkey"', '  node_type = "cache.m7g.large"',
               "  at_rest_encryption_enabled = true", "  transit_encryption_enabled = true", "  kms_key_id = var.kms_key_arn",
               "  user_group_ids = [aws_elasticache_user_group.cache.user_group_id]", "}"]
    prof = policy.build_profile(spec)
    build = {"nodejs": ["npm ci", "npx retire --exitwith 1", "npm run build", "npm test", "npm prune --production"],
             "java": ["mvn -B dependency-check:check", "mvn -B test package"],
             "cots": ["aws s3 cp s3://$ARTIFACT_BUCKET/vendor/pkg.tgz .", "sha256sum -c pkg.sha256"]}[prof]
    gates = ["gitleaks detect --source .", "semgrep ci", "checkov -d ."] + (["njsscan ."] if prof == "nodejs" else [])
    ci = f"""stages: [source_scan, build, publish, infra_validate, infra_plan, infra_apply, deploy, test, cutover]
default:
  id_tokens:
    AWS_ID_TOKEN:
      aud: https://gitlab.com
source_gates:
  stage: source_scan
  image: semgrep/semgrep:1.99.0
  script: {gates}
build_app:
  stage: build
  image: node:18.20.4
  script: {build}
publish_artifact:
  stage: publish
  image: aquasec/trivy:0.57.1
  script: [trivy fs --exit-code 1 --severity HIGH,CRITICAL dist/, aws s3 cp app.zip s3://$ARTIFACT_BUCKET/app/]
validate:
  stage: infra_validate
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform init -backend=false, terraform validate]
plan_staging:
  stage: infra_plan
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform plan -out=tfplan]
  artifacts: {{paths: [tfplan]}}
apply_staging:
  stage: infra_apply
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform apply tfplan]
  rules: [{{if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'}}]
apply_production:
  stage: infra_apply
  environment: production
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform apply tfplan]
  rules: [{{if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH', when: manual}}]
deploy_green:
  stage: deploy
  script: [aws ssm send-command --document-name AWS-RunShellScript]
integration_tests:
  stage: test
  script: [./run-integration-tests.sh]
dast_zap:
  stage: test
  image: ghcr.io/zaproxy/zaproxy:2.15.0
  script: [zap-baseline.py -t https://staging]
perf_k6:
  stage: test
  image: grafana/k6:0.55.0
  script: [k6 run perf.js]
cutover_5:
  stage: cutover
  environment: production
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform apply -auto-approve -var aws_traffic_weight=5]
  rules: [{{if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH', when: manual}}]
cutover_25:
  stage: cutover
  environment: production
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform apply -auto-approve -var aws_traffic_weight=25]
  rules: [{{if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH', when: manual}}]
cutover_100:
  stage: cutover
  environment: production
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform apply -auto-approve -var aws_traffic_weight=100]
  rules: [{{if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH', when: manual}}]
rollback:
  stage: cutover
  environment: production
  image: {{name: hashicorp/terraform:1.9.8}}
  script: [terraform apply -auto-approve -var aws_traffic_weight=0]
  when: on_failure
"""
    notes = "# Migration notes\n## Flags\n" + "\n".join(f"- {f}" for f in spec["flags"]) + "\n"
    return {"main.tf": "\n".join(tf) + "\n", "outputs.tf": 'output "secret_arn" {\n  value = aws_secretsmanager_secret.app.arn\n}\n',
            ".gitlab-ci.yml": ci, "MIGRATION_NOTES.md": notes}


class FakeLLM:
    """Builder: CLM-FRAU1's first change set opens SSH to the world (must be rejected, then fixed);
    CS-SURV2's first attempt fails terraform validate. Reviewer: approves with one medium finding."""
    def __init__(self): self.calls, self.seen = 0, {}

    def json(self, system, user, schema, name, max_tokens=0):
        self.calls += 1
        if name.startswith("build"):
            spec = json.loads(user.split("Target spec (authoritative):\n", 1)[1].split("\n\nvariables.tf", 1)[0])
            files = good_files(spec)
            part = list(schema["properties"]["files"]["required"])
            if part == [".gitlab-ci.yml", "MIGRATION_NOTES.md"]:
                return {"files": {f: files[f] for f in part}, "summary": "pipeline", "assumptions": []}
            n = self.seen[spec["app_id"]] = self.seen.get(spec["app_id"], 0) + 1
            if spec["app_id"] == "CLM-FRAU1" and n == 1:
                files["main.tf"] += ('resource "aws_security_group_rule" "ssh" {\n  type = "ingress"\n  from_port = 22\n  to_port = 22\n'
                                     '  protocol = "tcp"\n  cidr_blocks = ["0.0.0.0/0"]\n  security_group_id = aws_security_group.app.id\n}\n')
            if spec["app_id"] == "CS-SURV2" and n == 1: files["main.tf"] += "BROKEN"
            return {"files": {f: files[f] for f in part}, "summary": f"{spec['app_id']} built", "assumptions": []}
        return {"verdict": "approve", "findings": [{"severity": "medium", "file": "main.tf", "issue": "single AZ", "fix": "spread subnets"}]}

    def text(self, system, user, max_tokens=0):
        self.calls += 1
        return "- Fake summary."

    def usage(self): return {"calls": self.calls, "input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": 0}


class FakeTF:
    def validate(self, wd):
        return ("BROKEN" not in open(os.path.join(wd, "main.tf")).read(),
                "Error: Unsupported block type" if "BROKEN" in open(os.path.join(wd, "main.tf")).read() else "Success!")


def main():
    ok = lambda c, m: print(("PASS " if c else "FAIL ") + m) or c
    results = []
    groups, apps = inventory()

    # 1. policy on every app's compliant change set
    bad = {a: policy.check(build_spec(apps[a]), good_files(build_spec(apps[a])))[0] for a in apps}
    results.append(ok(not any(bad.values()), f"compliant change sets pass all policies for {len(apps)} apps "
                      + str({k: v[:2] for k, v in bad.items() if v})))
    sp = build_spec(apps["HR-RECR1"]); f = good_files(sp)
    f["main.tf"] += 'resource "aws_db_instance" "ora" {\n  engine = "oracle-ee"\n  password = "Sup3rSecret!"\n}\n'
    fails, _ = policy.check(sp, f)
    results.append(ok({"SPEC-DB", "NO-SECRETS", "RDS-SAFE"} <= {x.split(":")[0] for x in fails}, f"bad Oracle-on-RDS change set rejected: {sorted({x.split(':')[0] for x in fails})}"))

    sp = build_spec(apps["OPS-ASSE4"]); f = good_files(sp)
    f[".gitlab-ci.yml"] = (f[".gitlab-ci.yml"].replace("checkov -d .", "echo skip")
                           .replace("  script: [trivy fs", "  allow_failure: true\n  script: [trivy fs")
                           .replace("aws_traffic_weight=25]\n  rules: [{if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH', when: manual}]",
                                    "aws_traffic_weight=25]"))
    ids = {x.split(":")[0] for x in policy.check(sp, f)[0]}
    results.append(ok({"CI-SOURCE-GATES", "CI-GATES-BLOCK", "CI-WEIGHTED-CUTOVER"} <= ids,
                      f"pipeline without Checkov, with a non-blocking scanner and an automatic 25% cutover rejected: {sorted(ids)}"))
    # Defects GitLab itself rejected in the live pilot: an unquoted ': ' in a script line, and a
    # deploy job that needs a test-stage job.
    sp = build_spec(apps["CLM-FRAU1"]); f = good_files(sp)
    f[".gitlab-ci.yml"] += ('\nrollback_note:\n  stage: cutover\n  script:\n    - echo "Rolled back: traffic on legacy"\n'
                            '\ndeploy_after_tests:\n  stage: deploy\n  needs: [integration_tests]\n  script: [echo deploy]\n')
    fails = [x for x in policy.check(sp, f)[0] if x.startswith("CI-SCHEMA")]
    results.append(ok(len(fails) == 2, f"GitLab schema defects rejected (unquoted colon, later-stage need): {len(fails)} CI-SCHEMA findings"))
    # The load-balancer and cache gaps the pilot's scanners found (CLM-FRAU1 as first generated) vs the fix.
    sp = build_spec(apps["CLM-FRAU1"])
    insecure = ('resource "aws_lb" "web" {\n  internal = true\n  load_balancer_type = "application"\n}\n'
                'resource "aws_lb_target_group" "web" {\n  port = 80\n  protocol = "HTTP"\n}\n'
                'resource "aws_lb_listener" "http" {\n  port = 80\n  protocol = "HTTP"\n'
                '  default_action {\n    type = "forward"\n  }\n}\n')
    secure = ('resource "aws_lb" "web" {\n  internal = true\n  load_balancer_type = "application"\n'
              '  access_logs {\n    bucket = var.alb_log_bucket_name\n    enabled = true\n  }\n}\n'
              'resource "aws_lb_target_group" "web" {\n  port = 8443\n  protocol = "HTTPS"\n}\n'
              'resource "aws_lb_listener" "https" {\n  port = 443\n  protocol = "HTTPS"\n'
              '  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"\n  certificate_arn = var.acm_certificate_arn\n'
              '  default_action {\n    type = "forward"\n  }\n}\n'
              'resource "aws_lb_listener" "http_redirect" {\n  port = 80\n  protocol = "HTTP"\n'
              '  default_action {\n    type = "redirect"\n  }\n}\n')
    f = good_files(sp); f["main.tf"] += insecure
    f["main.tf"] = f["main.tf"].replace("  user_group_ids = [aws_elasticache_user_group.cache.user_group_id]\n", "")
    bad_ids = [x.split(":")[0] for x in policy.check(sp, f)[0]]
    f = good_files(sp); f["main.tf"] += secure
    good_ids = [x.split(":")[0] for x in policy.check(sp, f)[0]]
    results.append(ok(bad_ids.count("ALB-TLS") == 4 and "CACHE-AUTH" in bad_ids and not good_ids,
                      f"pilot scan findings caught offline: {bad_ids.count('ALB-TLS')} ALB-TLS + CACHE-AUTH; remediated version passes {good_ids}"))
    results.append(ok({policy.build_profile(build_spec(apps[a])) for a in ("OPS-ASSE4", "CLM-FRAU1", "CS-SURV2")} == {"cots", "java"}
                      and policy.build_profile({"app_type": "Homegrown", "runtime": {"app_runtime": "Node.js 18"}}) == "nodejs",
                      "build profiles: COTS -> cots, Java runtimes -> java, Node.js -> nodejs"))

    # 2. plan store rules
    tmp = tempfile.mkdtemp()
    st = PlanStore(tmp); st.create("FIN-EXP", {"wave": "wave-1", "target_spec": {}})
    try: st.write("builder", "FIN-EXP", {"stage": "ready"}); results.append(ok(False, "builder blocked from changing stage"))
    except OwnershipError: results.append(ok(True, "builder blocked from changing stage"))
    try: st.write("orchestrator", "FIN-EXP", {"stage": "ready"}); results.append(ok(False, "illegal transition planned->ready refused"))
    except ValueError: results.append(ok(True, "illegal transition planned->ready refused"))

    # 3. full flow: a test wave = CLM-FRAU1, CS-SURV2, FIN-RPT-LEGACY + a two-app move group with one partner on hold
    two = next(g for g, v in groups.items() if len(v["apps"]) == 2 and not any(a.startswith("SHR") for a in v["apps"]))
    wave_groups = [g for g, v in groups.items() if set(v["apps"]) & {"CLM-FRAU1", "CS-SURV2", "FIN-RPT-LEGACY"}] + [two]
    partner = groups[two]["apps"][0]
    apps[partner]["profile"]["security_level"] = "Restricted"; apps[partner]["profile"]["security_review"] = "Required"
    plan = os.path.join(tmp, "wave_plan.json")
    json.dump({"plans": [{"waves": [wave_groups]}]}, open(plan, "w"))
    work = os.path.join(tmp, "run")
    llm = FakeLLM()
    log = lambda m: print("   ", m)
    rt = {"store": PlanStore(work), "fetch": lambda ids: (groups, {a: apps[a] for a in ids}), "llm": llm, "work": work,
          "dry_run": True, "log": log, "builder": Builder(llm, FakeTF(), os.path.join(work, "apps")),
          "reviewer": Reviewer(llm), "validation": Validation()}
    os.makedirs(os.path.join(work, "approvals"))

    def run():
        fl = F.make_flow_class()(); fl._rt = rt
        fl.kickoff(inputs={"wave": 1, "plan_path": plan}); return fl.state
    s = run()
    results.append(ok(s.outcome == "awaiting_approval" and not os.path.exists(os.path.join(work, "apps")), "no build before gate 1"))
    json.dump({"wave": 1, "approver": "Test Lead", "approved_at": "2026-11-17T12:00:00", "plan_sha256": F.plan_hash(plan),
               "groups": wave_groups}, open(F.approval_path(work, 1), "w"))
    s = run()
    recs = rt["store"].all()
    results.append(ok(s.outcome == "go_no_go_ready" and llm.calls <= 1 and all(r["stage"] == "held" for r in recs.values())
                      and all("awaiting owner" in r["hold_reason"] for r in recs.values()),
                      "lead approval alone builds nothing: every app held awaiting its owner"))
    other = groups[two]["apps"][1]
    import subprocess
    for a in recs:
        if a == other: continue            # one move-group partner's owner has not approved yet
        st_ = rt["store"].get(a)
        d = {"wave": 1, "app_id": a, "gate": "build", "owner": st_["owner"], "approver": st_["owner"], "delegated": False,
             "decision": "approve", "reason": "", "decided_at": "2026-11-17T12:30:00", "spec_sha256": F.spec_hash(st_["target_spec"])}
        p = F.owner_approval_path(work, 1, a, "build"); os.makedirs(os.path.dirname(p), exist_ok=True); json.dump(d, open(p, "w"))
    s = run()
    recs = rt["store"].all()
    results.append(ok(recs[partner]["stage"] == "held" and "move-group partner" in recs[partner]["hold_reason"] and "build" not in recs[partner],
                      f"{partner} not built while move-group partner {other}'s owner has not approved"))
    st_ = rt["store"].get(other)
    d = {"wave": 1, "app_id": other, "gate": "build", "owner": st_["owner"], "approver": "Test Lead", "delegated": True,
         "decision": "approve", "reason": "", "decided_at": "2026-11-17T12:31:00", "spec_sha256": F.spec_hash(st_["target_spec"])}
    json.dump(d, open(F.owner_approval_path(work, 1, other, "build"), "w"))
    s = run()
    recs = rt["store"].all()
    results.append(ok(s.outcome == "go_no_go_ready", "flow reaches go/no-go after lead + owner approvals"))
    results.append(ok(recs["CLM-FRAU1"]["review"]["cycle"] == 2 and recs["CLM-FRAU1"]["stage"] == "ready", "CLM-FRAU1 rejected (SSH from anywhere), fixed, then ready"))
    results.append(ok(len(recs["CS-SURV2"]["build"]["attempts"]) == 2 and recs["CS-SURV2"]["stage"] == "ready", "CS-SURV2 validate failure retried, then ready"))
    results.append(ok(recs[partner]["stage"] == "held" and recs[other]["stage"] == "held" and "move group" in recs[other]["hold_reason"],
                      f"move group {two}: {partner} held (security review) holds {other} too"))
    msgs = [json.loads(l) for l in open(os.path.join(work, "messages.jsonl"))]
    results.append(ok(all(Message.model_validate(m) for m in msgs) and len(msgs) > 10, f"{len(msgs)} messages valid against the contract"))
    results.append(ok(any(m["type"] == "approval" and m["payload"]["delegated"] for m in msgs), "delegated owner approval is recorded as delegated"))
    results.append(ok(all(r["history"][-1]["agent"] == "orchestrator" for r in recs.values()), "last stage change on every record made by the orchestrator"))
    print(open(os.path.join(work, "go_no_go_wave-1.md")).read())
    shutil.rmtree(tmp)
    print(f"{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
