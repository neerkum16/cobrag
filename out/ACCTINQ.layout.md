# Record layouts - ACCTINQ

POS is 1-based (DFSORT/ICETOOL convention). LEN is per occurrence for OCCURS items.

## WS-COMMAREA  (length 14, WORKING-STORAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-COMMAREA | 1 | 14 | group |  |  |
| 05 | &nbsp;&nbsp;CA-ACCT-NO | 1 | 12 | X(12) | DISPLAY |  |
| 05 | &nbsp;&nbsp;CA-RETURN-CD | 13 | 2 | X(02) | DISPLAY |  |

## WS-RESP  (length 4, WORKING-STORAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-RESP | 1 | 4 | S9(08) | COMP |  |

## WS-MAP-OUT  (length 39, WORKING-STORAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | WS-MAP-OUT | 1 | 39 | group |  |  |
| 05 | &nbsp;&nbsp;MO-ACCT-NO | 1 | 12 | X(12) | DISPLAY |  |
| 05 | &nbsp;&nbsp;MO-BALANCE | 13 | 17 | -Z(12)9.99 | DISPLAY |  |
| 05 | &nbsp;&nbsp;MO-STATUS | 30 | 10 | X(10) | DISPLAY |  |

## ACCT-RECORD  (length 87, WORKING-STORAGE SECTION, copybook ACCTREC)

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

## DFHCOMMAREA  (length 14, LINKAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | DFHCOMMAREA | 1 | 14 | X(14) | DISPLAY |  |
