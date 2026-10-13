# Record layouts - PAYSETL1

POS is 1-based (DFSORT/ICETOOL convention). LEN is per occurrence for OCCURS items.

## TXN-RECORD  (length 67, FILE SECTION, FD TXN-FILE DD TXNIN QSAM, copybook TXNREC)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | TXN-RECORD | 1 | 67 | group |  |  |
| 05 | &nbsp;&nbsp;TXN-ID | 1 | 16 | X(16) | DISPLAY |  |
| 05 | &nbsp;&nbsp;TXN-ACCT-NO | 17 | 12 | X(12) | DISPLAY |  |
| 05 | &nbsp;&nbsp;TXN-TYPE | 29 | 1 | X(01) | DISPLAY | 88 TXN-TYPE-DEBIT='D'; 88 TXN-TYPE-CREDIT='C' |
| 05 | &nbsp;&nbsp;TXN-AMOUNT | 30 | 7 | S9(11)V99 | COMP-3 |  |
| 05 | &nbsp;&nbsp;TXN-EFF-DATE | 37 | 8 | group |  |  |
| 10 | &nbsp;&nbsp;&nbsp;&nbsp;TXN-EFF-CCYY | 37 | 4 | 9(04) | DISPLAY |  |
| 10 | &nbsp;&nbsp;&nbsp;&nbsp;TXN-EFF-MM | 41 | 2 | 9(02) | DISPLAY |  |
| 10 | &nbsp;&nbsp;&nbsp;&nbsp;TXN-EFF-DD | 43 | 2 | 9(02) | DISPLAY |  |
| 05 | &nbsp;&nbsp;TXN-EFF-DATE-N | 37 | 8 | 9(08) | DISPLAY | REDEFINES TXN-EFF-DATE |
| 05 | &nbsp;&nbsp;TXN-CHANNEL | 45 | 3 | X(03) | DISPLAY |  |
| 05 | &nbsp;&nbsp;FILLER | 48 | 20 | X(20) | DISPLAY |  |

## ACCT-RECORD  (length 87, FILE SECTION, FD ACCT-MASTER DD ACCTMST VSAM KSDS, copybook ACCTREC)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | ACCT-RECORD | 1 | 87 | group |  |  |
| 05 | &nbsp;&nbsp;ACCT-KEY | 1 | 12 | X(12) | DISPLAY |  |
| 05 | &nbsp;&nbsp;ACCT-STATUS | 13 | 1 | X(01) | DISPLAY | 88 ACCT-STAT-OPEN='O'; 88 ACCT-STAT-CLOSED='C'; 88 ACCT-STAT-FROZEN='F' |
| 05 | &nbsp;&nbsp;ACCT-LEDGER-BAL | 14 | 8 | S9(13)V99 | COMP-3 |  |
| 05 | &nbsp;&nbsp;ACCT-OPEN-DATE | 22 | 8 | 9(08) | DISPLAY |  |
| 05 | &nbsp;&nbsp;ACCT-TXN-CNT | 30 | 4 | S9(09) | COMP |  |
| 05 | &nbsp;&nbsp;ACCT-LIMITS | 34 | 8 | group |  | OCCURS 3 |
| 10 | &nbsp;&nbsp;&nbsp;&nbsp;ACCT-LIMIT-TYPE | 34 | 2 | X(02) | DISPLAY |  |
| 10 | &nbsp;&nbsp;&nbsp;&nbsp;ACCT-LIMIT-AMT | 36 | 6 | S9(09)V99 | COMP-3 |  |
| 05 | &nbsp;&nbsp;FILLER | 58 | 30 | X(30) | DISPLAY |  |

## REJECT-REC  (length 80, FILE SECTION, FD REJECT-FILE DD REJOUT QSAM)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | REJECT-REC | 1 | 80 | group |  |  |
| 05 | &nbsp;&nbsp;REJ-TXN-ID | 1 | 16 | X(16) | DISPLAY |  |
| 05 | &nbsp;&nbsp;REJ-REASON-CD | 17 | 4 | X(04) | DISPLAY |  |
| 05 | &nbsp;&nbsp;REJ-REASON-TXT | 21 | 40 | X(40) | DISPLAY |  |
| 05 | &nbsp;&nbsp;FILLER | 61 | 20 | X(20) | DISPLAY |  |

## WS-BANNER  (length 40, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-BANNER | 1 | 40 | X(40) | DISPLAY | VALUE 'PAYSETL1 NIGHTLY SETTLEMENT R |

## WS-FILE-STATUSES  (length 6, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-FILE-STATUSES | 1 | 6 | group |  |  |
| 05 | &nbsp;&nbsp;WS-TXN-FS | 1 | 2 | X(02) | DISPLAY |  |
| 05 | &nbsp;&nbsp;WS-ACCT-FS | 3 | 2 | X(02) | DISPLAY |  |
| 05 | &nbsp;&nbsp;WS-REJ-FS | 5 | 2 | X(02) | DISPLAY |  |

