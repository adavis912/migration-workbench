"""Remediate the three security gaps the pilot's scans found in CLM-FRAU1's main.tf:
1. Valkey had no client authentication -> RBAC user group with IAM authentication.
2. ALB->instance traffic was plain HTTP -> HTTPS target group on 8443.
3. No HTTP->HTTPS redirect / no TLS listener -> HTTPS listener (TLS 1.2+) with ACM cert; port 80 redirects.
Also: ALB access logs to the platform log bucket (Semgrep finding), and a documented Checkov skip for
CKV2_AWS_5 (tier security groups are attached through a for_each lookup Checkov cannot follow).
Usage: python3 fix_clm.py <app_dir>
"""
import sys, pathlib
d = pathlib.Path(sys.argv[1]); p = d / "main.tf"; t = p.read_text()

def sub(old, new):
    global t
    assert t.count(old) == 1, f"expected exactly one match for: {old[:60]!r}"
    t = t.replace(old, new)

# --- 2. web tier serves TLS on 8443
sub('alb_to_web   = { from = "alb", to = "web", port = 80 }', 'alb_to_web   = { from = "alb", to = "web", port = 8443 }')

# --- 3. on-prem clients reach the ALB on 443 (and 80, which only redirects)
sub('''  onprem_ingress = {
    for p in setproduct(["alb", "mq"], var.onprem_cidrs) : "${p[0]}-${p[1]}" => {
      tier = p[0]
      cidr = p[1]
      port = p[0] == "alb" ? 80 : 9092
    }
  }''', '''  onprem_ports = {
    alb_https    = { tier = "alb", port = 443 }
    alb_redirect = { tier = "alb", port = 80 }
    mq           = { tier = "mq", port = 9092 }
  }

  onprem_ingress = {
    for p in setproduct(keys(local.onprem_ports), var.onprem_cidrs) : "${p[0]}-${p[1]}" => {
      tier = local.onprem_ports[p[0]].tier
      cidr = p[1]
      port = local.onprem_ports[p[0]].port
    }
  }''')

sub('''resource "aws_security_group" "tier" {
  for_each = local.tiers
''', '''resource "aws_security_group" "tier" {
  # checkov:skip=CKV2_AWS_5:Attached through for_each lookups (aws_instance.server, aws_lb.web, the cache) that Checkov cannot resolve; the plan shows every tier group in use.
  for_each = local.tiers
''')

# --- 1. IAM permission for Valkey IAM authentication
sub('''  statement {
    sid       = "DecryptWithCmk"''', '''  statement {
    sid       = "ConnectToValkeyWithIam"
    actions   = ["elasticache:Connect"]
    resources = [aws_elasticache_replication_group.cache.arn, aws_elasticache_user.app.arn]
  }

  statement {
    sid       = "DecryptWithCmk"''')

# --- ALB: access logs, HTTPS target group, TLS listener, redirect
sub('''  # checkov:skip=CKV_AWS_91:Access-log bucket is not part of the provided inputs; add when the platform log bucket is available.
  # checkov:skip=CKV2_AWS_28:Internal ALB reachable only from private networks.
''', '''  # checkov:skip=CKV2_AWS_28:Internal ALB reachable only from private networks.
''')
sub('''  drop_invalid_header_fields = true
  enable_deletion_protection = true
}''', '''  drop_invalid_header_fields = true
  enable_deletion_protection = true

  access_logs {
    bucket  = var.alb_log_bucket_name
    prefix  = "${var.app_id}/${var.environment}"
    enabled = true
  }
}''')
sub('''  port                 = 80
  protocol             = "HTTP"
  target_type          = "instance"''', '''  port                 = 8443
  protocol             = "HTTPS"
  target_type          = "instance"''')
sub('''    path                = "/"
    matcher             = "200-399"''', '''    protocol            = "HTTPS"
    path                = "/"
    matcher             = "200-399"''')
sub('''  target_id        = aws_instance.server[each.key].id
  port             = 80''', '''  target_id        = aws_instance.server[each.key].id
  port             = 8443''')
sub('''resource "aws_lb_listener" "http" {
  # checkov:skip=CKV_AWS_2:No ACM certificate input is available; internal-only listener. Add HTTPS once a certificate is provided.
  # checkov:skip=CKV_AWS_103:See CKV_AWS_2.

  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}''', '''resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.web.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

# Port 80 only redirects to HTTPS.
resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.web.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}''')

# --- 1. Valkey RBAC with IAM authentication (no shared auth token to store or rotate)
sub('''  kms_key_id                 = var.kms_key_arn
  snapshot_retention_limit   = 7
}''', '''  kms_key_id                 = var.kms_key_arn
  snapshot_retention_limit   = 7
  user_group_ids             = [aws_elasticache_user_group.cache.user_group_id]
}

# Client authentication: RBAC user group. The app user signs in with IAM (elasticache:Connect on the
# instance role), so there is no shared auth token to store or rotate. The built-in "default" user is
# switched off.
resource "aws_elasticache_user" "default_off" {
  user_id       = "${local.name_prefix}-default"
  user_name     = "default"
  engine        = "valkey"
  access_string = "off -@all"

  authentication_mode {
    type = "no-password-required"
  }
}

resource "aws_elasticache_user" "app" {
  user_id       = "${local.name_prefix}-app"
  user_name     = "${local.name_prefix}-app" # IAM authentication requires user_name == user_id
  engine        = "valkey"
  access_string = "on ~* +@all -@dangerous"

  authentication_mode {
    type = "iam"
  }
}

resource "aws_elasticache_user_group" "cache" {
  user_group_id = "${local.name_prefix}-cache"
  engine        = "valkey"
  user_ids      = [aws_elasticache_user.default_off.user_id, aws_elasticache_user.app.user_id]
}''')
sub('''resource "aws_elasticache_replication_group" "cache" {
''', '''resource "aws_elasticache_replication_group" "cache" {
  # checkov:skip=CKV_AWS_31:Clients authenticate with IAM through the RBAC user group (user_group_ids) instead of a shared auth token; transit encryption is on.
''')
p.write_text(t)

n = d / "MIGRATION_NOTES.md"
n.write_text(n.read_text().rstrip() + '''

## Security remediation after the GitLab pilot scans

- **Cache authentication.** Valkey now requires sign-in through an RBAC user group. The application
  user `<name_prefix>-app` authenticates with IAM (the instance role has `elasticache:Connect`); the
  built-in `default` user is switched off. **App change needed:** the cache client must use IAM auth
  (generate the IAM auth token with the AWS SDK and connect over TLS as that user).
- **Encrypted traffic end to end.** The load balancer terminates TLS on 443 with `var.acm_certificate_arn`
  (TLS 1.2+ policy); port 80 only redirects to HTTPS. It forwards to the web servers over HTTPS on
  8443. **App change needed:** the web server must listen for TLS on 8443 (a self-signed certificate is
  acceptable; the load balancer does not validate target certificates).
- **Access logs.** Load balancer access logs go to `var.alb_log_bucket_name` under `<app_id>/<environment>/`.
- **New inputs:** `acm_certificate_arn`, `alb_log_bucket_name`.
''')
print("patched", p, "and", n)
