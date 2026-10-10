#!/usr/bin/env python3
"""
cobrag.py - COBOL semantic chunker and static analyzer for RAG / LLM pipelines.

Why: fixed-size token chunking of COBOL destroys meaning. A paragraph that says
"MOVE TXN-AMOUNT TO PH-AMOUNT" is useless to an embedding model or an LLM unless
the chunk also carries what TXN-AMOUNT is (PIC S9(11)V99 COMP-3, pos 30 len 7,
copybook TXNREC), who PERFORMs the paragraph, and which DB2 tables / VSAM files
it touches. cobrag produces chunks that carry that context.

Subcommands
  analyze   Parse fixed-format COBOL, expand copybooks, analyze, emit artifacts.
  search    BM25 retrieval over chunks.jsonl (offline RAG smoke test).

analyze outputs (--out directory)
  chunks.jsonl          paragraph / record_layout / program_summary chunks
  <PGM>.flow.mmd        Mermaid control flow (PERFORM, THRU, GO TO, fall-through)
  <PGM>.resources.mmd   Mermaid paragraph -> DB2 / VSAM / CICS / IMS resources
  <PGM>.layout.md       record layouts, 1-based positions, lengths, usage
  <PGM>.symnames        DFSORT SYMNAMES generated from the record layouts
  crud_matrix.md        paragraph-level and program-level CRUD matrices
  report.md             inventory, cross-references, dead code, migration flags

Known limits
  Reference (fixed) format only: cols 1-6 sequence, 7 indicator, 8-72 code.
  One program per member (no nested programs / END PROGRAM).
  COPY must begin a statement. REPLACING: ==pseudo-text== BY ==x== and word BY word.
  ALTER is flagged, not modelled. GO TO DEPENDING ON targets are all treated as edges.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

CRUD_ORDER = 'CRUD'

# =============================================================================
# 1. Literal handling - every analysis step runs on literal-masked text so that
#    'INSERT PAYMENT_HIST FAILED' inside a MOVE is never mistaken for SQL.
# =============================================================================
LIT_TOKEN = re.compile(r'<L(\d+)>')


def mask_literals(text):
    """Replace each literal with <Ln>. Handles doubled quotes and X'..'/N'..' prefixes."""
    out, lits, i, n = [], [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"":
            prefix = ''
            if out and out[-1] in 'XxNnGgZz' and (len(out) < 2 or not (out[-2].isalnum() or out[-2] == '-')):
                prefix = out.pop()
            j = i + 1
            while j < n:
                if text[j] == c:
                    if j + 1 < n and text[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            lits.append(prefix + text[i:j + 1])
            out.append(f'<L{len(lits) - 1}>')
            i = j + 1
        else:
            out.append(c)
            i += 1
    return ''.join(out), lits


def unmask(text, lits):
    return LIT_TOKEN.sub(lambda m: lits[int(m.group(1))], text)


def upper_code(text):
    """Uppercase everything except literal contents (COBOL is case-insensitive)."""
    masked, lits = mask_literals(text)
    return unmask(masked.upper(), lits)


def open_quote(text):
    """Return the quote char if text ends inside an unterminated literal."""
    q, i, n = None, 0, len(text)
    while i < n:
        c = text[i]
        if q is None:
            if c in "'\"":
                q = c
        elif c == q:
            if i + 1 < n and text[i + 1] == q:
                i += 1
            else:
                q = None
        i += 1
    return q


def strip_quotes(lit):
    if lit and len(lit) >= 2 and lit[0] in "'\"" and lit[-1] == lit[0]:
        return lit[1:-1].replace(lit[0] * 2, lit[0])
    return lit


# =============================================================================
# 2. Source reader - fixed format, comments, debug lines, continuation lines
# =============================================================================
@dataclass(eq=False)
class SrcLine:
    text: str          # columns 8-72 (Area A starts at index 0)
    area_a: bool       # first non-blank in columns 8-11
    member: str        # program or copybook member name
    lineno: int        # physical line number inside that member
    seg: int = 0       # start of last physical segment inside text (continuations)
    segw: int = 65     # width of that segment up to column 72
    comment: bool = False   # '*' or '/' in column 7 - kept for chunks, ignored by analysis


def read_fixed(path, member):
    out = []
    raw_lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    for no, raw in enumerate(raw_lines, 1):
        raw = raw.expandtabs(8)
        if len(raw) < 8 or not raw[7:72].strip():
            continue
        ind = raw[6]
        if ind in 'Dd':                         # debugging line (no WITH DEBUGGING MODE)
            continue
        if ind in '*/':                         # comment / eject: keep for LLM context
            out.append(SrcLine(raw[7:72], False, member, no, comment=True))
            continue
        code = raw[7:72]
        lead = len(code) - len(code.lstrip())
        prev = next((x for x in reversed(out) if not x.comment), None)
        if ind == '-' and prev is not None:     # continuation line
            padded = prev.text[:prev.seg] + prev.text[prev.seg:].ljust(prev.segw)
            q = open_quote(prev.text)
            if q:                               # continued literal: runs to col 72
                start = lead + 1 if code[lead:lead + 1] == q else lead
                prev.seg = len(padded)
                prev.text = padded + code[start:]
                prev.segw = 65 - start
            else:                               # continued word
                base = prev.text.rstrip()
                prev.seg = len(base)
                prev.text = base + code[lead:]
                prev.segw = 65 - lead
            continue
        out.append(SrcLine(code, lead < 4, member, no))
    for ln in out:
        ln.text = ln.text.rstrip()
    return out


# =============================================================================
# 3. Copybook resolution (COPY, COPY REPLACING, EXEC SQL INCLUDE)
# =============================================================================
class CopyResolver:
    EXTS = ['', '.cpy', '.CPY', '.cbl', '.CBL', '.cob', '.COB', '.copy', '.txt']

    def __init__(self, dirs):
        self.dirs = [Path(d) for d in dirs if Path(d).is_dir()]

    def find(self, member):
        for d in self.dirs:
            for e in self.EXTS:
                p = d / f'{member}{e}'
                if p.is_file():
                    return p
            for p in d.iterdir():
                if p.is_file() and p.stem.upper() == member.upper():
                    return p
        return None


TERM_PERIOD = re.compile(r'\.(?=\s|$)')
REPL_OPERAND = r"(==.*?==|'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[^\s.]+)"
REPL_PAIR = re.compile(REPL_OPERAND + r'\s+BY\s+' + REPL_OPERAND, re.S | re.I)


def statement_end(lines, i):
    for j in range(i, len(lines)):
        if lines[j].comment:
            continue
        masked, _ = mask_literals(lines[j].text)
        if TERM_PERIOD.search(masked):
            return j
    return len(lines) - 1


def parse_copy_stmt(stmt):
    up = upper_code(stmt)
    m = re.match(r"\s*COPY\s+('[^']+'|\"[^\"]+\"|[A-Z0-9#@$-]+)", up)
    member = strip_quotes(m.group(1)) if m else '?'
    pairs = []
    r = re.search(r'\bREPLACING\b(.*)$', up, re.S)
    if r:
        body = re.sub(r'\.\s*$', '', r.group(1).strip())
        for a, b in REPL_PAIR.findall(body):
            pairs.append((a, b))
    return member, pairs


def apply_replacing(lines, pairs):
    compiled = []
    for a, b in pairs:
        a_s = a[2:-2].strip() if a.startswith('==') else a
        b_s = b[2:-2].strip() if b.startswith('==') else b
        if not a_s:
            continue
        core = r'\s+'.join(re.escape(w) for w in a_s.split())
        if re.match(r'[A-Za-z0-9]', a_s[0]) or re.match(r'[A-Za-z0-9]', a_s[-1]):
            pat = re.compile(r'(?<![A-Za-z0-9-])' + core + r'(?![A-Za-z0-9-])', re.I)
        else:                                    # :TAG: style - plain substring
            pat = re.compile(core, re.I)
        compiled.append((pat, b_s))
    for ln in lines:
        for pat, rep in compiled:
            ln.text = pat.sub(lambda _m, r=rep: r, ln.text)


def expand_copies(lines, resolver, ctx, depth=0):
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if ln.comment:
            out.append(ln)
            i += 1
            continue
        up = upper_code(ln.text).strip()
        if re.match(r'^COPY\b', up):
            j = statement_end(lines, i)
            stmt = ' '.join(x.text.strip() for x in lines[i:j + 1] if not x.comment)
            member, pairs = parse_copy_stmt(stmt)
            path = resolver.find(member) if depth < 10 else None
            ctx.append({'member': member, 'resolved': bool(path), 'from': ln.member,
                        'line': ln.lineno, 'replacing': [f'{a} BY {b}' for a, b in pairs],
                        'kind': 'COPY'})
            if path:
                sub = read_fixed(path, member)
                if pairs:
                    apply_replacing(sub, pairs)
                out.extend(expand_copies(sub, resolver, ctx, depth + 1))
            i = j + 1
            continue
        m = re.match(r'^EXEC\s+SQL\s+INCLUDE\s+([A-Z0-9#@$-]+)', up)
        if m and m.group(1) not in ('SQLCA', 'SQLDA'):
            j = i
            while j < len(lines) and 'END-EXEC' not in upper_code(lines[j].text):
                j += 1
            path = resolver.find(m.group(1)) if depth < 10 else None
            ctx.append({'member': m.group(1), 'resolved': bool(path), 'from': ln.member,
                        'line': ln.lineno, 'replacing': [], 'kind': 'SQL INCLUDE'})
            if path:
                out.extend(expand_copies(read_fixed(path, m.group(1)), resolver, ctx, depth + 1))
                i = j + 1
                continue
        out.append(ln)
        i += 1
    return out


RULE_LINE = re.compile(r'^[\s*=\-_.+#]*$')


def comment_text(lines):
    """Comment lines -> list of meaningful text (drops separator rules)."""
    return [l.text.strip(' *') for l in lines if l.comment and not RULE_LINE.match(l.text)]


def leading_comments(lines, i):
    j = i - 1
    while j >= 0 and lines[j].comment:
        j -= 1
    return comment_text(lines[j + 1:i])


def render(ln):
    """Column 7 indicator + columns 8-72: keeps comments and Area A/B alignment."""
    return ('*' if ln.comment else ' ') + ln.text


def sentences(lines):
    """Split lines into COBOL sentences/entries. Returns (text, first_idx, last_idx)."""
    res, buf, first = [], [], None
    for idx, ln in enumerate(lines):
        if ln.comment:
            continue
        masked, lits = mask_literals(ln.text)
        start = 0
        for m in TERM_PERIOD.finditer(masked):
            seg = masked[start:m.start()]
            if first is None:
                first = idx
            buf.append(unmask(seg, lits))
            text = ' '.join(x.strip() for x in buf if x.strip())
            if text:
                res.append((text, first, idx))
            buf, first, start = [], None, m.end()
        rest = masked[start:]
        if rest.strip():
            if first is None:
                first = idx
            buf.append(unmask(rest, lits))
    if buf:
        text = ' '.join(x.strip() for x in buf if x.strip())
        if text:
            res.append((text, first, len(lines) - 1))
    return res


# =============================================================================
# 4. Data division model - PIC sizing, USAGE, OCCURS, REDEFINES, offsets
# =============================================================================
USAGE_WORDS = {
    'COMP': 'COMP', 'COMPUTATIONAL': 'COMP', 'COMP-4': 'COMP', 'COMPUTATIONAL-4': 'COMP',
    'BINARY': 'COMP', 'COMP-5': 'COMP-5', 'COMPUTATIONAL-5': 'COMP-5',
    'COMP-3': 'COMP-3', 'COMPUTATIONAL-3': 'COMP-3', 'PACKED-DECIMAL': 'COMP-3',
    'COMP-1': 'COMP-1', 'COMPUTATIONAL-1': 'COMP-1', 'COMP-2': 'COMP-2',
    'COMPUTATIONAL-2': 'COMP-2', 'DISPLAY': 'DISPLAY', 'DISPLAY-1': 'DISPLAY-1',
    'NATIONAL': 'NATIONAL', 'INDEX': 'INDEX', 'POINTER': 'POINTER', 'POINTER-32': 'POINTER',
    'PROCEDURE-POINTER': 'PROCEDURE-POINTER', 'FUNCTION-POINTER': 'POINTER',
}
CLAUSE_KW = {
    'PIC', 'PICTURE', 'USAGE', 'REDEFINES', 'OCCURS', 'VALUE', 'VALUES', 'SIGN', 'LEADING',
    'TRAILING', 'SEPARATE', 'SYNC', 'SYNCHRONIZED', 'JUST', 'JUSTIFIED', 'BLANK',
    'EXTERNAL', 'GLOBAL', 'RENAMES', 'IS', 'ARE', 'TIMES', 'INDEXED', 'ASCENDING',
    'DESCENDING', 'KEY', 'DEPENDING', 'ON', 'BY', 'TO', 'CHARACTER', 'WHEN',
    'GROUP-USAGE', 'VOLATILE',
} | set(USAGE_WORDS)
NO_PIC_SIZE = {'COMP-1': 4, 'COMP-2': 8, 'INDEX': 4, 'POINTER': 4, 'PROCEDURE-POINTER': 8}


@dataclass(eq=False)
class DataItem:
    level: int
    name: str
    member: str
    lineno: int
    section: str
    fd: str | None
    pic: str | None = None
    usage: str = 'DISPLAY'
    usage_explicit: bool = False
    redefines: str | None = None
    occurs_min: int = 0
    occurs_max: int = 0
    depending_on: str | None = None
    keys: list = field(default_factory=list)
    indexes: list = field(default_factory=list)
    value: str | None = None
    sign_leading: bool = False
    sign_separate: bool = False
    renames: str | None = None
    children: list = field(default_factory=list)
    conditions: list = field(default_factory=list)
    parent: 'DataItem | None' = None
    record: 'DataItem | None' = None
    offset: int = 0
    length: int = 0
    total: int = 0
    category: str = ''
    digits: int = 0
    scale: int = 0
    signed: bool = False
    comments: list = field(default_factory=list)

    def in_occurs(self):
        p = self.parent
        while p is not None:
            if p.occurs_max:
                return True
            p = p.parent
        return False


def expand_pic(pic):
    return re.sub(r'(.)\((\d+)\)', lambda m: m.group(1) * int(m.group(2)), pic.upper())


def pic_attrs(pic, usage, sign_sep):
    """Return (category, storage_bytes, digits, scale, signed) per Enterprise COBOL rules."""
    e = expand_pic(pic)
    s = set(e)
    if s <= set('9SVP'):
        digits = e.count('9')
        scale = e.split('V', 1)[1].count('9') if 'V' in e else 0
        signed = 'S' in e
        if usage in ('COMP', 'COMP-5'):
            size = 2 if digits <= 4 else 4 if digits <= 9 else 8
        elif usage == 'COMP-3':
            size = digits // 2 + 1
        elif usage == 'NATIONAL':
            size = 2 * (digits + (1 if signed and sign_sep else 0))
        else:
            size = digits + (1 if signed and sign_sep else 0)
        return 'numeric', size, digits, scale, signed
    if s <= set('X'):
        return 'alphanumeric', len(e), 0, 0, False
    if s <= set('A'):
        return 'alphabetic', len(e), 0, 0, False
    if s <= set('N'):
        return 'national', 2 * len(e), 0, 0, False
    if s <= set('GB') and 'G' in s:
        return 'dbcs', 2 * len(e), 0, 0, False
    if s <= set('AX9'):
        return 'alphanumeric', len(e), 0, 0, False
    size = len(e) - e.count('V') - e.count('P')
    numeric_edit = bool(s & set('Z*+-$.,')) or e.endswith(('CR', 'DB'))
    if usage == 'NATIONAL':
        size *= 2
    return ('numeric-edited' if numeric_edit else 'alphanumeric-edited'), size, e.count('9'), 0, False


def parse_data_entry(text, section, fd, member, lineno):
    masked, lits = mask_literals(upper_code(text))
    tok = masked.split()
    if not tok or not tok[0].isdigit():
        return None
    level = int(tok[0])
    j = 1
    if j < len(tok) and tok[j] not in CLAUSE_KW:
        name = tok[j]
        j += 1
    else:
        name = 'FILLER'
    it = DataItem(level, name, member, lineno, section, fd)
    n = len(tok)

    def skip(*words):
        nonlocal j
        while j < n and tok[j] in words:
            j += 1

    while j < n:
        t = tok[j]
        if t == 'REDEFINES' and j + 1 < n:
            it.redefines = tok[j + 1]
            j += 2
        elif t in ('PIC', 'PICTURE'):
            j += 1
            skip('IS')
            if j < n:
                it.pic = tok[j]
                j += 1
        elif t == 'USAGE':
            j += 1
            skip('IS')
            if j < n and tok[j] in USAGE_WORDS:
                it.usage, it.usage_explicit = USAGE_WORDS[tok[j]], True
                j += 1
        elif t in USAGE_WORDS:
            it.usage, it.usage_explicit = USAGE_WORDS[t], True
            j += 1
        elif t == 'OCCURS':
            j += 1
            if j < n and tok[j].isdigit():
                it.occurs_max = int(tok[j])
                j += 1
            if j + 1 < n and tok[j] == 'TO' and tok[j + 1].isdigit():
                it.occurs_min, it.occurs_max = it.occurs_max, int(tok[j + 1])
                j += 2
            skip('TIMES')
            while j < n:
                if tok[j] == 'DEPENDING':
                    j += 1
                    skip('ON')
                    if j < n:
                        it.depending_on = tok[j]
                        j += 1
                elif tok[j] in ('ASCENDING', 'DESCENDING'):
                    j += 1
                    skip('KEY', 'IS')
                    while j < n and tok[j] not in CLAUSE_KW:
                        it.keys.append(tok[j])
                        j += 1
                elif tok[j] == 'INDEXED':
                    j += 1
                    skip('BY')
                    while j < n and tok[j] not in CLAUSE_KW:
                        it.indexes.append(tok[j])
                        j += 1
                else:
                    break
        elif t in ('VALUE', 'VALUES'):
            j += 1
            skip('IS', 'ARE')
            vals = []
            while j < n and tok[j] not in CLAUSE_KW:
                vals.append(tok[j])
                j += 1
            it.value = unmask(' '.join(vals), lits)
        elif t == 'SIGN':
            j += 1
            skip('IS')
        elif t == 'LEADING':
            it.sign_leading = True
            j += 1
        elif t == 'TRAILING':
            j += 1
        elif t == 'SEPARATE':
            it.sign_separate = True
            j += 1
            skip('CHARACTER')
        elif t == 'BLANK':
            j += 1
            skip('WHEN', 'ZERO', 'ZEROS', 'ZEROES')
        elif t == 'RENAMES':
            it.renames = ' '.join(tok[j + 1:])
            break
        else:
            j += 1
    return it


def compute_layout(rec):
    def inherit(it, inh):
        if not it.usage_explicit and inh:
            it.usage = inh
        nxt = it.usage if it.usage_explicit else inh
        for ch in it.children:
            inherit(ch, nxt)

    def size(it, base):
        it.offset = base
        if it.children:
            cursor = end = base
            seen = {}
            for ch in it.children:
                start = seen[ch.redefines].offset if ch.redefines in seen else cursor
                size(ch, start)
                ext = start + ch.total
                if not ch.redefines:
                    cursor = ext
                end = max(end, ext)
                seen[ch.name] = ch
            it.length = end - base
            it.category = 'group'
        elif it.pic:
            it.category, it.length, it.digits, it.scale, it.signed = pic_attrs(
                it.pic, it.usage, it.sign_separate)
        else:
            it.length = NO_PIC_SIZE.get(it.usage, 0)
            it.category = 'float' if it.usage in ('COMP-1', 'COMP-2') else it.usage.lower()
        it.total = it.length * (it.occurs_max or 1)

    inherit(rec, None)
    size(rec, 0)


# =============================================================================
# 5. Program model
# =============================================================================
VERBS = set('''ACCEPT ADD ALLOCATE ALTER CALL CANCEL CLOSE COMPUTE CONTINUE DELETE DISPLAY
DIVIDE ELSE EVALUATE EXIT FREE GO GOBACK IF INITIALIZE INSPECT INVOKE MERGE MOVE MULTIPLY
OPEN PERFORM READ RELEASE RETURN REWRITE SEARCH SET SORT START STOP STRING SUBTRACT
UNSTRING WHEN WRITE AT INVALID JSON XML EXEC-BLOCK EXEC-CICS-RETURN'''.split())
SPECIAL_REGS = {
    'RETURN-CODE': 'special register - step condition code',
    'SORT-RETURN': 'special register - SORT completion code',
    'TALLY': 'special register',
    'SQLCODE': 'SQLCA - DB2 return code', 'SQLSTATE': 'SQLCA - SQLSTATE',
    'SQLERRD': 'SQLCA - diagnostic array (SQLERRD(3) = rows affected)',
    'SQLERRMC': 'SQLCA - error tokens', 'SQLWARN0': 'SQLCA - warning flag',
    'EIBCALEN': 'CICS EIB - COMMAREA length', 'EIBAID': 'CICS EIB - attention id',
    'EIBTRNID': 'CICS EIB - transaction id', 'EIBRESP': 'CICS EIB - RESP',
    'EIBRESP2': 'CICS EIB - RESP2', 'EIBTASKN': 'CICS EIB - task number',
    'EIBDATE': 'CICS EIB - date', 'EIBTIME': 'CICS EIB - time',
}
KNOWN_CALLS = {
    'CEE3ABD': 'LE forced abend', 'ILBOABN0': 'COBOL forced abend',
    'DSNTIAR': 'DB2 SQLCA message formatter', 'CBLTDLI': 'IMS DL/I interface',
    'AIBTDLI': 'IMS DL/I AIB interface', 'CEETDLI': 'IMS DL/I via LE',
    'DSNALI': 'DB2 CAF', 'DSNRLI': 'DB2 RRSAF', 'CEEDAYS': 'LE date to Lilian',
    'CEELOCT': 'LE local time',
}
DLI_FUNC_CRUD = {'GU': 'R', 'GHU': 'R', 'GN': 'R', 'GHN': 'R', 'GNP': 'R', 'GHNP': 'R',
                 'ISRT': 'C', 'REPL': 'U', 'DLET': 'D'}
DLI_LONG = {'GET UNIQUE': 'GU', 'GET NEXT IN PARENT': 'GNP', 'GET NEXT': 'GN',
            'INSERT': 'ISRT', 'REPLACE': 'REPL', 'DELETE': 'DLET'}
CICS_FILE_CRUD = {'READ': 'R', 'READNEXT': 'R', 'READPREV': 'R', 'STARTBR': 'R',
                  'WRITE': 'C', 'REWRITE': 'U', 'DELETE': 'D'}
CICS_Q_CRUD = {'READQ': 'R', 'WRITEQ': 'C', 'DELETEQ': 'D'}
CICS_TWO = {'SEND': {'MAP', 'TEXT', 'CONTROL', 'PAGE'}, 'RECEIVE': {'MAP'},
            'READQ': {'TS', 'TD'}, 'WRITEQ': {'TS', 'TD'}, 'DELETEQ': {'TS', 'TD'},
            'HANDLE': {'CONDITION', 'AID', 'ABEND'}, 'GET': {'CONTAINER'},
            'PUT': {'CONTAINER'}}
TOKEN_RE = re.compile(r'<L\d+>|<EOS>|[A-Z0-9](?:[A-Z0-9-]*[A-Z0-9])?|[()=<>+*/,:]')
FROM_RE = re.compile(r'\bFROM\s+(.+?)(?=\b(?:WHERE|GROUP|ORDER|HAVING|FETCH|FOR|WITH|UNION|'
                     r'EXCEPT|INTERSECT|OPTIMIZE|QUERYNO|SKIP|INTO)\b|\)|$)')
SQL_NAME = re.compile(r'^[A-Z#@$][A-Z0-9_#@$.]*$')


@dataclass(eq=False)
class Para:
    name: str
    kind: str                     # paragraph | section | entry
    section: str | None
    idx: int
    lines: list
    body: list = field(default_factory=list)
    performs: list = field(default_factory=list)
    gotos: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    sql: list = field(default_factory=list)
    cics: list = field(default_factory=list)
    dli: list = field(default_factory=list)
    file_ops: list = field(default_factory=list)
    crud: dict = field(default_factory=lambda: defaultdict(set))
    refs: list = field(default_factory=list)
    writes: list = field(default_factory=list)
    specials: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    terminal: str | None = None
    alter: bool = False
    reachable: bool = False
    callers: list = field(default_factory=list)


class Program:
    def __init__(self, path):
        self.path = Path(path)
        self.member = self.path.stem.upper()
        self.id = self.member
        self.lines = []
        self.copybooks = []
        self.selects = {}
        self.items = []
        self.records = []
        self.dd = defaultdict(list)
        self.conds = {}
        self.index_names = {}
        self.record_fd = {}
        self.fd_records = defaultdict(list)
        self.data_sql = []
        self.cursors = {}
        self.paras = []
        self.para_idx = {}
        self.sections = {}
        self.using = []
        self.fallthrough = set()
        self.tags = []
        self.header_comments = []

    # -- lookups ---------------------------------------------------------------
    def is_data(self, t):
        return t in self.dd or t in self.conds or t in self.index_names

    def is_proc(self, t):
        return t in self.para_idx or t in self.sections

    def proc_range(self, name, thru=None):
        def rng(n):
            if n in self.sections:
                ids = self.sections[n]
                return ids[0], ids[-1]
            i = self.para_idx[n]
            return i, i
        s, e = rng(name)
        if thru and self.is_proc(thru):
            e = rng(thru)[1]
        return s, max(s, e)

    def value_of(self, name):
        for it in self.dd.get(name, []):
            if it.value:
                return strip_quotes(it.value.strip())
        return None

    def file_res(self, fname):
        return f'FILE:{fname}'

    def resource_label(self, res):
        kind, _, name = res.partition(':')
        if kind == 'FILE' and name in self.selects:
            s = self.selects[name]
            return f"{name} (DD {s['dd']}, {s['vsam']})"
        return res


# =============================================================================
# 6. Parsing: divisions, environment, data, procedure
# =============================================================================
def parse_program(path, resolver):
    prog = Program(path)
    raw = read_fixed(path, prog.member)
    prog.lines = expand_copies(raw, resolver, prog.copybooks)
    lines = prog.lines

    div = {}
    for i, ln in enumerate(lines):
        if ln.comment:
            continue
        m = re.match(r'^(IDENTIFICATION|ID|ENVIRONMENT|DATA|PROCEDURE)\s+DIVISION\b',
                     upper_code(ln.text).strip())
        if m:
            key = 'IDENTIFICATION' if m.group(1) == 'ID' else m.group(1)
            div.setdefault(key, i)
    n = len(lines)
    order = sorted(div.items(), key=lambda kv: kv[1])
    bounds = {k: (v, order[x + 1][1] if x + 1 < len(order) else n) for x, (k, v) in enumerate(order)}

    first_code = bounds.get('DATA', bounds.get('PROCEDURE', (n, n)))[0]
    prog.header_comments = comment_text([l for l in lines[:first_code] if l.member == prog.member])
    if 'IDENTIFICATION' in bounds:
        s, e = bounds['IDENTIFICATION']
        for text, _, _ in sentences(lines[s:e]):
            m = re.match(r"^PROGRAM-ID\s*\.?\s*('[^']+'|\"[^\"]+\"|[A-Z0-9#@$-]+)", upper_code(text))
            if m:
                prog.id = strip_quotes(m.group(1))
                break
    if 'ENVIRONMENT' in bounds:
        parse_environment(prog, lines[slice(*bounds['ENVIRONMENT'])])
    if 'DATA' in bounds:
        parse_data(prog, lines[slice(*bounds['DATA'])])
    if 'PROCEDURE' in bounds:
        parse_procedure(prog, lines[slice(*bounds['PROCEDURE'])])
    finalize(prog)
    return prog


def parse_environment(prog, lines):
    for text, _, _ in sentences(lines):
        masked, lits = mask_literals(upper_code(text))
        tok = masked.split()
        if 'SELECT' not in tok:
            continue
        tok = tok[tok.index('SELECT'):]
        j = 1
        if j < len(tok) and tok[j] == 'OPTIONAL':
            j += 1
        if j >= len(tok):
            continue
        name = tok[j]
        sel = {'name': name, 'assign': None, 'org': 'SEQUENTIAL', 'access': 'SEQUENTIAL',
               'record_key': None, 'alt_keys': [], 'status': None, 'relative_key': None}
        k = j + 1
        while k < len(tok):
            t = tok[k]
            if t == 'ASSIGN':
                k += 1
                if k < len(tok) and tok[k] in ('TO', 'USING'):
                    k += 1
                if k < len(tok):
                    sel['assign'] = strip_quotes(unmask(tok[k], lits))
            elif t == 'ORGANIZATION':
                k += 1
                if k < len(tok) and tok[k] == 'IS':
                    k += 1
                if k < len(tok):
                    sel['org'] = 'LINE SEQUENTIAL' if tok[k] == 'LINE' else tok[k]
            elif t in ('INDEXED', 'RELATIVE') and tok[k - 1] not in ('IS', 'ORGANIZATION'):
                sel['org'] = t
            elif t == 'ACCESS':
                k += 1
                while k < len(tok) and tok[k] in ('MODE', 'IS'):
                    k += 1
                if k < len(tok):
                    sel['access'] = tok[k]
            elif t == 'RECORD' and k + 1 < len(tok) and tok[k - 1] != 'ALTERNATE':
                k += 1
                while k < len(tok) and tok[k] in ('KEY', 'IS'):
                    k += 1
                if k < len(tok):
                    sel['record_key'] = tok[k]
            elif t == 'ALTERNATE':
                k += 1
                while k < len(tok) and tok[k] in ('RECORD', 'KEY', 'IS'):
                    k += 1
                if k < len(tok):
                    sel['alt_keys'].append(tok[k])
            elif t == 'RELATIVE' and k + 1 < len(tok) and tok[k + 1] in ('KEY', 'IS'):
                k += 1
                while k < len(tok) and tok[k] in ('KEY', 'IS'):
                    k += 1
                if k < len(tok):
                    sel['relative_key'] = tok[k]
                    sel['org'] = 'RELATIVE'
            elif t == 'STATUS':
                k += 1
                if k < len(tok) and tok[k] == 'IS':
                    k += 1
                if k < len(tok):
                    sel['status'] = tok[k]
            k += 1
        assign = (sel['assign'] or name).upper()
        parts = assign.split('-')
        sel['dd'] = parts[-1]
        if sel['org'] == 'INDEXED':
            sel['vsam'] = 'VSAM KSDS'
        elif sel['org'] == 'RELATIVE':
            sel['vsam'] = 'VSAM RRDS'
        elif 'AS' in parts[:-1]:
            sel['vsam'] = 'VSAM ESDS'
        else:
            sel['vsam'] = 'QSAM'
        prog.selects[name] = sel


def parse_data(prog, lines):
    section, fd = None, None
    stack, last, rec = [], None, None
    for text, i0, _ in sentences(lines):
        ln = lines[i0]
        up = upper_code(text).strip()
        m = re.match(r'^(FILE|WORKING-STORAGE|LOCAL-STORAGE|LINKAGE|COMMUNICATION|REPORT|SCREEN)'
                     r'\s+SECTION$', up)
        if m:
            section, fd, stack, last = m.group(1), None, [], None
            continue
        m = re.match(r'^(FD|SD|RD)\s+([A-Z0-9-]+)', up)
        if m:
            fd, stack, last = m.group(2), [], None
            if m.group(1) == 'SD' and fd in prog.selects:
                prog.selects[fd]['vsam'] = 'SORT WORK'
            continue
        m = re.match(r'^EXEC\s+SQL\b(.*?)\bEND-EXEC$', up, re.S)
        if m:
            masked, lits = mask_literals(m.group(1))
            info = analyze_sql(masked, lits)
            info['member'], info['line'] = ln.member, ln.lineno
            prog.data_sql.append(info)
            if info['op'] == 'DECLARE CURSOR':
                prog.cursors[info['cursor']] = info
            continue
        it = parse_data_entry(text, section, fd, ln.member, ln.lineno)
        if it is None:
            continue
        if it.level == 88:
            if last is not None:
                it.parent, it.record = last, last.record
                last.conditions.append(it)
                prog.conds[it.name] = it
            continue
        if it.level == 66:
            it.record = rec
            prog.dd[it.name].append(it)
            continue
        if it.level in (1, 77) or not stack:
            stack, rec = [it], it
            it.record = it
            it.comments = leading_comments(lines, i0)
            prog.records.append(it)
            if section == 'FILE' and fd:
                prog.record_fd[it.name] = fd
                prog.fd_records[fd].append(it.name)
        else:
            while stack and stack[-1].level >= it.level:
                stack.pop()
            parent = stack[-1] if stack else rec
            parent.children.append(it)
            it.parent, it.record = parent, parent.record
            stack.append(it)
        last = it
        prog.items.append(it)
        if it.name != 'FILLER':
            prog.dd[it.name].append(it)
        for ix in it.indexes:
            prog.index_names[ix] = it
    for r in prog.records:
        compute_layout(r)


PARA_RE = re.compile(r'^([A-Z0-9][A-Z0-9-]*)\s*\.(?:\s+(.*))?$')
SECT_RE = re.compile(r'^([A-Z0-9][A-Z0-9-]*)\s+SECTION(?:\s+\d+)?\s*\.(?:\s+(.*))?$')
NOT_PARA = {'EXIT', 'GOBACK', 'CONTINUE', 'ELSE', 'END-IF', 'END-PERFORM', 'END-EXEC',
            'END-EVALUATE', 'END-READ', 'END-SEARCH', 'END-CALL', 'DECLARATIVES'}


def parse_procedure(prog, lines):
    sents = sentences(lines)
    hdr_end = sents[0][2] if sents else 0
    hdr = upper_code(sents[0][0]) if sents else ''
    m = re.search(r'\bUSING\b(.*?)(?:\bRETURNING\b|$)', hdr)
    if m:
        prog.using = [t for t in re.findall(r'[A-Z0-9][A-Z0-9-]*', m.group(1))
                      if t not in ('BY', 'REFERENCE', 'VALUE', 'CONTENT')]
    body = lines[hdr_end + 1:]
    cur, section, pending = None, None, []
    for ln in body:
        if ln.comment:
            pending.append(ln)
            continue
        up = upper_code(ln.text).strip()
        if ln.area_a and up in ('DECLARATIVES.', 'END DECLARATIVES.'):
            continue
        ms = SECT_RE.match(up) if ln.area_a else None
        mp = PARA_RE.match(up) if (ln.area_a and not ms) else None
        if mp and mp.group(1) in NOT_PARA:
            mp = None
        if ms or mp:
            m = ms or mp
            name = m.group(1)
            kind = 'section' if ms else 'paragraph'
            if ms:
                section = name
            cur = Para(name, kind, section, len(prog.paras), pending + [ln])
            pending = []
            if m.group(2):
                cur.body.append(m.group(2))
            prog.paras.append(cur)
            prog.para_idx.setdefault(name, cur.idx)
            if section:
                prog.sections.setdefault(section, []).append(cur.idx)
            continue
        if cur is None:
            cur = Para('(PROCEDURE-ENTRY)', 'entry', None, 0, [])
            prog.paras.append(cur)
            prog.para_idx[cur.name] = 0
        cur.lines.extend(pending)
        pending = []
        cur.lines.append(ln)
        cur.body.append(upper_code(ln.text))
    if cur is not None:
        cur.lines.extend(pending)
    for p in prog.paras:
        analyze_para(prog, p)


# =============================================================================
# 7. Statement analysis
# =============================================================================
def names_after(st, start, stops, is_data):
    out, depth = [], 0
    for t in st[start:]:
        if t == '(':
            depth += 1
            continue
        if t == ')':
            depth = max(0, depth - 1)
            continue
        if depth:
            continue
        if t in stops:
            break
        if is_data(t):
            out.append(t)
    return out


def first_index(st, *words):
    for i, t in enumerate(st):
        if t in words:
            return i
    return -1


def analyze_sql(masked_body, lits):
    b = ' '.join(masked_body.split())
    info = {'op': None, 'tables': {}, 'cursor': None, 'host_vars': [], 'into_vars': [],
            'with_hold': False, 'text': unmask(b, lits)}
    words = b.split()
    if not words:
        return info

    def add(t, c):
        t = t.strip().rstrip(',')
        if t and SQL_NAME.match(t):
            info['tables'].setdefault(t, set()).add(c)

    def from_tables(s):
        for m in FROM_RE.finditer(s):
            for part in re.split(r',|\bJOIN\b', m.group(1)):
                part = part.strip()
                if not part or part.startswith('('):
                    continue
                w = part.split()[0]
                if w in ('LEFT', 'RIGHT', 'INNER', 'FULL', 'OUTER', 'CROSS', 'LATERAL',
                         'TABLE', 'FINAL', 'OLD', 'NEW') or w.startswith(':'):
                    continue
                add(w, 'R')

    def hv(s):
        out = []
        for h in re.findall(r':\s*([A-Z0-9][A-Z0-9-]*(?:\.[A-Z0-9][A-Z0-9-]*)*)', s):
            out.extend(h.split('.'))
        return out

    info['host_vars'] = hv(b)
    w0 = words[0]
    op = w0
    if w0 == 'SELECT':
        from_tables(b)
        m = re.search(r'\bINTO\b(.*?)\bFROM\b', b)
        if m:
            info['into_vars'] = hv(m.group(1))
    elif w0 == 'INSERT':
        m = re.match(r'INSERT\s+INTO\s+([A-Z0-9_#@$.]+)', b)
        if m:
            add(m.group(1), 'C')
            from_tables(b[m.end():])
    elif w0 == 'UPDATE':
        m = re.match(r'UPDATE\s+([A-Z0-9_#@$.]+)', b)
        if m:
            add(m.group(1), 'U')
            from_tables(b[m.end():])
        mc = re.search(r'WHERE\s+CURRENT\s+OF\s+([A-Z0-9_#@$-]+)', b)
        if mc:
            info['cursor'] = mc.group(1)
    elif w0 == 'DELETE':
        m = re.match(r'DELETE\s+FROM\s+([A-Z0-9_#@$.]+)', b)
        if m:
            add(m.group(1), 'D')
            from_tables(b[m.end():])
    elif w0 == 'MERGE':
        m = re.match(r'MERGE\s+INTO\s+([A-Z0-9_#@$.]+)', b)
        if m:
            add(m.group(1), 'C')
            add(m.group(1), 'U')
        mu = re.search(r'\bUSING\s+([A-Z0-9_#@$.]+)', b)
        if mu:
            add(mu.group(1), 'R')
    elif w0 == 'DECLARE':
        mc = re.match(r'DECLARE\s+([A-Z0-9_#@$-]+)\s+(?:[A-Z]+\s+){0,4}CURSOR\b(.*)$', b)
        if mc:
            op = 'DECLARE CURSOR'
            info['cursor'] = mc.group(1)
            info['with_hold'] = 'WITH HOLD' in mc.group(2)
            from_tables(mc.group(2))
        elif re.match(r'DECLARE\s+\S+\s+TABLE\b', b):
            op = 'DECLARE TABLE'
        else:
            op = 'DECLARE'
    elif w0 in ('OPEN', 'CLOSE') and len(words) > 1:
        info['cursor'] = words[1]
    elif w0 == 'FETCH':
        orient = {'NEXT', 'PRIOR', 'FIRST', 'LAST', 'CURRENT', 'BEFORE', 'AFTER', 'ABSOLUTE',
                  'RELATIVE', 'ROWSET', 'STARTING', 'AT', 'FROM', 'SENSITIVE', 'INSENSITIVE',
                  'WITH', 'CONTINUE', 'NO'}
        for w in words[1:]:
            if w in orient or w.startswith(':') or w.lstrip('+-').isdigit():
                continue
            info['cursor'] = w
            break
        m = re.search(r'\bINTO\b(.*)$', b)
        if m:
            info['into_vars'] = hv(m.group(1))
    elif w0 == 'EXECUTE' and len(words) > 1 and words[1] == 'IMMEDIATE':
        op = 'EXECUTE IMMEDIATE'
    elif w0 == 'INCLUDE' and len(words) > 1:
        info['member'] = words[1]
    info['op'] = op
    return info


def cics_opts(text):
    return re.findall(r'([A-Z][A-Z0-9-]*)\s*(\((?:[^()]|\([^()]*\))*\))?', text)


def analyze_cics(prog, masked_body, lits):
    b = ' '.join(masked_body.split())
    words = b.split()
    info = {'command': '', 'resource': None, 'crud': None, 'program': None,
            'writes': [], 'refs': [], 'text': unmask(b, lits)}
    if not words:
        return info
    cmd, rest = words[0], b[len(words[0]):].strip()
    second = rest.split()[0] if rest.split() else ''
    if second in CICS_TWO.get(cmd, set()) and not rest[len(second):].lstrip().startswith('('):
        cmd = f'{cmd} {second}'
        rest = rest[len(second):].strip()
    info['command'] = cmd
    base = cmd.split()[0]

    def resolve(arg):
        a = unmask(arg, lits).strip()
        if a[:1] in "'\"":
            return strip_quotes(a)
        v = prog.value_of(a)
        return f'{v} (via {a})' if v else f'?{a}'

    for name, arg in cics_opts(rest):
        inner = arg[1:-1].strip() if arg else ''
        names = [t for t in TOKEN_RE.findall(inner) if prog.is_data(t)]
        info['refs'].extend(names)
        if name in ('INTO', 'SET', 'RESP', 'RESP2'):
            info['writes'].extend(names)
        if name in ('FILE', 'DATASET') and inner:
            info['resource'] = 'CICS-FILE:' + resolve(inner)
            info['crud'] = CICS_FILE_CRUD.get(base)
        elif name in ('QUEUE', 'QNAME') and inner:
            kind = 'TDQ' if cmd.endswith('TD') else 'TSQ'
            info['resource'] = f'{kind}:' + resolve(inner)
            info['crud'] = CICS_Q_CRUD.get(base)
        elif name == 'PROGRAM' and inner and base in ('LINK', 'XCTL', 'LOAD'):
            info['program'] = resolve(inner)
    return info


def analyze_dli(masked_body, lits):
    b = ' '.join(masked_body.split())
    func = b.split()[0] if b else ''
    for long, short in DLI_LONG.items():
        if b.startswith(long):
            func = short
            break
    segs = re.findall(r'SEGMENT\s*\(\s*([A-Z0-9#@$-]+)\s*\)', b)
    pcb = re.search(r'PCB\s*\(\s*([^)]+?)\s*\)', b)
    return {'function': func, 'segments': segs, 'pcb': pcb.group(1) if pcb else '?',
            'crud': DLI_FUNC_CRUD.get(func), 'text': unmask(b, lits)}


def ssa_segment(prog, ssa_name):
    """First 8 bytes of an SSA group's leading VALUE = segment name."""
    for it in prog.dd.get(ssa_name, []):
        for ch in it.children or [it]:
            if ch.value:
                v = strip_quotes(ch.value.strip())
                return re.split(r'[\s(*]', v[:9], maxsplit=1)[0] or None
    return None


