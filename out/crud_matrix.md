# CRUD matrices

C=create (INSERT/WRITE/ISRT) R=read (SELECT/FETCH/READ/GU/GN) U=update (UPDATE/REWRITE/REPL) D=delete (DELETE/DLET)

## ACCTINQ

| Paragraph | CICS-FILE:ACCTMST |
|---|---|
| 1000-READ-ACCOUNT | R |

## IMSRPT1

| Paragraph | DLI:PCB-MASK |
|---|---|
| 0000-MAIN | R |
| 1000-RESCORE | U |

## PAYSETL1

| Paragraph | DB2:ACCOUNT_BAL | DB2:PAYMENT_HIST | DB2:PAYMENT_HOLD | ACCT-MASTER (DD ACCTMST, VSAM KSDS) | REJECT-FILE (DD REJOUT, QSAM) | TXN-FILE (DD TXNIN, QSAM) |
|---|---|---|---|---|---|---|
| 2100-VALIDATE-TXN |  |  |  | R |  |  |
| 2200-APPLY-HOLDS |  |  | R |  |  |  |
| 2300-POST-PAYMENT | U | C |  | U |  |  |
| 2800-WRITE-REJECT |  |  |  |  | C |  |
| 2900-READ-TXN |  |  |  |  |  | R |
| 3000-LEGACY-RECON (dead) |  |  | D |  |  |  |

## Program level (files keyed by DD name)

| Resource | ACCTINQ | IMSRPT1 | PAYSETL1 |
|---|---|---|---|
| CICS-FILE:ACCTMST | R |  |  |
| DB2:ACCOUNT_BAL |  |  | U |
| DB2:PAYMENT_HIST |  |  | C |
| DB2:PAYMENT_HOLD |  |  | RD |
| DLI:PCB-MASK |  | RU |  |
| FILE:ACCTMST |  |  | RU |
| FILE:REJOUT |  |  | C |
| FILE:TXNIN |  |  | R |
