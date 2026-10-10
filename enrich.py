#!/usr/bin/env python3
"""
enrich.py - LLM enrichment pass over cobrag chunks.

For each paragraph chunk, Claude extracts business rules as strict JSON. The result
is grounded-checked (every cited line must exist in the chunk) and merged back:
  - metadata.llm      parsed JSON
  - embed_text        prefixed with PURPOSE / RULES so embeddings capture intent

Modes
  --dry-run                     write prompts/*.txt and batch_requests.jsonl only
  (default)                     call the Messages API sequentially (ANTHROPIC_API_KEY)
  --merge-batch RESULTS.jsonl   merge a Message Batches results file

Examples
  python3 enrich.py --chunks out/chunks.jsonl --dry-run
  ANTHROPIC_API_KEY=sk-... python3 enrich.py --chunks out/chunks.jsonl --limit 5
  python3 enrich.py --chunks out/chunks.jsonl --merge-batch results.jsonl

Standard library only (urllib). No SDK dependency.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API_URL = 'https://api.anthropic.com/v1/messages'
API_VERSION = '2023-06-01'
DEFAULT_MODEL = 'claude-sonnet-5-5'

SYSTEM = """You are a senior IBM z/OS modernization analyst. You read one COBOL paragraph \
together with statically derived context (callers, PERFORM targets, SQL, file I/O, CRUD, \
field definitions with PIC/USAGE/offsets) and extract the business logic it implements.