def analyze_para(prog, p):
    text = '\n'.join(p.body)
    masked, lits = mask_literals(text)
    execs = []

    def take_exec(m):
        execs.append((m.group(1), m.group(2)))
        if m.group(1) == 'CICS' and re.match(r'\s*(RETURN|XCTL|ABEND)\b', m.group(2)):
            return ' EXEC-CICS-RETURN '
        return ' EXEC-BLOCK '

    cobol = re.sub(r'\bEXEC\s+(SQL|CICS|DLI)\b(.*?)\bEND-EXEC\b', take_exec, masked, flags=re.S)
    cobol = TERM_PERIOD.sub(' <EOS> ', cobol)
    toks = TOKEN_RE.findall(cobol)

    refs, writes, specials = [], [], []
    for t in toks:
        if prog.is_data(t):
            refs.append(t)
        elif t in SPECIAL_REGS:
            specials.append(t)

    # ---- EXEC blocks ---------------------------------------------------------
    for kind, body in execs:
        if kind == 'SQL':
            info = analyze_sql(body, lits)
            p.sql.append(info)
            refs.extend(h for h in info['host_vars'] if prog.is_data(h))
            writes.extend(h for h in info['into_vars'] if prog.is_data(h))
            for t, c in info['tables'].items():
                p.crud[f'DB2:{t}'] |= c
            if info['op'] == 'DECLARE CURSOR':
                prog.cursors[info['cursor']] = info
        elif kind == 'CICS':
            info = analyze_cics(prog, body, lits)
            p.cics.append(info)
            refs.extend(info['refs'])
            writes.extend(info['writes'])
            if info['resource'] and info['crud']:
                p.crud[info['resource']].add(info['crud'])
            if info['program']:
                p.calls.append({'name': info['program'], 'dynamic': info['program'].startswith('?'),
                                'via': info['command'], 'args': []})
        else:
            info = analyze_dli(body, lits)
            p.dli.append(info)
            if info['crud']:
                p.crud[f"DLI:PCB({info['pcb']})"].add(info['crud'])

    # ---- statements ----------------------------------------------------------
    stmts, cur = [], []
    for t in toks:
        if t == '<EOS>':
            if cur:
                stmts.append(cur)
            stmts.append(['<EOS>'])
            cur = []
            continue
        if (t in VERBS or t.startswith('END-')) and cur:
            stmts.append(cur)
            cur = []
        cur.append(t)
    if cur:
        stmts.append(cur)

    nest = []
    isd = prog.is_data

    def cond_parent(names):
        return [prog.conds[x].parent.name if x in prog.conds else x for x in names]

    for st in stmts:
        v = st[0]
        if v == '<EOS>':
            nest.clear()
            continue
        depth = len(nest)
        # unconditional terminators at nesting depth 0 stop fall-through
        if depth == 0 and p.terminal is None:
            if v == 'GO' and 'DEPENDING' not in st:
                p.terminal = 'GO TO'
            elif v == 'GOBACK':
                p.terminal = 'GOBACK'
            elif v == 'STOP' and len(st) > 1 and st[1] == 'RUN':
                p.terminal = 'STOP RUN'
            elif v == 'EXIT' and len(st) > 1 and st[1] == 'PROGRAM':
                p.terminal = 'EXIT PROGRAM'
            elif v == 'EXEC-CICS-RETURN':
                p.terminal = 'EXEC CICS RETURN/XCTL'
        # nesting
        if v in ('IF', 'EVALUATE', 'SEARCH'):
            nest.append(v)
        elif v == 'PERFORM' and (len(st) == 1 or not prog.is_proc(st[1])):
            nest.append('PERFORM')
        elif v in ('AT', 'INVALID'):
            if not nest or nest[-1] != 'PHRASE':
                nest.append('PHRASE')
        elif v.startswith('END-'):
            target = {'END-IF': 'IF', 'END-EVALUATE': 'EVALUATE', 'END-SEARCH': 'SEARCH',
                      'END-PERFORM': 'PERFORM'}.get(v)
            if target:
                while nest:
                    if nest.pop() == target:
                        break
            elif nest and nest[-1] == 'PHRASE':
                nest.pop()
        if any(x in st for x in ('EXCEPTION', 'OVERFLOW')) or ('SIZE' in st and 'ERROR' in st):
            if v not in ('AT', 'INVALID') and (not nest or nest[-1] != 'PHRASE'):
                nest.append('PHRASE')

        # control flow
        if v == 'PERFORM' and len(st) > 1:
            if prog.is_proc(st[1]):
                thru = st[3] if len(st) > 3 and st[2] in ('THRU', 'THROUGH') else None
                rest = st[4:] if thru else st[2:]
                loop = None
                if rest and rest[0] in ('UNTIL', 'VARYING', 'WITH', 'TEST'):
                    loop = ' '.join(rest)
                elif len(rest) > 1 and rest[1] == 'TIMES':
                    loop = f'{rest[0]} TIMES'
                p.performs.append({'target': st[1], 'thru': thru, 'loop': unmask(loop, lits) if loop else None,
                                   'range': prog.proc_range(st[1], thru), 'conditional': depth > 0})
            elif st[1] not in ('UNTIL', 'VARYING', 'WITH', 'TEST', 'FOREVER') \
                    and not isd(st[1]) and not st[1].isdigit():
                p.unresolved.append(f'PERFORM {st[1]}')
        elif v == 'GO':
            i = 2 if len(st) > 1 and st[1] == 'TO' else 1
            for t in st[i:]:
                if t == 'DEPENDING':
                    break
                if prog.is_proc(t):
                    p.gotos.append(t)
                elif t not in ('(', ')', ','):
                    p.unresolved.append(f'GO TO {t}')
        elif v == 'ALTER':
            p.alter = True
        elif v in ('SORT', 'MERGE'):
            for k, t in enumerate(st):
                if t == 'PROCEDURE':
                    j = k + 1
                    if j < len(st) and st[j] == 'IS':
                        j += 1
                    if j < len(st) and prog.is_proc(st[j]):
                        thru = st[j + 2] if j + 2 < len(st) and st[j + 1] in ('THRU', 'THROUGH') else None
                        p.performs.append({'target': st[j], 'thru': thru, 'conditional': depth > 0,
                                           'range': prog.proc_range(st[j], thru), 'via': v})
            ui, gi = first_index(st, 'USING'), first_index(st, 'GIVING')
            if ui > 0:
                for f in st[ui + 1:gi if gi > ui else None]:
                    if f in prog.selects:
                        p.file_ops.append({'verb': f'{v} USING', 'file': f})
                        p.crud[prog.file_res(f)].add('R')
            if gi > 0:
                for f in st[gi + 1:]:
                    if f in prog.selects:
                        p.file_ops.append({'verb': f'{v} GIVING', 'file': f})
                        p.crud[prog.file_res(f)].add('C')

        # CALL
        if v == 'CALL' and len(st) > 1:
            tgt = st[1]
            ui = first_index(st, 'USING')
            args = names_after(st, ui + 1, {'RETURNING', 'ON', 'EXCEPTION', 'NOT', 'OVERFLOW'},
                               isd) if ui > 0 else []
            if tgt.startswith('<L'):
                call = {'name': strip_quotes(unmask(tgt, lits)), 'dynamic': False, 'args': args,
                        'via': 'CALL literal'}
            else:
                call = {'name': tgt, 'dynamic': True, 'var': tgt, 'args': args,
                        'via': 'CALL identifier'}
            if call['name'] in ('CBLTDLI', 'AIBTDLI', 'CEETDLI') and len(args) >= 2:
                func = (prog.value_of(args[0]) or f'?{args[0]}').strip()
                seg = ssa_segment(prog, args[3]) if len(args) > 3 else None
                d = {'function': func, 'pcb': args[1], 'io_area': args[2] if len(args) > 2 else None,
                     'ssa': args[3:], 'segments': [seg] if seg else [], 'crud': DLI_FUNC_CRUD.get(func),
                     'text': ' '.join(unmask(x, lits) for x in st)}
                p.dli.append(d)
                if d['crud']:
                    p.crud[f'DLI:{args[1]}'].add(d['crud'])
                    if d['crud'] == 'R' and d['io_area']:
                        writes.append(d['io_area'])
            p.calls.append(call)

        # data modification
        if v == 'MOVE':
            i = first_index(st, 'TO')
            if i > 0:
                writes += names_after(st, i + 1, set(), isd)
        elif v == 'COMPUTE':
            writes += names_after(st, 1, {'=', 'EQUAL'}, isd)
        elif v in ('ADD', 'SUBTRACT', 'MULTIPLY', 'DIVIDE'):
            stops = {'ON', 'NOT', 'SIZE', 'REMAINDER'}
            gi = first_index(st, 'GIVING')
            if gi > 0:
                writes += names_after(st, gi + 1, stops, isd)
            else:
                kw = {'ADD': 'TO', 'SUBTRACT': 'FROM', 'MULTIPLY': 'BY', 'DIVIDE': 'INTO'}[v]
                ki = first_index(st, kw)
                if ki > 0:
                    writes += names_after(st, ki + 1, stops | {'GIVING'}, isd)
            ri = first_index(st, 'REMAINDER')
            if ri > 0:
                writes += names_after(st, ri + 1, stops, isd)
        elif v == 'INITIALIZE':
            writes += names_after(st, 1, {'REPLACING', 'WITH', 'TO', 'ALL'}, isd)
        elif v == 'SET':
            writes += cond_parent(names_after(st, 1, {'TO', 'UP', 'DOWN'}, isd))
        elif v == 'ACCEPT':
            writes += names_after(st, 1, {'FROM'}, isd)
        elif v in ('READ', 'RETURN'):
            ii = first_index(st, 'INTO')
            if ii > 0:
                writes += names_after(st, ii + 1, {'KEY', 'AT', 'INVALID', 'NOT', 'WITH'}, isd)
        elif v in ('STRING', 'UNSTRING'):
            ii = first_index(st, 'INTO')
            if ii > 0:
                writes += names_after(st, ii + 1, {'WITH', 'ON', 'NOT', 'OVERFLOW', 'TALLYING'}, isd)
            for kw in ('POINTER', 'TALLYING'):
                ki = first_index(st, kw)
                if ki > 0:
                    writes += names_after(st, ki + 1, {'ON', 'NOT', 'OVERFLOW'}, isd)[:1]
        elif v == 'INSPECT' and len(st) > 1:
            if 'REPLACING' in st or 'CONVERTING' in st:
                writes += names_after(st, 1, {'TALLYING', 'REPLACING', 'CONVERTING'}, isd)[:1]
            ti = first_index(st, 'TALLYING')
            if ti > 0:
                writes += names_after(st, ti + 1, {'FOR'}, isd)[:1]

        # file I/O
        if v == 'OPEN':
            mode = None
            for t in st[1:]:
                if t in ('INPUT', 'OUTPUT', 'I-O', 'EXTEND'):
                    mode = t
                elif t in prog.selects:
                    p.file_ops.append({'verb': 'OPEN', 'file': t, 'mode': mode})
        elif v == 'CLOSE':
            for t in st[1:]:
                if t in prog.selects:
                    p.file_ops.append({'verb': 'CLOSE', 'file': t})
        elif v in ('READ', 'DELETE', 'START') and len(st) > 1 and st[1] in prog.selects:
            verb = v + (' NEXT' if v == 'READ' and len(st) > 2 and st[2] in ('NEXT', 'PREVIOUS') else '')
            p.file_ops.append({'verb': verb, 'file': st[1]})
            p.crud[prog.file_res(st[1])].add({'READ': 'R', 'START': 'R', 'DELETE': 'D'}[v])
        elif v in ('WRITE', 'REWRITE') and len(st) > 1:
            f = prog.record_fd.get(st[1], st[1] if st[1] in prog.selects else None)
            if f:
                p.file_ops.append({'verb': v, 'file': f, 'record': st[1]})
                p.crud[prog.file_res(f)].add('C' if v == 'WRITE' else 'U')
            fi = first_index(st, 'FROM')
            if fi > 0:
                refs += names_after(st, fi + 1, set(), isd)

    p.refs = list(dict.fromkeys(refs))
    p.writes = list(dict.fromkeys(writes))
    p.specials = list(dict.fromkeys(specials))


