# cobrag analysis report

## Inventory

| Program | Type | Paragraphs | Source lines (expanded) | Copybooks | Calls | Dead-code candidates |
|---|---|---:|---:|---|---|---|
| ACCTINQ | CICS, VSAM | 4 | 74 | ACCTREC | AUDITLOG | - |
| IMSRPT1 | BATCH, IMS DL/I | 2 | 49 | - | CBLTDLI | - |
| PAYSETL1 | BATCH, DB2, VSAM | 14 | 275 | TXNREC, ACCTREC, PAYWS | CEE3ABD, DATEUTIL | 3000-LEGACY-RECON |

## Copybook cross-reference (change impact)

| Copybook | Used by |
|---|---|
| ACCTREC | ACCTINQ, PAYSETL1 |
| PAYWS | PAYSETL1 |
| TXNREC | PAYSETL1 |

## Findings

### ACCTINQ

- Migration encoding: 2 packed-decimal and 2 binary field(s) need type-aware conversion (not EBCDIC->ASCII byte translation).

### IMSRPT1

- Migration encoding: 1 packed-decimal and 0 binary field(s) need type-aware conversion (not EBCDIC->ASCII byte translation).

### PAYSETL1

- Dynamic CALL via WS-DATE-PGM in 1000-INITIALIZE resolves to DATEUTIL from VALUE clause and the field is never modified in this program.
- Dead-code candidate 3000-LEGACY-RECON: no PERFORM/GO TO/fall-through path from entry.
- 1 GO TO statement(s) - convert to structured flow before automated translation.
- Cursor CSR_PENDING is WITH HOLD - stays open across COMMIT; preserve semantics in target design.
- Migration encoding: 10 packed-decimal and 5 binary field(s) need type-aware conversion (not EBCDIC->ASCII byte translation).
- REDEFINES (TXN-EFF-DATE-N, WS-REASON-TABLE): target schema needs a discriminator or a single canonical view.
- SEARCH ALL tables (WS-REASON-ENTRY): ASCENDING KEY order is a load-time contract.

## Chunk statistics

32 chunks (paragraph=20, program_summary=3, record_layout=9); token estimate min 57 / max 808 / total 7937.