Rules:
- Use only the code and context provided. Do not invent field meanings, table semantics \
or upstream behaviour. If something depends on code not shown, say so in "assumptions".
- Every business rule must cite the 6-digit source line numbers it comes from.
- Describe behaviour in business terms first, technical terms second.
- Flag modernization risks: packed/binary arithmetic and rounding, REDEFINES, GO TO, \
WITH HOLD cursors, commit scope, file-status or SQLCODE handling gaps, dead code.
- Output a single JSON object matching the schema. No markdown, no prose outside JSON."""

SCHEMA = {
    "purpose": "1-2 sentences, business terms",
    "business_rules": [{"id": "BR1", "rule": "string", "condition": "string",
                        "action": "string", "source_lines": [123]}],
    "validations": ["string"],
    "error_handling": ["string"],
    "data_effects": [{"resource": "DB2:TABLE | FILE:NAME | ...", "operation": "C|R|U|D",
                      "description": "string"}],
    "migration_notes": ["string"],
    "assumptions": ["string"],
}


def build_user_prompt(chunk):
    header = chunk['embed_text'].split('\nCODE:\n', 1)[0]
    return (f"CONTEXT (static analysis):\n{header}\n\n"
            f"SOURCE (6-digit line number, column-7 indicator, columns 8-72):\n{chunk['code_numbered']}\n\n"
            f"Return JSON with exactly this shape:\n{json.dumps(SCHEMA, indent=2)}")


def safe_id(cid):
    return re.sub(r'[^A-Za-z0-9_-]', '_', cid)[:64]


def parse_json(text):
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find('{'), text.rfind('}')
        if a >= 0 and b > a:
            return json.loads(text[a:b + 1])
        raise


def grounding(chunk, result):
    """Rules citing line numbers outside the chunk are marked ungrounded."""
    valid = {int(l[:6]) for l in chunk['code_numbered'].splitlines() if l[:6].isdigit()}
    bad = []
    for r in result.get('business_rules', []):
        lines = r.get('source_lines') or []
        r['grounded'] = bool(lines) and all(isinstance(x, int) and x in valid for x in lines)
        if not r['grounded']:
            bad.append(r.get('id', '?'))
    return bad


def merge(chunk, result):
    bad = grounding(chunk, result)
    chunk['metadata']['llm'] = result
    chunk['metadata']['llm_ungrounded_rules'] = bad
    rules = [f"- {r.get('rule')}" for r in result.get('business_rules', []) if r.get('grounded')]
    prefix = f"PURPOSE: {result.get('purpose', '')}\n" + ('BUSINESS RULES:\n' + '\n'.join(rules) + '\n' if rules else '')
    chunk['embed_text'] = prefix + chunk['embed_text']
    chunk['token_estimate'] = -(-len(chunk['embed_text']) // 4)


def call_claude(api_key, model, user, max_tokens, retries=5):
    body = json.dumps({'model': model, 'max_tokens': max_tokens, 'system': SYSTEM,
                       'messages': [{'role': 'user', 'content': user}]}).encode()
    for attempt in range(retries):
        req = urllib.request.Request(API_URL, data=body, method='POST', headers={
            'x-api-key': api_key, 'anthropic-version': API_VERSION, 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            return ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                wait = float(e.headers.get('retry-after') or 2 ** attempt)
                print(f'  HTTP {e.code}, retry in {wait:.0f}s', file=sys.stderr)
                time.sleep(wait)
                continue
            raise RuntimeError(f'HTTP {e.code}: {e.read().decode(errors="replace")[:500]}')
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f'network error: {e.reason}')
    raise RuntimeError('retries exhausted')


def main():
    ap = argparse.ArgumentParser(description='LLM business-rule enrichment for cobrag chunks')
    ap.add_argument('--chunks', required=True)
    ap.add_argument('--out', help='default: <chunks dir>/enriched_chunks.jsonl')
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--max-tokens', type=int, default=2000)
    ap.add_argument('--limit', type=int, default=0, help='max paragraph chunks to enrich (0 = all)')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--merge-batch', help='Message Batches results .jsonl to merge')
    a = ap.parse_args()

    src = Path(a.chunks)
    chunks = [json.loads(l) for l in src.open(encoding='utf-8')]
    targets = [c for c in chunks if c['kind'] in ('paragraph', 'section') and c.get('code_numbered')]
    if a.limit:
        targets = targets[:a.limit]
    out = Path(a.out) if a.out else src.with_name('enriched_chunks.jsonl')

    if a.dry_run:
        pdir = src.parent / 'prompts'
        pdir.mkdir(exist_ok=True)
        idmap = {}
        with open(src.parent / 'batch_requests.jsonl', 'w', encoding='utf-8') as bf:
            for c in targets:
                cid = safe_id(c['id'])
                idmap[cid] = c['id']
                user = build_user_prompt(c)
                (pdir / f'{cid}.txt').write_text(f'=== SYSTEM ===\n{SYSTEM}\n\n=== USER ===\n{user}\n')
                bf.write(json.dumps({'custom_id': cid, 'params': {
                    'model': a.model, 'max_tokens': a.max_tokens, 'system': SYSTEM,
                    'messages': [{'role': 'user', 'content': user}]}}) + '\n')
        (src.parent / 'batch_idmap.json').write_text(json.dumps(idmap, indent=1))
        print(f'{len(targets)} prompts -> {pdir}/ ; batch file -> {src.parent / "batch_requests.jsonl"}')
        return

    results = {}
    if a.merge_batch:
        idmap = json.loads((src.parent / 'batch_idmap.json').read_text())
        for line in open(a.merge_batch, encoding='utf-8'):
            r = json.loads(line)
            res = r.get('result', {})
            if res.get('type') != 'succeeded':
                print(f"  {r.get('custom_id')}: {res.get('type')}", file=sys.stderr)
                continue
            text = ''.join(b.get('text', '') for b in res['message'].get('content', []) if b.get('type') == 'text')
            results[idmap.get(r['custom_id'], r['custom_id'])] = text
    else:
        key = os.environ.get('ANTHROPIC_API_KEY')
        if not key:
            sys.exit('ANTHROPIC_API_KEY not set (use --dry-run to only build prompts)')
        for i, c in enumerate(targets, 1):
            print(f'[{i}/{len(targets)}] {c["id"]}', file=sys.stderr)
            results[c['id']] = call_claude(key, a.model, build_user_prompt(c), a.max_tokens)

    ok = fail = 0
    for c in chunks:
        if c['id'] in results:
            try:
                merge(c, parse_json(results[c['id']]))
                ok += 1
            except (json.JSONDecodeError, ValueError) as e:
                c['metadata']['llm_error'] = str(e)
                fail += 1
    with open(out, 'w', encoding='utf-8') as fh:
        for c in chunks:
            fh.write(json.dumps(c) + '\n')
    print(f'enriched {ok}, failed {fail} -> {out}')


if __name__ == '__main__':
    main()