# =============================================================================
# 8. Whole-program post-processing: cursors, dynamic calls, reachability
# =============================================================================
def finalize(prog):
    for p in prog.paras:
        for s in p.sql:
            if s['op'] in ('OPEN', 'FETCH') and s['cursor'] in prog.cursors:
                for t in prog.cursors[s['cursor']]['tables']:
                    p.crud[f'DB2:{t}'].add('R')
    all_writes = {w for p in prog.paras for w in p.writes}
    for p in prog.paras:
        for c in p.calls:
            if c.get('dynamic') and c.get('var'):
                v = prog.value_of(c['var'])
                c['resolved'] = v
                c['stable'] = c['var'] not in all_writes
                if v:
                    c['name'] = v
    reachability(prog)
    for p in prog.paras:
        for pf in p.performs:
            s, e = pf['range']
            for k in range(s, e + 1):
                q = prog.paras[k]
                if k == s:
                    q.callers.append(f"PERFORM from {p.name}" + (f" (THRU {pf['thru']})" if pf['thru'] else '')
                                     + (f" {pf['loop']}" if pf.get('loop') else ''))
                else:
                    q.callers.append(f"inside PERFORM {prog.paras[s].name} THRU {prog.paras[e].name} from {p.name}")
        for g in p.gotos:
            prog.paras[prog.para_idx[g]].callers.append(f'GO TO from {p.name}')
    for i, j in sorted(prog.fallthrough):
        prog.paras[j].callers.append(f'falls through from {prog.paras[i].name}')
    for p in prog.paras:
        p.callers = list(dict.fromkeys(p.callers))
    tags = ['CICS' if any(p.cics for p in prog.paras) else 'BATCH']
    if any(p.sql for p in prog.paras) or prog.data_sql:
        tags.append('DB2')
    if any(s['vsam'].startswith('VSAM') for s in prog.selects.values()) or \
            any(r.startswith('CICS-FILE:') for p in prog.paras for r in p.crud):
        tags.append('VSAM')
    if any(p.dli for p in prog.paras):
        tags.append('IMS DL/I')
    prog.tags = tags


