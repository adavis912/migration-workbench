# FIN-RPT - Finance Reporting

Last updated: 2026-11-09

Interview with Lena Sato, application owner.

Finance Reporting runs on Tomcat. The important part is the nightly job on the batch server: at about 02:00 it reads the general ledger database directly over JDBC and builds the next day's reports. We tried running it from another data center once and it timed out, so it needs to stay close to the GL database.

Downtime is fine on weekends. Reports are needed by 07:00 on business days.
