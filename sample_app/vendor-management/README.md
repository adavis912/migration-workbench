# Vendor Management (plugin) (OPS-VEND2)

Fictional legacy code written for the capstone. Not a real system; contains deliberate issues for code-scan practice.

## Current stack

- Vendor product MarkPoint (COTS) on Tomcat 9, Oracle JDK 1.8, fronted by Apache httpd
- This repo is our export plugin WAR deployed next to MarkPoint
- MongoDB 6.0 on a VM, ~400 GB
- Data classification: Confidential (vendor tax IDs and bank details)
- Jenkins build, manual deploy

## Layout

- `src/main/java/com/hvig/vendor`: export servlet and Mongo repository
- `config/markpoint-plugin.xml`: plugin registration
- `deploy/httpd/vendor.conf`: reverse proxy

## Known legacy concerns (for the migration assessment)

- Server-side JavaScript ($where) built from a request parameter
- Export returns bank details with no role check
- MongoDB connection without TLS
- Mongo driver 3.12 is old: check compatibility with Amazon DocumentDB or MongoDB Atlas
- COTS product: confirm vendor support for the target platform