def reachability(prog):
    """State = (paragraph index, end of active PERFORM range or None for sequential).
    Models PERFORM ranges, GO TO and fall-through; flags paragraphs never reached."""
    n = len(prog.paras)
    if not n:
        return
    seen, stack = set(), [(0, None)]
    while stack:
        i, end = stack.pop()
        if i >= n or (i, end) in seen:
            continue
        seen.add((i, end))
        p = prog.paras[i]
        p.reachable = True
        for pf in p.performs:
            stack.append(pf['range'])
        for g in p.gotos:
            j = prog.para_idx[g]
            stack.append((j, end if end is not None and j <= end else None))
        if p.terminal:
            continue
        if end is None and i + 1 < n:
            prog.fallthrough.add((i, i + 1))
            stack.append((i + 1, None))
        elif end is not None and i < end:
            prog.fallthrough.add((i, i + 1))
            stack.append((i + 1, end))


# =============================================================================
# 9. Chunk construction
# =============================================================================
def crud_str(c):
    return ''.join(sorted(c, key=CRUD_ORDER.index))


def describe(prog, name):
    if name in prog.conds:
        c = prog.conds[name]
        return f'{name}: 88-level on {c.parent.name} VALUE {c.value}'
    if name in prog.index_names:
        t = prog.index_names[name]
        return f'{name}: index-name for {t.name} (OCCURS {t.occurs_max})'
    if name in SPECIAL_REGS:
        return f'{name}: {SPECIAL_REGS[name]}'
    items = prog.dd.get(name)
    if not items:
        return None
    it = items[0]
    parts = [f'{name}: {it.level:02d}']
    if it.record is not it and it.record is not None:
        parts.append(f'in {it.record.name}')
    if it.member != prog.member:
        parts.append(f'[{it.member}]')
    parts.append(f'pos {it.offset + 1} len {it.length}' + (' (1st occurrence)' if it.in_occurs() else ''))
    if it.children:
        parts.append('group')
    else:
        if it.pic:
            parts.append(f'PIC {it.pic}')
        if it.usage != 'DISPLAY':
            parts.append(it.usage)
    if it.occurs_max:
        o = f'OCCURS {it.occurs_max}'
        if it.depending_on:
            o += f' DEPENDING ON {it.depending_on}'
        if it.keys:
            o += f" KEY {' '.join(it.keys)}"
        if it.indexes:
            o += f" INDEXED BY {' '.join(it.indexes)}"
        parts.append(o)
    if it.redefines:
        parts.append(f'REDEFINES {it.redefines}')
    if it.value:
        parts.append(f'VALUE {it.value[:40]}')
    if it.conditions:
        parts.append('88s ' + ', '.join(f'{c.name}={c.value}' for c in it.conditions))
    if it.record is not None and it.record.name in prog.record_fd:
        parts.append(f'record of FD {prog.record_fd[it.record.name]}')
    if len(items) > 1:
        parts.append(f'(name defined {len(items)} times - qualify)')
    return ' '.join(parts)


