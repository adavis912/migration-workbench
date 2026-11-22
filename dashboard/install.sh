#!/bin/bash
# Install or update the Wave 1 pipeline dashboard on the migration workbench. Run with sudo.
# Creates the login secret on first install (value never printed), a self-signed certificate,
# a locked-down system user and a systemd service on port 443.
set -euo pipefail
APP=/opt/capstone/capstone_kb/kb_aws/dashboard
CERT=/etc/capstone-dashboard
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1

id dashboard >/dev/null 2>&1 || useradd --system --no-create-home --shell /sbin/nologin dashboard

mkdir -p "$CERT"
if [ ! -f "$CERT/cert.pem" ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=capstone-migration-workbench" \
    -keyout "$CERT/key.pem" -out "$CERT/cert.pem" 2>/dev/null
fi
chown -R root:dashboard "$CERT"; chmod 750 "$CERT"; chmod 640 "$CERT"/*.pem

if ! aws secretsmanager describe-secret --secret-id capstone/dashboard-login >/dev/null 2>&1; then
  S=$(mktemp); chmod 600 "$S"
  python3 -c 'import json,secrets; print(json.dumps({"username":"antonio","password":secrets.token_urlsafe(18)}))' > "$S"
  aws secretsmanager create-secret --name capstone/dashboard-login \
    --description "Login for the Wave 1 pipeline dashboard on the migration workbench" \
    --secret-string "file://$S" >/dev/null
  shred -u "$S"
  echo "Created login secret capstone/dashboard-login (username: antonio). Copy the password from Secrets Manager."
fi

cat > /etc/systemd/system/capstone-dashboard.service <<UNIT
[Unit]
Description=Wave 1 pipeline dashboard
After=network-online.target
Wants=network-online.target

[Service]
User=dashboard
Group=dashboard
Environment=CAPSTONE=/opt/capstone/capstone_kb AWS_REGION=us-east-1 PYTHONUNBUFFERED=1
WorkingDirectory=$APP
ExecStart=/opt/capstone/venv/bin/python $APP/server.py
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadOnlyPaths=/opt/capstone
StateDirectory=capstone-dashboard
StateDirectoryMode=0755
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now capstone-dashboard
systemctl restart capstone-dashboard
sleep 3
systemctl is-active capstone-dashboard
curl -sk -o /dev/null -w "local healthz %{http_code}\n" https://127.0.0.1/healthz
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60")
echo "Open: https://$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/public-ipv4)/"
