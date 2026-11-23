#!/bin/bash
# Turn on AWS Transform code scans for the GitHub edition of the workbench. Run as root on that host.
# Same runner and sync as the GitLab edition; the scanned code comes from github.com/adavis912/migration-pilots.
# Log: /var/log/capstone-gh-scans.log
exec > >(tee -a /var/log/capstone-gh-scans.log) 2>&1
set -euo pipefail
KB=/opt/capstone/capstone_kb/kb_aws
W=/opt/capstone/scans
REG=$W/apps.github.json
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1

# 1) Node 22 and the AWS Transform CLI (atx) for ssm-user, the account scans run as
dnf install -y -q nodejs22 >/dev/null 2>&1 || dnf install -y -q nodejs >/dev/null
command -v node >/dev/null || ln -sf "$(ls /usr/bin/node-2* | sort -V | tail -1)" /usr/local/bin/node
node --version
id ssm-user >/dev/null 2>&1 || useradd -m -s /bin/bash ssm-user
if [ ! -x /home/ssm-user/.local/bin/atx ]; then
  curl -fsSL https://transform-cli.awsstatic.com/install.sh -o /tmp/atx-install.sh
  runuser -l ssm-user -c 'bash /tmp/atx-install.sh' | tail -3
fi
runuser -l ssm-user -c 'export PATH=$HOME/.local/bin:$PATH; atx --version | head -1'
runuser -l ssm-user -c 'git config --global user.name "Migration Workbench (GitHub edition)"; git config --global user.email workbench@example.invalid'

# 2) One standalone repo per app, copied from the pilots repo (atx commits its results to a branch there)
mkdir -p $W && chown ssm-user:ssm-user $W
rm -rf /tmp/pilots && git clone -q https://github.com/adavis912/migration-pilots.git /tmp/pilots
SHA=$(git -C /tmp/pilots rev-parse --short HEAD)
python3 - "$REG" "$SHA" <<'PY'
import json, sys
reg, sha = sys.argv[1], sys.argv[2]
apps = {"cs-surv2": "CS-SURV2", "fin-rpt-legacy": "FIN-RPT-LEGACY"}
out = {"_comment": "GitHub edition: scanned repo path -> portfolio app. Source: github.com/adavis912/migration-pilots."}
for slug, app in apps.items():
    out[f"/opt/capstone/scans/gh-{slug}"] = {"app": app, "repo": f"adavis912/migration-pilots/apps/{slug}",
        "repo_url": f"https://github.com/adavis912/migration-pilots/tree/main/apps/{slug}", "base_commit": sha}
json.dump(out, open(reg, "w"), indent=1)
PY
for slug in cs-surv2 fin-rpt-legacy; do
  D=$W/gh-$slug
  if [ ! -d $D/.git ]; then
    mkdir -p $D && cp -r /tmp/pilots/apps/$slug/. $D/ && chown -R ssm-user:ssm-user $D
    runuser -u ssm-user -- git -C $D init -q -b main
    runuser -u ssm-user -- git -C $D add -A
    runuser -u ssm-user -- git -C $D commit -qm "apps/$slug from adavis912/migration-pilots@$SHA"
  fi
done
chmod 644 $REG

# 3) Root runner (every minute) and result sync (every 2 minutes), pointed at the GitHub registry
mkdir -p /var/lib/capstone-dashboard/scan-queue /var/lib/capstone-dashboard/scan-status
chown dashboard:dashboard /var/lib/capstone-dashboard/scan-queue && chmod 700 /var/lib/capstone-dashboard/scan-queue
chmod 755 /var/lib/capstone-dashboard/scan-status
for u in runner sync; do
  if [ $u = runner ]; then CMD="/usr/bin/python3 $KB/scans/scan_runner.py"; EVERY=1min; DESC="Start AWS Transform scans requested from the dashboard"
  else CMD="/usr/bin/python3 $KB/scans/sync_scan_results.py"; EVERY=2min; DESC="Copy AWS Transform scan output for the dashboard"; fi
  cat > /etc/systemd/system/capstone-scan-$u.service <<UNIT
[Unit]
Description=$DESC (GitHub edition)
[Service]
Type=oneshot
Environment=SCAN_REGISTRY=$REG
ExecStart=$CMD
UNIT
  cat > /etc/systemd/system/capstone-scan-$u.timer <<UNIT
[Unit]
Description=$DESC every $EVERY
[Timer]
OnBootSec=1min
OnUnitActiveSec=$EVERY
AccuracySec=5s
[Install]
WantedBy=timers.target
UNIT
done

# 4) Let the dashboard queue scans and read this registry
mkdir -p /etc/systemd/system/capstone-gh-dashboard.service.d
cat > /etc/systemd/system/capstone-gh-dashboard.service.d/scans.conf <<UNIT
[Service]
Environment=WB_SCANS=1 SCAN_REGISTRY=$REG
UNIT
systemctl daemon-reload
systemctl enable --now capstone-scan-runner.timer capstone-scan-sync.timer
systemctl restart capstone-gh-dashboard
sleep 3
systemctl is-active capstone-gh-dashboard capstone-scan-runner.timer capstone-scan-sync.timer
cat $REG
echo SCANS-READY
