# Recruiting Tracker (HR-RECR1)

Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice.

## Current stack

- WAR on Tomcat 9, Oracle JDK 1.8
- Oracle 19c on an OCI DB System (VM), ~150 GB
- JNDI datasource in Tomcat context.xml
- Jenkins build, manual deploy

## Layout

- `src/main/java/com/hvig/recruit`: servlet and DAO
- `db/recruit_pkg.sql`: PL/SQL package the app calls
- `src/main/webapp/META-INF/context.xml`: datasource

## Known legacy concerns (for the migration assessment)

- Business logic in a PL/SQL package (Oracle-specific; affects a move to PostgreSQL)
- Candidate national ID stored in clear text
- Oracle JDK 8 licensing and end of support
- Datasource password kept in context.xml