## WS-FLAGS  (length 2, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-FLAGS | 1 | 2 | group |  |  |
| 05 | &nbsp;&nbsp;WS-EOF-SW | 1 | 1 | X(01) | DISPLAY | VALUE 'N'; 88 WS-EOF-TXN='Y' |
| 05 | &nbsp;&nbsp;WS-VALID-SW | 2 | 1 | X(01) | DISPLAY | 88 WS-TXN-VALID='Y'; 88 WS-TXN-INVALID='N' |

## WS-COUNTERS  (length 15, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-COUNTERS | 1 | 15 | group |  |  |
| 05 | &nbsp;&nbsp;WS-TXN-READ-CNT | 1 | 5 | S9(09) | COMP-3 | VALUE 0 |
| 05 | &nbsp;&nbsp;WS-TXN-POSTED-CNT | 6 | 5 | S9(09) | COMP-3 | VALUE 0 |
| 05 | &nbsp;&nbsp;WS-TXN-REJ-CNT | 11 | 5 | S9(09) | COMP-3 | VALUE 0 |

## WS-WORK-AREAS  (length 117, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-WORK-AREAS | 1 | 117 | group |  |  |
| 05 | &nbsp;&nbsp;WS-ACCT-NO | 1 | 12 | X(12) | DISPLAY |  |
| 05 | &nbsp;&nbsp;WS-HOLD-ID | 13 | 4 | S9(09) | COMP |  |
| 05 | &nbsp;&nbsp;WS-HOLD-AMT | 17 | 7 | S9(11)V99 | COMP-3 |  |
| 05 | &nbsp;&nbsp;WS-HOLD-TOTAL | 24 | 7 | S9(11)V99 | COMP-3 |  |
| 05 | &nbsp;&nbsp;WS-NET-AMT | 31 | 7 | S9(11)V99 | COMP-3 |  |
| 05 | &nbsp;&nbsp;WS-REJ-REASON | 38 | 4 | X(04) | DISPLAY |  |
| 05 | &nbsp;&nbsp;WS-RUN-DATE | 42 | 10 | X(10) | DISPLAY |  |
| 05 | &nbsp;&nbsp;WS-ABEND-MSG | 52 | 50 | X(50) | DISPLAY |  |
| 05 | &nbsp;&nbsp;WS-ABEND-CODE | 102 | 4 | S9(09) | COMP | VALUE 4001 |
| 05 | &nbsp;&nbsp;WS-ABEND-TIMING | 106 | 4 | S9(09) | COMP | VALUE 1 |
| 05 | &nbsp;&nbsp;WS-DATE-PGM | 110 | 8 | X(08) | DISPLAY | VALUE 'DATEUTIL' |

## WS-DATE-PARMS  (length 16, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-DATE-PARMS | 1 | 16 | group |  |  |
| 05 | &nbsp;&nbsp;WS-DP-FUNCTION | 1 | 4 | X(04) | DISPLAY | VALUE 'CURR' |
| 05 | &nbsp;&nbsp;WS-DP-CURR-DATE | 5 | 10 | X(10) | DISPLAY |  |
| 05 | &nbsp;&nbsp;WS-DP-RC | 15 | 2 | S9(04) | COMP |  |

## PH-HOST-VARS  (length 45, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | PH-HOST-VARS | 1 | 45 | group |  |  |
| 05 | &nbsp;&nbsp;PH-TXN-ID | 1 | 16 | X(16) | DISPLAY |  |
| 05 | &nbsp;&nbsp;PH-ACCT-NO | 17 | 12 | X(12) | DISPLAY |  |
| 05 | &nbsp;&nbsp;PH-AMOUNT | 29 | 7 | S9(11)V99 | COMP-3 |  |
| 05 | &nbsp;&nbsp;PH-POST-DATE | 36 | 10 | X(10) | DISPLAY |  |

## WS-REASON-VALUES  (length 136, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-REASON-VALUES | 1 | 136 | group |  |  |
| 05 | &nbsp;&nbsp;FILLER | 1 | 34 | X(34) | DISPLAY | VALUE 'R001AMOUNT NOT POSITIVE' |
| 05 | &nbsp;&nbsp;FILLER | 35 | 34 | X(34) | DISPLAY | VALUE 'R002INVALID TXN TYPE' |
| 05 | &nbsp;&nbsp;FILLER | 69 | 34 | X(34) | DISPLAY | VALUE 'R003ACCOUNT NOT FOUND' |
| 05 | &nbsp;&nbsp;FILLER | 103 | 34 | X(34) | DISPLAY | VALUE 'R004ACCOUNT CLOSED' |

## WS-REASON-TABLE  (length 136, WORKING-STORAGE SECTION, copybook PAYWS)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-REASON-TABLE | 1 | 136 | group |  | REDEFINES WS-REASON-VALUES |
| 05 | &nbsp;&nbsp;WS-REASON-ENTRY | 1 | 34 | group |  | OCCURS 4 KEY WS-REASON-CODE INDEXED BY WS-RSN-IDX |
| 10 | &nbsp;&nbsp;&nbsp;&nbsp;WS-REASON-CODE | 1 | 4 | X(04) | DISPLAY |  |
| 10 | &nbsp;&nbsp;&nbsp;&nbsp;WS-REASON-TEXT | 5 | 30 | X(30) | DISPLAY |  |
