"""Restore the complete live archive before building a new Pages deployment."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request

MAX_FILE_BYTES = 25 * 1024 * 1024
RACE_ID = re.compile(r"\d{4}-\d{2}")

def reject_constant(value):
    raise ValueError(f"Non-finite JSON value: {value}")

def parse_json(raw):
    return json.loads(raw, parse_constant=reject_constant)

def validate_index(index):
    if not isinstance(index, dict) or index.get("schema_version") != 1 or not isinstance(index.get("races"), list):
        raise ValueError("Invalid race index")
    ids = set()
    for race in index["races"]:
        if not isinstance(race, dict) or not RACE_ID.fullmatch(str(race.get("id", ""))):
            raise ValueError("Invalid race identifier")
        if race["id"] in ids:
            raise ValueError("Duplicate race identifier")
        ids.add(race["id"])
        if race.get("state") not in {"ready", "processing", "scheduled"}:
            raise ValueError("Unknown race state")
        if race["id"] != f'{int(race["year"])}-{int(race["round"]):02d}':
            raise ValueError("Race identifier does not match year and round")
        if race["state"] == "ready" and race.get("replay_url") != f'races/{race["id"]}.json':
            raise ValueError("Unexpected replay path")

def validate_race(data, race_id):
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("id") != race_id or data.get("state") != "ready":
        raise ValueError("Invalid or mismatched replay")
    if len(data.get("drivers", [])) < 5 or len(data.get("results", [])) < 5 or len(data.get("track", [])) < 3:
        raise ValueError("Incomplete replay")
    if data.get("total_laps", 0) < 1 or data.get("duration", 0) <= 0:
        raise ValueError("Invalid race duration or lap count")

def validate_base(base):
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("PUBLIC_BASE_URL must be a public HTTPS URL without credentials, query or fragment")
    return base.rstrip("/")

def fetch_json(url):
    # A browser-compatible UA avoids the error 1010 observed with urllib's default UA.
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read(MAX_FILE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Archive download returned HTTP {error.code}; existing deployment was not changed") from None
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError("Archive file exceeds the Cloudflare Pages file limit")
    return raw, parse_json(raw)

def restore(base, output, fetch=fetch_json):
    base = validate_base(base)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    raw, index = fetch(base + "/index.json")
    validate_index(index)
    # Validate and stage ALL published replays before replacing any local file.
    # GitHub cache eviction or a fresh runner must never truncate the live archive.
    with tempfile.TemporaryDirectory(prefix="bh-archive-", dir=output.parent) as folder:
        stage = Path(folder)
        for race in index["races"]:
            if race["state"] != "ready":
                continue
            relative = race["replay_url"]
            race_raw, data = fetch(base + "/" + relative)
            validate_race(data, race["id"])
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(race_raw)
        (stage / "index.json").write_bytes(raw)
        for source in (stage / "races").glob("*.json") if (stage / "races").exists() else []:
            destination = output / "races" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
        (stage / "index.json").replace(output / "index.json")
    print(f'Restored {sum(r["state"] == "ready" for r in index["races"])} existing replays')

def validate_directory(output):
    output = Path(output)
    files = list(output.rglob("*"))
    if sum(p.is_file() for p in files) > 20000:
        raise ValueError("Cloudflare Pages file count limit exceeded")
    for path in files:
        if path.is_file() and path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File exceeds 25 MiB: {path.relative_to(output)}")
    headers = (output / "_headers").read_text()
    if "Access-Control-Allow-Origin: *" not in headers:
        raise ValueError("Shopify CORS header is missing")
    if not (output / "404.html").is_file():
        raise ValueError("404.html is required to avoid HTML fallback for missing JSON")
    index = parse_json((output / "index.json").read_bytes())
    validate_index(index)
    for race in index["races"]:
        if race["state"] == "ready":
            validate_race(parse_json((output / race["replay_url"]).read_bytes()), race["id"])
    print("Deployment validated: replay files, file sizes and Shopify CORS")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--output", default="public")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        validate_directory(args.output)
    elif args.base:
        restore(args.base, args.output)
    else:
        parser.error("--base is required unless --validate-only is set")
