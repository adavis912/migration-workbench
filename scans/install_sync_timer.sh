#!/bin/bash
# Install a root systemd timer that runs sync_scan_results.sh every 2 minutes. Run with sudo.
set -euo pipefail
S=/opt/capstone/capstone_kb/kb_aws/scans/sync_scan_results.sh
cat > /etc/systemd/system/capstone-scan-sync.service <<UNIT
[Unit]
Description=Copy AWS Transform scan output for the dashboard
[Service]
Type=oneshot
ExecStart=/bin/bash $S
UNIT
cat > /etc/systemd/system/capstone-scan-sync.timer <<UNIT
[Unit]
Description=Sync AWS Transform scan output every 2 minutes
[Timer]
OnBootSec=1min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now capstone-scan-sync.timer
systemctl start capstone-scan-sync.service
ls -la /opt/capstone/scans/results/latest/ && head -c 400 /opt/capstone/scans/results/latest/status.json
