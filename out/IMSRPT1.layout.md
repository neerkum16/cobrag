# Record layouts - IMSRPT1

POS is 1-based (DFSORT/ICETOOL convention). LEN is per occurrence for OCCURS items.

## DLI-FUNCTIONS  (length 12, WORKING-STORAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | DLI-FUNCTIONS | 1 | 12 | group |  |  |
| 05 | &nbsp;&nbsp;FUNC-GU | 1 | 4 | X(04) | DISPLAY | VALUE 'GU  ' |
| 05 | &nbsp;&nbsp;FUNC-GN | 5 | 4 | X(04) | DISPLAY | VALUE 'GN  ' |
| 05 | &nbsp;&nbsp;FUNC-REPL | 9 | 4 | X(04) | DISPLAY | VALUE 'REPL' |

## CUST-SEG-IO  (length 52, WORKING-STORAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | CUST-SEG-IO | 1 | 52 | group |  |  |
| 05 | &nbsp;&nbsp;CUST-ID | 1 | 10 | X(10) | DISPLAY |  |
| 05 | &nbsp;&nbsp;CUST-NAME | 11 | 40 | X(40) | DISPLAY |  |
| 05 | &nbsp;&nbsp;CUST-RISK-SCORE | 51 | 2 | S9(03) | COMP-3 |  |

## CUST-SSA  (length 30, WORKING-STORAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | CUST-SSA | 1 | 30 | group |  |  |
| 05 | &nbsp;&nbsp;FILLER | 1 | 19 | X(19) | DISPLAY | VALUE 'CUSTSEG (CUSTID  EQ' |
| 05 | &nbsp;&nbsp;SSA-CUST-ID | 20 | 10 | X(10) | DISPLAY |  |
| 05 | &nbsp;&nbsp;FILLER | 30 | 1 | X(01) | DISPLAY | VALUE ')' |

## PCB-MASK  (length 32, LINKAGE SECTION)

| Lvl | Field | Pos | Len | PIC | Usage | Notes |
|---|---|---:|---:|---|---|---|
| 01 | PCB-MASK | 1 | 32 | group |  |  |
| 05 | &nbsp;&nbsp;PCB-DBD-NAME | 1 | 8 | X(08) | DISPLAY |  |
| 05 | &nbsp;&nbsp;PCB-SEG-LEVEL | 9 | 2 | X(02) | DISPLAY |  |
| 05 | &nbsp;&nbsp;PCB-STATUS | 11 | 2 | X(02) | DISPLAY | 88 PCB-OK=SPACES; 88 PCB-NOT-FOUND='GE'; 88 PCB-END-DB='GB' |
| 05 | &nbsp;&nbsp;FILLER | 13 | 20 | X(20) | DISPLAY |  |
