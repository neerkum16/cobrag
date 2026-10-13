import json
import pathlib
import sys
import anthropic

RAW = pathlib.Path("analysis_raw.json")
OUT = pathlib.Path("analysis.md")
FILES = ["README.md", "cobrag.py", "enrich.py", "tests/test_cobrag.py"]


def show(data):
    text = "\n".join(
        b.get("text", "") for b in data["content"] if b.get("type") == "text"
    )
    OUT.write_text(text)
    print(text)
    u = data["usage"]
    print(f"\nStop reason: {data.get('stop_reason')}")
    print(f"Tokens: {u['input_tokens']} in, {u['output_tokens']} out")


# Re-display a previously saved response without calling the API
if "--saved" in sys.argv:
    show(json.loads(RAW.read_text()))
    sys.exit()

parts = []
for f in FILES:
    p = pathlib.Path(f)
    if p.exists():
        parts.append(f"=== {f} ===\n{p.read_text(errors='replace')}")

samples = pathlib.Path("samples")
if samples.exists():
    names = "\n".join(sorted(str(x) for x in samples.rglob("*") if x.is_file()))
    parts.append(f"=== samples/ file list ===\n{names}")

prompt = (
    "Analyse this repository. Provide:\n"
    "1. What it does and how the scripts relate to each other.\n"
    "2. Required dependencies and environment variables.\n"
    "3. Exact commands to set up and run it, in order.\n"
    "4. How to run the tests.\n"
    "5. Bugs, risks, or missing pieces.\n\n"
    + "\n\n".join(parts)
)

client = anthropic.Anthropic()
resp = client.messages.create(
    model="claude-sonnet-5-5",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},
    messages=[{"role": "user", "content": prompt}],
)

# Save the full raw response before any processing
RAW.write_text(resp.model_dump_json(indent=2))

show(json.loads(RAW.read_text()))
