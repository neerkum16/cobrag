# cobrag - COBOL semantic chunker and static analyzer for RAG

Python 3.10+, standard library only. No parser generator, no network.

## Run

```
python3 cobrag.py analyze samples/src -I samples/copy -o out
python3 cobrag.py search "who updates ACCOUNT_BAL" --chunks out/chunks.jsonl --kind paragraph
python3 enrich.py --chunks out/chunks.jsonl --dry-run
ANTHROPIC_API_KEY=... python3 enrich.py --chunks out/chunks.jsonl --limit 5
python3 -m unittest tests.test_cobrag -v
```

`-I` is repeatable (copybook search order = SYSLIB concatenation order). Member lookup tries
no extension, .cpy, .cbl, .cob, .copy, .txt, then a case-insensitive stem match.

## Pipeline

1. Reader: reference format, cols 1-6 ignored, col 7 indicator (`*` `/` kept as comments,
   `D` dropped, `-` continuation incl. continued alphanumeric literals), cols 8-72 code,
   Area A detection.
2. COPY / COPY REPLACING (`==pseudo-text==`, word BY word, `:TAG:` substring) and
   `EXEC SQL INCLUDE` (DCLGEN members) expanded recursively, SQLCA/SQLDA left in place.
3. ENVIRONMENT: SELECT/ASSIGN -> DD name, ORGANIZATION/ACCESS/KEY/STATUS, VSAM type
   (INDEXED=KSDS, RELATIVE=RRDS, AS- prefix=ESDS, else QSAM).
4. DATA: level tree, PIC sizing per USAGE (DISPLAY, COMP/COMP-4/BINARY/COMP-5, COMP-3,
   COMP-1/2, INDEX, POINTER, NATIONAL, edited), group USAGE inheritance, OCCURS (incl. ODO,
   KEY, INDEXED BY), REDEFINES offset sharing, 88/66 levels, FD-to-record mapping.
5. PROCEDURE: sections/paragraphs, literal-masked statement analysis: PERFORM [THRU]
   [UNTIL/VARYING/TIMES], GO TO, CALL (static, dynamic resolved via VALUE with a
   never-modified check), EXEC SQL (op, tables, CRUD, host vars, cursor->table), EXEC CICS
   (command, FILE/QUEUE/PROGRAM), EXEC DLI and CALL CBLTDLI (function code resolved from
   VALUE, segment from SSA), file I/O verbs, data written (MOVE/COMPUTE/ADD../SET 88/
   INITIALIZE/READ INTO/FETCH INTO/STRING/UNSTRING/INSPECT).
6. Control flow: reachability over states (paragraph, active PERFORM range end) so that
   THRU ranges, GO TO inside ranges and fall-through are modelled. Unconditional
   terminators at nesting depth 0 (GO TO, GOBACK, STOP RUN, EXIT PROGRAM, CICS RETURN/XCTL)
   stop fall-through.

## Chunk record (chunks.jsonl)

| key | content |
|---|---|
| id | `PGM::PARA::NAME::part`, `PGM::PROGRAM`, `PGM::DATA::SECTION::n`, `COPYBOOK::MEMBER::n` |
| kind | paragraph, section, record_layout, program_summary |
| embed_text | context header + field glossary + code - the string to embed |
| code | paragraph source with column-7 indicator (comments retained) |
| code_numbered | same, prefixed with 6-digit source line numbers (for LLM citation) |
| metadata | callers, performs, go_to, calls, sql, cics, dli, file_ops, crud, data_referenced, data_modified, reachable, terminal |
| token_estimate | ceil(chars/4) |

Copybook layouts are emitted once per copybook and list every program that uses them.
Paragraphs over `--max-tokens` are split at statement boundaries with `--overlap` lines
repeated; every part carries the full header.

## Limits

Fixed format only. One program per member. COPY must start a statement. ALTER flagged,
not modelled. Qualified names (OF/IN) not disambiguated - ambiguous names are flagged in the
glossary. Nested IF/phrase scope for fall-through detection is heuristic. EXEC SQL dynamic
statements are flagged, not resolved. `enrich.py` live and batch-merge modes were not run
against the API in the build environment (no network); the merge/grounding logic was
tested with a mocked result.
