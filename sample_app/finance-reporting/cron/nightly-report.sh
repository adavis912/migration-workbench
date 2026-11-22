#!/bin/sh
# crontab on finrpt-app01:  15 2 * * * /opt/finrpt/nightly-report.sh
curl -s -X POST http://localhost:8080/finrpt/build?period=$(date +%Y-%m) >> /var/log/finrpt/nightly.log 2>&1