def para_header(prog, p, part=None):
    h = [f"PROGRAM {prog.id} [{', '.join(prog.tags)}] | {p.kind.upper()} {p.name}"
         + (f' | SECTION {p.section}' if p.section and p.kind != 'section' else '')
         + (f' | PART {part[0]}/{part[1]}' if part and part[1] > 1 else '')]
    if p.lines:
        a, b = p.lines[0], p.lines[-1]
        src = f'{a.member} line {a.lineno}' + (f'-{b.lineno}' if b.member == a.member else f' .. {b.member} line {b.lineno}')
        h.append(f'SOURCE: {src}')
    if p.idx == 0:
        h.append('INVOKED BY: program entry point')
    elif p.callers:
        h.append('INVOKED BY: ' + '; '.join(p.callers))
    if not p.reachable:
        h.append('STATUS: UNREACHABLE - no PERFORM, GO TO or fall-through path from entry (dead-code candidate)')
    if p.performs:
        pf = dict.fromkeys(f"{x['target']}" + (f" THRU {x['thru']}" if x['thru'] else '')
                           + (f" {x['loop']}" if x.get('loop') else '')
                           + (' (conditional)' if x['conditional'] else '') for x in p.performs)
        h.append('PERFORMS: ' + ', '.join(pf))
    if p.gotos:
        h.append('GO TO: ' + ', '.join(p.gotos))
    if p.calls:
        cs = []
        for c in {c['name'] + c.get('via', ''): c for c in p.calls}.values():
            s = c['name']
            if c.get('var'):
                s += f" (dynamic via {c['var']}" + ('' if c.get('stable') else ', MODIFIED at runtime') + ')'
            if c['name'] in KNOWN_CALLS:
                s += f" - {KNOWN_CALLS[c['name']]}"
            if c.get('via', '').startswith(('LINK', 'XCTL')):
                s += f" (CICS {c['via']})"
            cs.append(s)
        h.append('CALLS: ' + ', '.join(cs))
    if p.sql:
        h.append('SQL: ' + '; '.join(
            (s['op'] or '?') + (f" {s['cursor']}" if s['cursor'] else '')
            + (' ' + ','.join(sorted(s['tables'])) if s['tables'] else '') for s in p.sql))
    if p.file_ops:
        h.append('FILE I/O: ' + '; '.join(
            f"{f['verb']}{' ' + f['mode'] if f.get('mode') else ''} {prog.resource_label('FILE:' + f['file'])}"
            for f in p.file_ops))
    if p.cics:
        h.append('CICS: ' + '; '.join(c['command'] + (f" {c['resource'].split(':', 1)[1]}" if c['resource'] else '')
                                      + (f" PROGRAM {c['program']}" if c['program'] else '') for c in p.cics))
    if p.dli:
        h.append('DL/I: ' + '; '.join(f"{d['function']} PCB {d['pcb']}"
                                      + (f" SEG {','.join(d['segments'])}" if d['segments'] else '') for d in p.dli))
    if p.crud:
        h.append('CRUD: ' + ' '.join(f'{prog.resource_label(r)}={crud_str(c)}' for r, c in sorted(p.crud.items())))
    if p.writes:
        h.append('MODIFIES: ' + ', '.join(p.writes))
    if p.specials:
        h.append('CHECKS: ' + ', '.join(p.specials))
    if p.terminal:
        h.append(f'ENDS WITH: {p.terminal} (no fall-through)')
    if p.alter:
        h.append('WARNING: ALTER statement - control flow is runtime-modified')
    if p.unresolved:
        h.append('UNRESOLVED: ' + ', '.join(p.unresolved))
    gloss = [d for d in (describe(prog, n) for n in list(dict.fromkeys(p.refs + p.specials))[:40]) if d]
    if gloss:
        h.append('FIELD DEFINITIONS:')
        h.extend('  ' + g for g in gloss)
    return '\n'.join(h)


