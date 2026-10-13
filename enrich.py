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
# argparse reads the command-line options; json reads and writes the chunk and API data.
import argparse
import json
# os reads the API key from the environment. re handles IDs and JSON code fences.
import os
import re
# sys handles error output and exit messages; time provides the retry delays.
import sys
import time
# urllib sends HTTP requests to the Messages API and identifies request failures.
import urllib.error
import urllib.request
# Path builds and accesses the input and output file paths.
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
    # The embedding text starts with the static analysis context. Send that
    # context, the numbered COBOL source, and the required JSON shape to Claude.
    header = chunk['embed_text'].split('\nCODE:\n', 1)[0]
    return (f"CONTEXT (static analysis):\n{header}\n\n"
            f"SOURCE (6-digit line number, column-7 indicator, columns 8-72):\n{chunk['code_numbered']}\n\n"
            f"Return JSON with exactly this shape:\n{json.dumps(SCHEMA, indent=2)}")


def safe_id(cid):
    # Batch IDs and prompt filenames use the same ID. Replace other characters
    # with underscores and keep the first 64 characters.
    return re.sub(r'[^A-Za-z0-9_-]', '_', cid)[:64]


def parse_json(text):
    # The model may wrap its JSON in a Markdown code fence. Remove that wrapper
    # before asking the JSON parser to read the response.
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # If extra text surrounds the object, try the text from the first
        # opening brace through the last closing brace. Parsing errors still
        # reach the caller if this text is not valid JSON.
        start_index, end_index = text.find('{'), text.rfind('}')
        if start_index >= 0 and end_index > start_index:
            return json.loads(text[start_index:end_index + 1])
        raise


def grounding(chunk, result):
    """Rules citing line numbers outside the chunk are marked ungrounded."""
    # Fixed-format source lines begin with six digits. These are the only line
    # numbers a rule may cite for this chunk.
    valid = {int(line[:6]) for line in chunk['code_numbered'].splitlines() if line[:6].isdigit()}
    bad = []
    for rule in result.get('business_rules', []):
        # A rule is grounded only when it cites at least one valid integer line.
        # Store that finding on the rule and collect the IDs that fail the check.
        lines = rule.get('source_lines') or []
        rule['grounded'] = bool(lines) and all(isinstance(source_line, int) and source_line in valid for source_line in lines)
        if not rule['grounded']:
            bad.append(rule.get('id', '?'))
    return bad


