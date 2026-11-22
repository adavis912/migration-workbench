# Web Content Service (MKT-WEB3)

Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice.

## Current stack

- Spring Boot 2.3 on OpenJDK 17
- Oracle 19c on Exadata (RAC), ~2.5 TB
- Uploaded images stored on the VM's local disk
- Jenkins build, manual deploy

## Layout

- `src/main/java/com/hvig/content`: Boot app, REST controller, repository
- `src/main/resources/application.yml`: settings

## Known legacy concerns (for the migration assessment)

- Spring Boot 2.3 is end of support
- Oracle-specific SQL (CONNECT BY hierarchy, optimizer hints)
- Exadata-sized database: right-size before choosing an RDS class
- Uploads on local disk (/var/content): move to object storage
- All actuator endpoints exposed without auth
