#!/bin/bash
# Let the dashboard queue scans and install the root runner that starts them. Run with sudo.
set -euo pipefail
D=/opt/capstone/capstone_kb/kb_aws
# 1) The dashboard (otherwise read-only) gets one writable folder: /var/lib/capstone-dashboard
mkdir -p /etc/systemd/system/capstone-dashboard.service.d
cat > /etc/systemd/system/capstone-dashboard.service.d/scan-queue.conf <<UNIT
[Service]
StateDirectory=capstone-dashboard
StateDirectoryMode=0755
UNIT
mkdir -p /var/lib/capstone-dashboard/scan-status && chmod 755 /var/lib/capstone-dashboard/scan-status
# 2) Root runner every minute
cat > /etc/systemd/system/capstone-scan-runner.service <<UNIT
[Unit]
Description=Start AWS Transform scans requested from the dashboard
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $D/scans/scan_runner.py
UNIT
cat > /etc/systemd/system/capstone-scan-runner.timer <<UNIT
[Unit]
Description=Check for requested AWS Transform scans every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=5s
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl restart capstone-dashboard
mkdir -p /var/lib/capstone-dashboard/scan-queue && chown dashboard:dashboard /var/lib/capstone-dashboard/scan-queue && chmod 700 /var/lib/capstone-dashboard/scan-queue
systemctl enable --now capstone-scan-runner.timer
systemctl is-active capstone-dashboard capstone-scan-runner.timer
