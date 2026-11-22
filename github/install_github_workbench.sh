#!/bin/bash
# Install the GitHub edition of the Migration Workbench on its own EC2 host. Run as root.
# It never touches the GitLab workbench: different host, service name, secrets (capstone-gh/*) and login.
#   bash install_github_workbench.sh
# Log: /var/log/capstone-gh-install.log
exec > >(tee -a /var/log/capstone-gh-install.log) 2>&1
set -euo pipefail
REPO_URL=https://github.com/adavis912/migration-workbench.git
BRANCH=github-edition
ROOT=/opt/capstone/capstone_kb
APP=$ROOT/kb_aws/dashboard
CERT=/etc/capstone-gh-dashboard
LOGIN_SECRET=capstone-gh/dashboard-login
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1

dnf install -y -q git python3.11 python3.11-pip openssl >/dev/null
mkdir -p $ROOT
if [ -d $ROOT/kb_aws/.git ]; then git -C $ROOT/kb_aws fetch -q origin $BRANCH && git -C $ROOT/kb_aws reset -q --hard origin/$BRANCH
else git clone -q -b $BRANCH $REPO_URL $ROOT/kb_aws; fi
# Same deterministic synthetic portfolio the GitLab workbench uses (fixed seed).
rm -rf $ROOT/synthetic_portfolio && cp -r $ROOT/kb_aws/synthetic_portfolio $ROOT/synthetic_portfolio
python3.11 $ROOT/synthetic_portfolio/generate.py
[ -x /opt/capstone/venv/bin/python ] || python3.11 -m venv /opt/capstone/venv
/opt/capstone/venv/bin/pip install -q --upgrade pip
/opt/capstone/venv/bin/pip install -q boto3 pyyaml python-hcl2 pydantic

id dashboard >/dev/null 2>&1 || useradd --system --no-create-home --shell /sbin/nologin dashboard
mkdir -p $CERT
[ -f $CERT/cert.pem ] || openssl req -x509 -newkey rsa:2048 -nodes -days 825 -subj "/CN=capstone-gh-migration-workbench" \
  -keyout $CERT/key.pem -out $CERT/cert.pem 2>/dev/null
chown -R root:dashboard $CERT; chmod 750 $CERT; chmod 640 $CERT/*.pem

if ! aws secretsmanager describe-secret --secret-id $LOGIN_SECRET >/dev/null 2>&1; then
  S=$(mktemp); chmod 600 "$S"
  python3.11 -c 'import json,secrets; print(json.dumps({"username":"antonio","password":secrets.token_urlsafe(18)}))' > "$S"
  aws secretsmanager create-secret --name $LOGIN_SECRET --description "Login for the GitHub edition of the migration workbench" \
    --secret-string "file://$S" >/dev/null
  shred -u "$S"
  echo "Created login secret $LOGIN_SECRET (username: antonio). Copy the password from Secrets Manager."
fi

cat > /etc/systemd/system/capstone-gh-dashboard.service <<UNIT
[Unit]
Description=Migration Workbench, GitHub edition
After=network-online.target
Wants=network-online.target

[Service]
User=dashboard
Group=dashboard
Environment=CAPSTONE=$ROOT AWS_REGION=us-east-1 PYTHONUNBUFFERED=1 WB_PROVIDER=github WB_SCANS=0
Environment=DASHBOARD_LOGIN_SECRET=$LOGIN_SECRET DASHBOARD_CERT_DIR=$CERT
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
systemctl enable --now capstone-gh-dashboard
systemctl restart capstone-gh-dashboard
sleep 4
systemctl is-active capstone-gh-dashboard
curl -sk -o /dev/null -w "local healthz %{http_code}\n" https://127.0.0.1/healthz
date -u +%FT%TZ > /opt/capstone/GH_INSTALL_DONE
