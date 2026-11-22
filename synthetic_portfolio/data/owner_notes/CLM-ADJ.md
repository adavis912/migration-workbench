# CLM-ADJ - Claims Adjudication

Last updated: 2026-09-29

Interview with Owen Ramos.

Runs on OKE and reads claim events from Claims Intake through Kafka. The database is Autonomous (ATP). The CMDB lists 8 servers for us, but two of the Kafka brokers were removed in the spring when we resized the cluster.