def split_lines(lines, budget, overlap):
    texts = [l.text for l in lines]
    if sum(len(t) + 1 for t in texts) <= budget:
        return [lines]
    parts, cur, size = [], [], 0
    for ln in lines:
        starts = re.match(r'\s*([A-Z][A-Z0-9-]*)', upper_code(ln.text))
        boundary = starts and (starts.group(1) in VERBS or starts.group(1) == 'EXEC')
        if cur and size + len(ln.text) + 1 > budget and (boundary or size > budget * 1.5):
            parts.append(cur)
            cur = cur[-overlap:] if overlap else []
            size = sum(len(x.text) + 1 for x in cur)
        cur.append(ln)
        size += len(ln.text) + 1
    if cur:
        parts.append(cur)
    return parts


def numbered(lines):
    return '\n'.join(f'{l.lineno:06d}{render(l)}' + (f'   <{l.member}>' if i == 0 or l.member != lines[i - 1].member else '')
                     for i, l in enumerate(lines))


def est_tokens(s):
    return math.ceil(len(s) / 4)


def layout_rows(rec):
    rows = []

    def walk(it, depth):
        rows.append((depth, it))
        for ch in it.children:
            walk(ch, depth + 1)
    walk(rec, 0)
    return rows


def item_notes(it):
    notes = []
    if it.redefines:
        notes.append(f'REDEFINES {it.redefines}')
    if it.occurs_max:
        o = f"OCCURS {f'{it.occurs_min} TO ' if it.depending_on else ''}{it.occurs_max}"
        if it.depending_on:
            o += f' DEPENDING ON {it.depending_on}'
        if it.keys:
            o += f" KEY {' '.join(it.keys)}"
        if it.indexes:
            o += f" INDEXED BY {' '.join(it.indexes)}"
        notes.append(o)
    if it.value and not it.children:
        notes.append(f'VALUE {it.value[:30]}')
    for c in it.conditions:
        notes.append(f'88 {c.name}={c.value}')
    if it.sign_separate:
        notes.append(f"SIGN {'LEADING' if it.sign_leading else 'TRAILING'} SEPARATE")
    return '; '.join(notes)


def layout_text(rec):
    out = [f"{'LVL':<4}{'FIELD':<34}{'POS':>5}{'LEN':>6}  {'PIC / USAGE':<24}NOTES"]
    for depth, it in layout_rows(rec):
        pu = (f'PIC {it.pic}' if it.pic else 'group' if it.children else '') + \
             (f' {it.usage}' if it.usage != 'DISPLAY' and not it.children else '')
        out.append(f"{it.level:02d}  {('  ' * depth + it.name)[:33]:<34}{it.offset + 1:>5}{it.length:>6}  "
                   f"{pu:<24}{item_notes(it)}")
    return '\n'.join(out)


def build_chunks(progs, max_tokens, overlap):
    chunks = []
    shared = {}                              # copybook-scoped record layouts (dedup)
    for prog in progs:
        for p in prog.paras:
            hdr0 = para_header(prog, p)
            budget = max(800, max_tokens * 4 - len(hdr0) - 16)
            parts = split_lines(p.lines, budget, overlap)
            for k, part in enumerate(parts, 1):
                hdr = para_header(prog, p, (k, len(parts)))
                code = '\n'.join(render(l) for l in part)
                embed = f'{hdr}\nCODE:\n{code}'
                chunks.append({
                    'id': f'{prog.id}::PARA::{p.name}::{k}', 'kind': p.kind if p.kind != 'entry' else 'paragraph',
                    'program': prog.id, 'name': p.name, 'section': p.section, 'part': k, 'parts': len(parts),
                    'source': {'member': part[0].member, 'start_line': part[0].lineno,
                               'end_line': part[-1].lineno} if part else None,
                    'metadata': {
                        'reachable': p.reachable, 'terminal': p.terminal, 'invoked_by': p.callers,
                        'performs': [{k2: v for k2, v in x.items() if k2 != 'range'} for x in p.performs],
                        'go_to': p.gotos, 'calls': p.calls,
                        'sql': [{k2: (sorted(v) if isinstance(v, set) else v) for k2, v in s.items()
                                 if k2 != 'tables'} | {'tables': {t: crud_str(c) for t, c in s['tables'].items()}}
                                for s in p.sql],
                        'cics': p.cics, 'dli': p.dli, 'file_ops': p.file_ops,
                        'crud': {r: crud_str(c) for r, c in sorted(p.crud.items())},
                        'data_referenced': p.refs, 'data_modified': p.writes,
                        'special_registers': p.specials, 'unresolved': p.unresolved,
                    },
                    'embed_text': embed, 'code': code, 'code_numbered': numbered(part),
                    'token_estimate': est_tokens(embed),
                })
        chunks.append(program_summary_chunk(prog))
        # record layouts: one chunk group per copybook (deduplicated across programs)
        # and one per program section for inline definitions
        groups = defaultdict(list)
        for rec in prog.records:
            key = ('COPY', rec.member) if rec.member != prog.member else ('PGM', rec.section)
            groups[key].append(rec)
        for (kind, name), recs in groups.items():
            fds = []
            for rec in recs:
                fd = prog.record_fd.get(rec.name)
                if fd:
                    sel = prog.selects.get(fd)
                    fds.append(f"{prog.id} FD {fd}" + (f" DD {sel['dd']} {sel['vsam']}"
                               + (f" KEY {sel['record_key']}" if sel['record_key'] else '') if sel else ''))
            if kind == 'COPY':
                sig = (name, tuple((r.name, r.length) for r in recs))
                if sig in shared:
                    shared[sig]['used_by'].append(prog.id)
                    shared[sig]['fds'].extend(fds)
                else:
                    shared[sig] = {'member': name, 'recs': recs, 'used_by': [prog.id], 'fds': fds}
                continue
            hdr = f'PROGRAM {prog.id} | DATA {name} SECTION (inline definitions)'
            chunks.extend(layout_chunks(f'{prog.id}::DATA::{name}', hdr, recs, prog.id, prog.member,
                                        {'section': name, 'files': fds}, max_tokens))
    for sig, v in shared.items():
        hdr = f"COPYBOOK {v['member']} | used by {', '.join(v['used_by'])}" + \
              (f"\nFILES: {'; '.join(v['fds'])}" if v['fds'] else '')
        chunks.extend(layout_chunks(f"COPYBOOK::{v['member']}", hdr, v['recs'], None, v['member'],
                                    {'copybook': v['member'], 'used_by': v['used_by'], 'files': v['fds']},
                                    max_tokens))
    return chunks


def record_block(rec):
    head = f'RECORD {rec.name} | {rec.section} SECTION | length {rec.length}'
    if rec.comments:
        head += '\nNOTES: ' + ' / '.join(rec.comments)
    return f'{head}\n{layout_text(rec)}'


