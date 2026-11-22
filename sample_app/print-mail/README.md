# Print and Mail (adapter) (OPS-PRIN1)

Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice.

## Current stack

- Vendor product PrintFlow (COTS) does composition and printing; this repo is our Spring Boot adapter
- Spring Boot 2.7 on OpenJDK 17, fronted by Apache httpd
- Redis list as the job queue
- Oracle Autonomous (ATP) for job history
- Jenkins build, manual deploy

## Layout

- `src/main/java/com/hvig/print`: REST endpoint, queue worker, PrintFlow hot-folder writer
- `src/main/resources/application.properties`: settings
- `deploy/httpd/print.conf`: reverse proxy

## Known legacy concerns (for the migration assessment)

- Spring Boot 2.7 open-source support has ended
- Hands jobs to PrintFlow through a local hot folder (/printflow/hotfolder)
- Redis without TLS or auth
- Job payloads include customer names and mailing addresses
- COTS product: confirm vendor support for the target platform
