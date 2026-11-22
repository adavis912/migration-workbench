# Finance Reporting (Legacy, retiring) (FIN-RPT-LEGACY)

Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice.

## Current stack

- WAR on Tomcat 7, Oracle JDK 1.8, one VM
- No database: reads nightly CSV extracts from an NFS share
- Nightly cron job builds Excel reports
- Jenkins build, manual deploy
- Scheduled for retirement once the new reporting app is live

## Layout

- `src/main/java/com/hvig/finrpt`: report servlet and extract reader
- `cron/nightly-report.sh`: cron job on the VM
- `src/main/webapp/WEB-INF/web.xml`: servlet mapping

## Known legacy concerns (for the migration assessment)

- Tomcat 7 and Oracle JDK 8 are end of life
- Apache POI 3.9 has known vulnerabilities
- Report download takes a file name from the request (path traversal)
- Depends on an NFS mount (/mnt/finshare)
- Retirement candidate: confirm with the business before migrating