def layout_chunks(cid, hdr, recs, program, member, meta, max_tokens):
    budget = max(1200, max_tokens * 4 - len(hdr))
    parts, cur, size = [], [], 0
    for rec in recs:
        blk = record_block(rec)
        if cur and size + len(blk) > budget:
            parts.append(cur)
            cur, size = [], 0
        cur.append((rec, blk))
        size += len(blk) + 1
    if cur:
        parts.append(cur)
    out = []
    for k, part in enumerate(parts, 1):
        h = hdr + (f' | PART {k}/{len(parts)}' if len(parts) > 1 else '')
        body = '\n\n'.join(b for _, b in part)
        text = f'{h}\nLAYOUTS:\n{body}'
        out.append({'id': f'{cid}::{k}', 'kind': 'record_layout', 'program': program,
                    'name': ', '.join(r.name for r, _ in part), 'part': k, 'parts': len(parts),
                    'source': {'member': member, 'start_line': part[0][0].lineno},
                    'metadata': meta | {'records': {r.name: r.length for r, _ in part}},
                    'embed_text': text, 'code': body, 'token_estimate': est_tokens(text)})
    return out


def call_tree(prog, max_depth=10):
    out, expanded = [], set()

    def tags(i, e):
        t, seen = [], set()
        for k in range(i, e + 1):
            q = prog.paras[k]
            for r, c in sorted(q.crud.items()):
                s = f'{crud_str(c)} {prog.resource_label(r).split(" (")[0]}'
                if s not in seen:
                    seen.add(s)
                    t.append(s)
            for c in q.calls:
                s = f"CALL {c['name']}"
                if s not in seen:
                    seen.add(s)
                    t.append(s)
        return f"  [{'; '.join(t)}]" if t else ''

    def visit(i, e, depth, label):
        p = prog.paras[i]
        rng = f' THRU {prog.paras[e].name}' if e != i else ''
        line = '  ' * depth + f'{label}{p.name}{rng}{tags(i, e)}'
        if (i, e) in expanded:
            out.append(line + '  (expanded above)')
            return
        out.append(line)
        expanded.add((i, e))
        if depth >= max_depth:
            return
        kids = []
        for k in range(i, e + 1):
            for pf in prog.paras[k].performs:
                if pf['range'] not in kids:
                    kids.append(pf['range'])
        for s, ee in kids:
            visit(s, ee, depth + 1, 'PERFORM ')
    if prog.paras:
        visit(0, 0, 0, '')
    return '\n'.join(out)


def program_summary_chunk(prog):
    tables = defaultdict(set)
    files = defaultdict(set)
    calls = {}
    for p in prog.paras:
        for r, c in p.crud.items():
            (tables if r.startswith('DB2:') else files)[r] |= c
        for c in p.calls:
            calls[c['name']] = c
    dead = [p.name for p in prog.paras if not p.reachable]
    lines = [f"PROGRAM {prog.id} [{', '.join(prog.tags)}] | PROGRAM SUMMARY",
             f"SOURCE: {prog.path.name} | paragraphs {len(prog.paras)} | entry {prog.paras[0].name if prog.paras else '-'}"]
    if prog.header_comments:
        lines.append('DESCRIPTION: ' + ' / '.join(prog.header_comments))
    if prog.using:
        lines.append('PROCEDURE DIVISION USING: ' + ', '.join(prog.using))
    if prog.copybooks:
        lines.append('COPYBOOKS: ' + ', '.join(f"{c['member']}{'' if c['resolved'] else ' (UNRESOLVED)'}"
                                               for c in prog.copybooks))
    for name, s in prog.selects.items():
        lines.append(f"FILE {name}: DD {s['dd']} {s['vsam']} ORG {s['org']} ACCESS {s['access']}"
                     + (f" KEY {s['record_key']}" if s['record_key'] else '')
                     + (f" STATUS {s['status']}" if s['status'] else ''))
    for cname, c in prog.cursors.items():
        lines.append(f"CURSOR {cname}: {', '.join(sorted(c['tables']))}" + (' WITH HOLD' if c['with_hold'] else ''))
    if tables:
        lines.append('DB2 CRUD: ' + ' '.join(f'{r[4:]}={crud_str(c)}' for r, c in sorted(tables.items())))
    if files:
        lines.append('FILE/IMS/CICS CRUD: ' + ' '.join(f'{prog.resource_label(r)}={crud_str(c)}'
                                                      for r, c in sorted(files.items())))
    if calls:
        lines.append('CALLS: ' + ', '.join(f"{n}" + (f" ({KNOWN_CALLS[n]})" if n in KNOWN_CALLS else '')
                                           for n in calls))
    if dead:
        lines.append('DEAD-CODE CANDIDATES: ' + ', '.join(dead))
    lines.append('PERFORM HIERARCHY:')
    lines.append(call_tree(prog))
    text = '\n'.join(lines)
    return {'id': f'{prog.id}::PROGRAM', 'kind': 'program_summary', 'program': prog.id, 'name': prog.id,
            'source': {'member': prog.path.name, 'start_line': 1},
            'metadata': {'tags': prog.tags, 'dead_code': dead, 'copybooks': prog.copybooks,
                         'files': prog.selects, 'tables': {r[4:]: crud_str(c) for r, c in tables.items()}},
            'embed_text': text, 'code': '', 'token_estimate': est_tokens(text)}


# =============================================================================
# 10. Artifacts: Mermaid, layouts, DFSORT SYMNAMES, CRUD matrix, report
# =============================================================================
def mid(prefix, name):
    return prefix + re.sub(r'[^A-Za-z0-9]', '_', name)


def flow_mmd(prog):
    out = ['flowchart TD']
    _append = out.append

    def add(line):
        if line not in out:
            _append(line)
    for p in prog.paras:
        shape = f'{mid("P_", p.name)}(["{p.name}"])' if p.idx == 0 else f'{mid("P_", p.name)}["{p.name}"]'
        out.append(f'  {shape}')
    ext = set()
    for p in prog.paras:
        a = mid('P_', p.name)
        for pf in p.performs:
            lbl = f"THRU {pf['thru']}" if pf['thru'] else 'PERFORM'
            if pf.get('via'):
                lbl = f"{pf['via']} PROCEDURE"
            add(f'  {a} -->|{lbl}| {mid("P_", pf["target"] if pf["target"] in prog.para_idx else prog.paras[pf["range"][0]].name)}')
        for g in dict.fromkeys(p.gotos):
            out.append(f'  {a} -.->|GO TO| {mid("P_", g)}')
        for c in p.calls:
            x = mid('X_', c['name'])
            if x not in ext:
                ext.add(x)
                out.append(f'  {x}[["CALL {c["name"]}"]]')
            add(f'  {a} ==> {x}')
    for i, j in sorted(prog.fallthrough):
        if not any(pf['range'][0] == j for q in prog.paras for pf in q.performs if q.idx == i):
            out.append(f'  {mid("P_", prog.paras[i].name)} -. falls into .-> {mid("P_", prog.paras[j].name)}')
    out.append('  classDef dead fill:#f2f2f2,stroke:#999,stroke-dasharray:4 3,color:#888')
    out.append('  classDef ext fill:#fff7e6,stroke:#d48806')
    dead = [mid('P_', p.name) for p in prog.paras if not p.reachable]
    if dead:
        out.append(f"  class {','.join(dead)} dead")
    if ext:
        out.append(f"  class {','.join(sorted(ext))} ext")
    return '\n'.join(out) + '\n'


def resources_mmd(prog):
    out = ['flowchart LR', f'  subgraph {mid("G_", prog.id)}["{prog.id}"]']
    used = [p for p in prog.paras if p.crud]
    for p in used:
        out.append(f'    {mid("P_", p.name)}["{p.name}"]')
    out.append('  end')
    nodes = {}
    for p in used:
        for r, c in sorted(p.crud.items()):
            kind, _, name = r.partition(':')
            rid = mid('R_', r)
            if rid not in nodes:
                label = prog.resource_label(r) if kind == 'FILE' else name
                nodes[rid] = (f'{rid}[("DB2 {label}")]' if kind == 'DB2' else
                              f'{rid}[/"{label}"/]' if kind == 'FILE' else
                              f'{rid}[/"CICS FILE {label}"/]' if kind == 'CICS-FILE' else
                              f'{rid}{{{{"{kind} {label}"}}}}')
            out.append(f'  {mid("P_", p.name)} -->|{crud_str(c)}| {rid}')
    out[3 + len(used):3 + len(used)] = [f'  {v}' for v in nodes.values()]
    return '\n'.join(out) + '\n'


def layout_md(prog):
    out = [f'# Record layouts - {prog.id}', '',
           'POS is 1-based (DFSORT/ICETOOL convention). LEN is per occurrence for OCCURS items.', '']
    for rec in prog.records:
        fd = prog.record_fd.get(rec.name)
        sel = prog.selects.get(fd) if fd else None
        out.append(f'## {rec.name}  (length {rec.length}, {rec.section} SECTION'
                   + (f", FD {fd} DD {sel['dd']} {sel['vsam']}" if sel else '')
                   + (f', copybook {rec.member}' if rec.member != prog.member else '') + ')')
        out.append('')
        out.append('| Lvl | Field | Pos | Len | PIC | Usage | Notes |')
        out.append('|---|---|---:|---:|---|---|---|')
        for depth, it in layout_rows(rec):
            out.append(f"| {it.level:02d} | {'&nbsp;' * 2 * depth}{it.name} | {it.offset + 1} | {it.length} | "
                       f"{it.pic or ('group' if it.children else '')} | "
                       f"{'' if it.children else it.usage} | {item_notes(it).replace('|', '/')} |")
        out.append('')
    return '\n'.join(out)


def sym_format(it):
    if it.children:
        return 'CH'
    u = it.usage
    if u == 'COMP-3':
        return 'PD'
    if u in ('COMP', 'COMP-5'):
        return 'FI' if it.signed else 'BI'
    if u in ('COMP-1', 'COMP-2'):
        return 'FL'
    if u in ('INDEX', 'POINTER', 'PROCEDURE-POINTER'):
        return 'BI'
    if it.category == 'numeric' and u == 'DISPLAY':
        if it.signed and it.sign_separate:
            return 'CSL' if it.sign_leading else 'CST'
        if it.signed and it.sign_leading:
            return 'CLO'
        return 'ZD'
    return 'CH'


def sym_constant(cond, parent):
    v = (cond.value or '').strip()
    if not v or ' THRU ' in f' {v} ' or ' THROUGH ' in f' {v} ':
        return None
    masked, lits = mask_literals(v)
    toks = masked.split()
    if len(toks) != 1:
        return None
    t = toks[0]
    if t.startswith('<L'):
        lit = unmask(t, lits)
        if lit[0] in 'Xx':
            return 'X' + lit[1:]
        return "C'" + strip_quotes(lit).replace("'", "''") + "'"
    if t in ('SPACE', 'SPACES'):
        return "C' '"
    if t in ('ZERO', 'ZEROS', 'ZEROES'):
        return "C'0'" if parent.category != 'numeric' else '0'
    if t in ('HIGH-VALUE', 'HIGH-VALUES'):
        return "X'" + 'FF' * max(1, parent.length) + "'"
    if t in ('LOW-VALUE', 'LOW-VALUES'):
        return "X'" + '00' * max(1, parent.length) + "'"
    if re.match(r'^[+-]?\d+$', t):
        return t
    return None


