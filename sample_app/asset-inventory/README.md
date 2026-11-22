# Asset Inventory (customizations) (OPS-ASSE4)

Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice.

## Current stack

- Vendor product TalentArc (COTS) holds the asset records; this repo is our sync service and config only
- Node.js 18 service on a VM, run by systemd
- Pulls assets from the vendor API every hour and drops a CSV on an SFTP server for Finance
- Jenkins build, manual deploy

## Layout

- `src/talentarc-client.js`: vendor API client
- `src/sync.js`: hourly sync job
- `config/default.json`: settings

## Known legacy concerns (for the migration assessment)

- Node.js 18 reached end of life in April 2025
- axios 0.21 has known vulnerabilities
- TLS certificate checks turned off for the vendor API
- Schedule runs inside the process (node-cron): consider a managed scheduler
- COTS product: confirm the vendor's supported AWS deployment before planning a rehost