def merge(chunk, result):
    # Grounding adds a flag to each rule before the result goes into metadata.
    bad = grounding(chunk, result)
    chunk['metadata']['llm'] = result
    chunk['metadata']['llm_ungrounded_rules'] = bad
    # Prefix the text sent for embedding with the model's purpose and grounded
    # rules. The original embedding text remains after this new prefix.
    rules = [f"- {rule.get('rule')}" for rule in result.get('business_rules', []) if rule.get('grounded')]
    prefix = f"PURPOSE: {result.get('purpose', '')}\n" + ('BUSINESS RULES:\n' + '\n'.join(rules) + '\n' if rules else '')
    chunk['embed_text'] = prefix + chunk['embed_text']
    # The existing estimate rounds the character count up to groups of four.
    chunk['token_estimate'] = -(-len(chunk['embed_text']) // 4)


def call_claude(api_key, model, user, max_tokens, retries=5):
    # Encode the model name, system instructions, user prompt, and token limit
    # as JSON bytes for the existing Messages API request.
    body = json.dumps({'model': model, 'max_tokens': max_tokens, 'system': SYSTEM,
                       'messages': [{'role': 'user', 'content': user}]}).encode()
    for attempt in range(retries):
        # Build a new POST request for each attempt. The API key and version go
        # in HTTP headers; the JSON bytes above are the request body.
        req = urllib.request.Request(API_URL, data=body, method='POST', headers={
            'x-api-key': api_key, 'anthropic-version': API_VERSION, 'content-type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
            # The API returns content blocks. Keep the text from text blocks in
            # their original order and return it for JSON parsing by the caller.
            return ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
        except urllib.error.HTTPError as e:
            # Only the listed HTTP statuses retry, and only while attempts
            # remain. A Retry-After header takes precedence over backoff.
            if e.code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                wait = float(e.headers.get('retry-after') or 2 ** attempt)
                print(f'  HTTP {e.code}, retry in {wait:.0f}s', file=sys.stderr)
                time.sleep(wait)
                continue
            # Include up to 500 characters of the server's error response.
            raise RuntimeError(f'HTTP {e.code}: {e.read().decode(errors="replace")[:500]}')
        except urllib.error.URLError as e:
            # Network errors use the same number of attempts but exponential
            # delays. The last failure becomes an error for the caller.
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f'network error: {e.reason}')
    raise RuntimeError('retries exhausted')


def main():
    # Parse the paths and limits supplied by the user. The defaults and option
    # meanings are shared by dry-run, batch merge, and direct API modes.
    parser = argparse.ArgumentParser(description='LLM business-rule enrichment for cobrag chunks')
    parser.add_argument('--chunks', required=True)
    parser.add_argument('--out', help='default: <chunks dir>/enriched_chunks.jsonl')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    parser.add_argument('--max-tokens', type=int, default=2000)
    parser.add_argument('--limit', type=int, default=0, help='max paragraph chunks to enrich (0 = all)')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--merge-batch', help='Message Batches results .jsonl to merge')
    args = parser.parse_args()

    # Read one JSON chunk per line. Only numbered paragraph and section chunks
    # are sent for enrichment; every input chunk is kept for the final output.
    source_path = Path(args.chunks)
    chunks = [json.loads(line) for line in source_path.open(encoding='utf-8')]
    targets = [chunk for chunk in chunks if chunk['kind'] in ('paragraph', 'section') and chunk.get('code_numbered')]
    if args.limit:
        targets = targets[:args.limit]
    output_path = Path(args.out) if args.out else source_path.with_name('enriched_chunks.jsonl')

    if args.dry_run:
        # Write prompt files and a JSONL batch request file without calling the
        # API. The ID map links safe batch IDs back to original chunk IDs.
        prompts_directory = source_path.parent / 'prompts'
        prompts_directory.mkdir(exist_ok=True)
        id_map = {}
        with open(source_path.parent / 'batch_requests.jsonl', 'w', encoding='utf-8') as batch_file:
            for chunk in targets:
                safe_chunk_id = safe_id(chunk['id'])
                id_map[safe_chunk_id] = chunk['id']
                user_prompt = build_user_prompt(chunk)
                (prompts_directory / f'{safe_chunk_id}.txt').write_text(f'=== SYSTEM ===\n{SYSTEM}\n\n=== USER ===\n{user_prompt}\n')
                batch_file.write(json.dumps({'custom_id': safe_chunk_id, 'params': {
                    'model': args.model, 'max_tokens': args.max_tokens, 'system': SYSTEM,
                    'messages': [{'role': 'user', 'content': user_prompt}]}}) + '\n')
        (source_path.parent / 'batch_idmap.json').write_text(json.dumps(id_map, indent=1))
        print(f'{len(targets)} prompts -> {prompts_directory}/ ; batch file -> {source_path.parent / "batch_requests.jsonl"}')
        return

    responses_by_chunk_id = {}
    if args.merge_batch:
        # Batch result lines contain a custom ID and a Messages API result.
        # Look up the original chunk ID before storing the returned text.
        id_map = json.loads((source_path.parent / 'batch_idmap.json').read_text())
        for line in open(args.merge_batch, encoding='utf-8'):
            batch_result = json.loads(line)
            result_status = batch_result.get('result', {})
            if result_status.get('type') != 'succeeded':
                print(f"  {batch_result.get('custom_id')}: {result_status.get('type')}", file=sys.stderr)
                continue
            text = ''.join(block.get('text', '') for block in result_status['message'].get('content', []) if block.get('type') == 'text')
            responses_by_chunk_id[id_map.get(batch_result['custom_id'], batch_result['custom_id'])] = text
    else:
        # Direct mode reads the existing API key from the environment and sends
        # each selected chunk's prompt to the Messages API in sequence.
        api_key = os.environ.get('ANTHROPIC_API_KEY')
        if not api_key:
            sys.exit('ANTHROPIC_API_KEY not set (use --dry-run to only build prompts)')
        for index, chunk in enumerate(targets, 1):
            print(f'[{index}/{len(targets)}] {chunk["id"]}', file=sys.stderr)
            responses_by_chunk_id[chunk['id']] = call_claude(api_key, args.model, build_user_prompt(chunk), args.max_tokens)

    # Parse each available response and attach it to its original chunk. A
    # malformed JSON response is recorded in that chunk's metadata, while the
    # remaining chunks continue through the same output loop.
    successful_count = failed_count = 0
    for chunk in chunks:
        if chunk['id'] in responses_by_chunk_id:
            try:
                merge(chunk, parse_json(responses_by_chunk_id[chunk['id']]))
                successful_count += 1
            except (json.JSONDecodeError, ValueError) as error:
                chunk['metadata']['llm_error'] = str(error)
                failed_count += 1
    # Write one JSON object per line, including chunks that were not targeted.
    with open(output_path, 'w', encoding='utf-8') as output_file:
        for chunk in chunks:
            output_file.write(json.dumps(chunk) + '\n')
    print(f'enriched {successful_count}, failed {failed_count} -> {output_path}')


if __name__ == '__main__':
    main()
