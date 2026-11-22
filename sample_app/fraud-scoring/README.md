# Fraud Scoring (sample legacy app)

Stand-in source code for **CLM-FRAU1 Fraud Scoring** in the fictional Harborview Insurance portfolio, built for the
capstone so AWS Transform has real code to analyze. **Not a real system.** All names, hosts and data are fictional.

- Java 8 WAR on Tomcat 9 behind Apache httpd (AJP), Spring MVC 4
- Consumes claim events from Kafka (`claims.intake`), scores them with configurable rules, caches scores in Redis
- Built with Maven: `mvn -B package`; legacy CI is the `Jenkinsfile` (to be replaced by GitLab CI)

Known debt is deliberate so the scan has something to find: end-of-life libraries, Java serialization over Kafka,
rule expressions run through the Nashorn script engine (removed in JDK 15), an unauthenticated API, MD5 hashing,
an AJP connector without a secret, plaintext Redis, and configuration read from a local file.
