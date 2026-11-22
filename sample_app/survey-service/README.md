# Survey Service (CS-SURV2)

Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice.

## Current stack

- Embedded Jetty 9.4 on OpenJDK 17, fronted by Apache httpd
- SQL Server 2019 on a VM (survey responses, ~6 TB)
- Jenkins build, manual deploy

## Layout

- `src/main/java/com/hvig/survey`: Jetty server, servlet and DAO
- `config/survey.properties.example`: runtime settings
- `deploy/httpd/survey.conf`: reverse proxy

## Known legacy concerns (for the migration assessment)

- Jetty 9.4 is end of community support
- SQL built by string concatenation in SurveyDao
- JDBC connection has encrypt=false
- Large SQL Server database: plan the data move separately (DMS or native backup)
- Response exports written to local disk under /data/exports