def symnames(prog, occurs_cap=20):
    out = [f'* DFSORT SYMNAMES generated by cobrag from {prog.id}',
           '* Positions are 1-based. OCCURS elements are expanded as NAME_n.']
    seen = set()

    def emit(name, line):
        if not re.match(r'^[A-Za-z#$@]', name):
            out.append(f'* SKIPPED (invalid symbol start): {line}')
        elif name in seen:
            out.append(f'* DUPLICATE: {line}')
        else:
            seen.add(name)
            out.append(line)

    def walk(it, shift, suffix):
        reps = range(min(it.occurs_max, occurs_cap)) if it.occurs_max else [None]
        for k in reps:
            sh = shift + (k * it.length if k is not None else 0)
            sf = suffix + (f'_{k + 1}' if k is not None else '')
            if it.name != 'FILLER':
                nm = (it.name + sf)[:50]
                emit(nm, f'{nm},{it.offset + 1 + sh},{it.length},{sym_format(it)}')
                if k in (None, 0):
                    for c in it.conditions:
                        const = sym_constant(c, it)
                        if const:
                            emit(c.name[:50], f'{c.name[:50]},{const}')
                        else:
                            out.append(f'* 88 {c.name} VALUE {c.value} (not expressible as one constant)')
            for ch in it.children:
                walk(ch, sh, sf)
        if it.occurs_max and it.occurs_max > occurs_cap:
            out.append(f'* {it.name}: OCCURS {it.occurs_max} truncated to {occurs_cap}')

    recs = [r for r in prog.records if r.section == 'FILE' or r.member != prog.member]
    if not recs:
        return None
    out.append('* Scope: FILE SECTION records and copybook-sourced records. Each record starts at 1.')
    for rec in recs:
        out.append(f'* ---- {rec.name}  LRECL {rec.length}' + (f'  FD {prog.record_fd[rec.name]}' if rec.name in prog.record_fd else ''))
        walk(rec, 0, '')
    return '\n'.join(out) + '\n'


def crud_md(progs):
    out = ['# CRUD matrices', '', 'C=create (INSERT/WRITE/ISRT) R=read (SELECT/FETCH/READ/GU/GN) '
           'U=update (UPDATE/REWRITE/REPL) D=delete (DELETE/DLET)', '']
    prog_level = defaultdict(dict)
    for prog in progs:
        res = sorted({r for p in prog.paras for r in p.crud})
        if not res:
            continue
        out.append(f'## {prog.id}')
        out.append('')
        out.append('| Paragraph | ' + ' | '.join(prog.resource_label(r) for r in res) + ' |')
        out.append('|---|' + '---|' * len(res))
        for p in prog.paras:
            if p.crud:
                out.append(f'| {p.name}{"" if p.reachable else " (dead)"} | '
                           + ' | '.join(crud_str(p.crud.get(r, set())) for r in res) + ' |')
                for r, c in p.crud.items():
                    key = r if not r.startswith('FILE:') else f"FILE:{prog.selects[r[5:]]['dd']}"
                    prog_level[key][prog.id] = crud_str(set(prog_level[key].get(prog.id, '')) | c)
        out.append('')
    if prog_level:
        pids = [p.id for p in progs]
        out.append('## Program level (files keyed by DD name)')
        out.append('')
        out.append('| Resource | ' + ' | '.join(pids) + ' |')
        out.append('|---|' + '---|' * len(pids))
        for r in sorted(prog_level):
            out.append(f'| {r} | ' + ' | '.join(prog_level[r].get(pid, '') for pid in pids) + ' |')
        out.append('')
    return '\n'.join(out)


def report_md(progs, chunks):
    out = ['# cobrag analysis report', '']
    out.append('## Inventory')
    out.append('')
    out.append('| Program | Type | Paragraphs | Source lines (expanded) | Copybooks | Calls | Dead-code candidates |')
    out.append('|---|---|---:|---:|---|---|---|')
    for prog in progs:
        calls = sorted({c['name'] for p in prog.paras for c in p.calls})
        dead = [p.name for p in prog.paras if not p.reachable]
        out.append(f"| {prog.id} | {', '.join(prog.tags)} | {len(prog.paras)} | {len(prog.lines)} | "
                   f"{', '.join(dict.fromkeys(c['member'] for c in prog.copybooks)) or '-'} | {', '.join(calls) or '-'} | "
                   f"{', '.join(dead) or '-'} |")
    out.append('')
    xref = defaultdict(list)
    for prog in progs:
        for c in prog.copybooks:
            xref[c['member']].append(prog.id + ('' if c['resolved'] else ' (unresolved)'))
    if xref:
        out.append('## Copybook cross-reference (change impact)')
        out.append('')
        out.append('| Copybook | Used by |')
        out.append('|---|---|')
        for k in sorted(xref):
            out.append(f"| {k} | {', '.join(dict.fromkeys(xref[k]))} |")
        out.append('')
    out.append('## Findings')
    out.append('')
    for prog in progs:
        f = []
        for p in prog.paras:
            if not p.reachable:
                f.append(f'Dead-code candidate {p.name}: no PERFORM/GO TO/fall-through path from entry.')
            for u in p.unresolved:
                f.append(f'Unresolved reference in {p.name}: {u}.')
            if p.alter:
                f.append(f'ALTER in {p.name}: control flow modified at runtime.')
            for c in p.calls:
                if c.get('var'):
                    if c.get('resolved'):
                        f.append(f"Dynamic CALL via {c['var']} in {p.name} resolves to {c['resolved']} from VALUE clause"
                                 + (' and the field is never modified in this program.' if c.get('stable')
                                    else ' but the field IS modified - target is runtime-dependent.'))
                    else:
                        f.append(f"Dynamic CALL via {c['var']} in {p.name} has no VALUE - target unknown statically.")
            for s in p.sql:
                if s['op'] == 'EXECUTE IMMEDIATE':
                    f.append(f'Dynamic SQL in {p.name}: table access not statically known.')
        for c in prog.copybooks:
            if not c['resolved']:
                f.append(f"Unresolved {c['kind']} {c['member']} at {c['from']} line {c['line']}.")
        gotos = sum(len(p.gotos) for p in prog.paras)
        if gotos:
            f.append(f'{gotos} GO TO statement(s) - convert to structured flow before automated translation.')
        for cn, c in prog.cursors.items():
            if c['with_hold']:
                f.append(f'Cursor {cn} is WITH HOLD - stays open across COMMIT; preserve semantics in target design.')
        items = [it for it in prog.items]
        packed = sum(1 for it in items if not it.children and it.usage == 'COMP-3')
        binary = sum(1 for it in items if not it.children and it.usage in ('COMP', 'COMP-5'))
        redef = [it.name for it in items if it.redefines]
        odo = [it.name for it in items if it.depending_on]
        search_all = [it.name for it in items if it.occurs_max and it.keys]
        if packed or binary:
            f.append(f'Migration encoding: {packed} packed-decimal and {binary} binary field(s) need '
                     f'type-aware conversion (not EBCDIC->ASCII byte translation).')
        if redef:
            f.append(f"REDEFINES ({', '.join(redef)}): target schema needs a discriminator or a single canonical view.")
        if odo:
            f.append(f"OCCURS DEPENDING ON ({', '.join(odo)}): variable-length record - map to array/child table.")
        if search_all:
            f.append(f"SEARCH ALL tables ({', '.join(search_all)}): ASCENDING KEY order is a load-time contract.")
        if f:
            out.append(f'### {prog.id}')
            out.append('')
            out.extend(f'- {x}' for x in f)
            out.append('')
    kinds = defaultdict(int)
    for c in chunks:
        kinds[c['kind']] += 1
    toks = [c['token_estimate'] for c in chunks]
    out.append('## Chunk statistics')
    out.append('')
    out.append(f"{len(chunks)} chunks ({', '.join(f'{k}={v}' for k, v in sorted(kinds.items()))}); "
               f"token estimate min {min(toks)} / max {max(toks)} / total {sum(toks)}.")
    out.append('')
    return '\n'.join(out)


# =============================================================================
# 11. BM25 retrieval (offline smoke test of the chunk set)
# =============================================================================
def stem(w):
    if len(w) > 5 and w.endswith('ing'):
        return w[:-3]
    if len(w) > 4 and w.endswith('ed'):
        return w[:-2]
    if len(w) > 3 and w.endswith('s') and not w.endswith('ss'):
        return w[:-1]
    return w


def ir_tokens(text):
    """Lowercase, keep hyphenated COBOL names whole AND split, light suffix stemming."""
    out = []
    for w in re.findall(r'[a-z0-9][a-z0-9_-]*', text.lower()):
        out.append(w)
        if '-' in w or '_' in w:
            out.extend(stem(x) for x in re.split(r'[-_]', w) if x and not x.isdigit())
        else:
            out[-1] = stem(w)
    return out


def bm25_search(chunks, query, k=5, kinds=None, k1=1.2, b=0.75):
    if kinds:
        chunks = [c for c in chunks if c['kind'] in kinds]
    docs = [ir_tokens(c['embed_text']) for c in chunks]
    N = len(docs)
    avg = sum(map(len, docs)) / max(1, N)
    df = defaultdict(int)
    for d in docs:
        for t in set(d):
            df[t] += 1
    q = ir_tokens(query)
    scores = []
    for c, d in zip(chunks, docs):
        tf = defaultdict(int)
        for t in d:
            tf[t] += 1
        s = 0.0
        for t in q:
            if t not in tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / avg))
        scores.append((s, c))
    scores.sort(key=lambda x: -x[0])
    return [x for x in scores[:k] if x[0] > 0]


# =============================================================================
# 12. CLI
# =============================================================================
SRC_EXTS = {'.cbl', '.cob', '.cobol', '.CBL', '.COB'}


def collect_sources(paths):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out.extend(sorted(x for x in p.iterdir() if x.suffix in SRC_EXTS))
        elif p.is_file():
            out.append(p)
    return out


def cmd_analyze(a):
    resolver = CopyResolver(a.copy)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    progs = [parse_program(s, resolver) for s in collect_sources(a.sources)]
    if not progs:
        sys.exit('no COBOL sources found')
    chunks = build_chunks(progs, a.max_tokens, a.overlap)
    with open(outdir / 'chunks.jsonl', 'w', encoding='utf-8') as fh:
        for c in chunks:
            fh.write(json.dumps(c, default=lambda o: sorted(o) if isinstance(o, set) else str(o)) + '\n')
    for prog in progs:
        (outdir / f'{prog.id}.flow.mmd').write_text(flow_mmd(prog))
        (outdir / f'{prog.id}.resources.mmd').write_text(resources_mmd(prog))
        (outdir / f'{prog.id}.layout.md').write_text(layout_md(prog))
        sym = symnames(prog)
        if sym:
            (outdir / f'{prog.id}.symnames').write_text(sym)
    (outdir / 'crud_matrix.md').write_text(crud_md(progs))
    (outdir / 'report.md').write_text(report_md(progs, chunks))
    for prog in progs:
        dead = [p.name for p in prog.paras if not p.reachable]
        print(f"{prog.id:<10} {', '.join(prog.tags):<22} paras={len(prog.paras):<3} records={len(prog.records):<3} "
              f"copybooks={len(prog.copybooks):<2} dead={','.join(dead) or '-'}")
    print(f'{len(chunks)} chunks -> {outdir / "chunks.jsonl"}')


def cmd_search(a):
    chunks = [json.loads(l) for l in open(a.chunks, encoding='utf-8')]
    for score, c in bm25_search(chunks, a.query, a.k, set(a.kind) if a.kind else None):
        print(f"{score:7.2f}  {c['id']}")
        if a.verbose:
            print('         ' + c['embed_text'].split('\n', 1)[0])


def main(argv=None):
    ap = argparse.ArgumentParser(description='COBOL semantic chunker / static analyzer for RAG')
    sub = ap.add_subparsers(dest='cmd', required=True)
    an = sub.add_parser('analyze')
    an.add_argument('sources', nargs='+', help='COBOL source files or directories')
    an.add_argument('-I', '--copy', action='append', default=[], help='copybook directory (repeatable)')
    an.add_argument('-o', '--out', default='out')
    an.add_argument('--max-tokens', type=int, default=900, help='approx tokens per paragraph chunk')
    an.add_argument('--overlap', type=int, default=3, help='lines repeated between split parts')
    an.set_defaults(func=cmd_analyze)
    se = sub.add_parser('search')
    se.add_argument('query')
    se.add_argument('--chunks', default='out/chunks.jsonl')
    se.add_argument('-k', type=int, default=5)
    se.add_argument('-v', '--verbose', action='store_true')
    se.add_argument('--kind', action='append', help='restrict to chunk kind (repeatable): paragraph, record_layout, program_summary')
    se.set_defaults(func=cmd_search)
    a = ap.parse_args(argv)
    a.func(a)


if __name__ == '__main__':
    main()
