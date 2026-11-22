#!/bin/bash
# Wrapper kept for the systemd unit: sync every AWS Transform scan job, tagged with its app (see sync_scan_results.py).
exec /usr/bin/python3 "$(dirname "$(readlink -f "$0")")/sync_scan_results.py"
