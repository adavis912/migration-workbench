# Migration wave plan (synthetic Harborview portfolio)

Search: tree-of-thought beam search, width 3, 78 candidate evaluations, status **complete**.

## Recommendation

## Recommendation

**Conditionally approve Plan A (score 71.85).** Only one plan was provided, so there is no runner-up to compare. I can't say it beats an alternative, only that it looks workable if the items below are confirmed.

It moves 138 active servers across six two-week waves from 2026-12-07 to 2027-02-28. Wave 1 is the smallest (18 servers) and has the best business fit (14/20). Waves 2-5 carry 23-33 servers each.

## Trade-off within the plan

- **Wave 1** is the best-supported wave. The critic found the maintenance windows compatible and the month-end blackout avoided. Slack is thin, though: CLM-FRAU1 and CS-SURV2 tolerate 1 hour of downtime, and OPS-PRIN1 tolerates 4.
- **Waves 2-5** have no critic assessment. Their 10/20 is a neutral placeholder, not evidence of fit.
- **Wave 6** is small (6 servers) but the riskiest. SHR-SSO serves every internal application and must stay up until the last consumer has moved. SHR-ETL's nightly jobs pull from the general ledger, which risks month-end processing near Feb 28. The business fit is the lowest (6/20).

## Blocker

- **MG-01 (FIN-PAY)** is not in the plan. It is Restricted/PCI=Y, and its required security review has no completion date. It cannot be scheduled until the review is done.

## Needs human action before approval

1. Confirm a security review date for MG-01, and decide which wave it will join.
2. Have owners review waves 2-5, since none has been critiqued.
3. Confirm that every internal consumer of SHR-SSO will have moved before its wave 6 cutover.
4. Confirm that SHR-ETL's cutover avoids the month-end ledger run, or move it earlier.
5. Confirm that the Wave 1 owners accept the 1-hour and 4-hour downtime tolerances.
6. Provide an alternative plan if a real comparison is wanted.

## Plan A (recommended): score 71.85/100

| Wave | Dates | Groups | Applications | Active servers | Business fit |
| --- | --- | --- | --- | --- | --- |
| 1 | 2026-12-07 to 2026-12-20 | MG-13, MG-15, MG-21, MG-24, MG-27, MG-28 | CLM-FRAU1, CS-SURV2, HR-RECR1, OPS-ASSE4, OPS-PRIN1, OPS-VEND2 | 18 | 14/20 |
| 2 | 2026-12-21 to 2027-01-03 | MG-04, MG-08, MG-17, MG-22, MG-05, MG-10 | CLM-REPA4, SLS-SALE3, HR-BENE3, POL-AGEN3, FIN-EXP, MKT-EMAI2, CLM-SUBR3, FIN-ACCO1, MKT-CAMP1, POL-POLI2 | 33 | 10/20 |
| 3 | 2027-01-04 to 2027-01-17 | MG-18, MG-23, MG-16, MG-19, MG-20, MG-33, MG-11, MG-26 | FIN-RPT-LEGACY, MKT-WEB3, FIN-BUDG3, FIN-TAX4, FIN-TREA2, SLS-PART2, POL-ADMIN, POL-RATING, OPS-FACI3 | 23 | 10/20 |
| 4 | 2027-01-18 to 2027-01-31 | MG-31, MG-09, MG-25, MG-06, MG-12, MG-14, MG-29 | SHR-MQ, HR-CORE, HR-PORTAL, OPS-AUDI5, CS-CONTACT, DOC-ARCHIVE, SLS-CRM, SLS-QUOTE, CS-CUST1, POL-UNDE1 | 33 | 10/20 |
| 5 | 2027-02-01 to 2027-02-14 | MG-02, MG-03, MG-07 | CLM-ADJ, CLM-INTAKE, CLM-CLAI2, HR-LEAR2, CS-KNOW3, SLS-LEAD1 | 25 | 10/20 |
| 6 | 2027-02-15 to 2027-02-28 | MG-30, MG-32 | SHR-ETL, SHR-SSO | 6 | 6/20 |

Computed criteria: risk_sequencing 25.0, dependency_cohesion 14.29, readiness 12.09, wave_balance 10.47; business fit (mean) 10.0/20.

## Blocked - cannot be placed until resolved

- **MG-01** (FIN-GL, FIN-PAY, FIN-RPT): FIN-PAY: Restricted/PCI=Y needs a completed security review (status: Required - no completion date)

## Needs human confirmation before approval

- Link CLM-INTAKE -> CLM-ADJ (unconfirmed (owner note only))
- Link CS-CONTACT -> DOC-ARCHIVE (partially confirmed)
- Link FIN-RPT -> FIN-GL (unconfirmed (owner note only))
- Link SLS-QUOTE -> SLS-CRM (unconfirmed (network only))
- CLM-ADJ: cmdb_discovery_conflict
- CLM-CLAI2: stale
- CS-CONTACT: suspected_injection
- CS-CUST1: stale
- CS-KNOW3: stale
- FIN-BUDG3: stale
- FIN-RPT-LEGACY: stale
- HR-LEAR2: stale
- HR-PORTAL: secret_in_config
- MKT-WEB3: stale
- OPS-AUDI5: stale
- POL-UNDE1: stale
- SLS-CRM: possibly_decommissioned_server
- SLS-LEAD1: stale
- SLS-QUOTE: suspected_injection
