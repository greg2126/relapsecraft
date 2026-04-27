#!/usr/bin/env python3

import json
import time
import os
import hashlib
import argparse
import urllib.request
import urllib.error
from pathlib import Path
from urllib.parse import urlparse, quote
from concurrent.futures import ThreadPoolExecutor, as_completed

# required-client	Players must install this to use the server feature properly	Required for players
# server-only	Installed on the server; players do not need it	Server-side only
# optional-client	Players can install it for extra/client-only features	Optional client mod
# admin-only	Server/admin utility; not relevant to normal players	Admin/server utility


API_BASE = "https://api.modrinth.com/v2"

USER_AGENT = os.getenv(
    "MODRINTH_USER_AGENT",
    "RelapsecraftMods/1.0 (contact: your-email@example.com)"
)

CACHE_FILE = ".modrinth_cache.json"

VALID_INSTALL_TAGS = {
    "required-client",
    "optional-client",
    "server-only",
    "admin-only",
    "hidden"
}

PROJECT_TYPES = {
    "mod", "modpack", "resourcepack", "shader", "plugin", "datapack"
}

# -------------------------
# Utilities
# -------------------------

def hash_input(mods):
    return hashlib.sha256(json.dumps(mods, sort_keys=True).encode()).hexdigest()

def load_cache():
    if not Path(CACHE_FILE).exists():
        return {}
    return json.loads(Path(CACHE_FILE).read_text())

def save_cache(cache):
    Path(CACHE_FILE).write_text(json.dumps(cache, indent=2))

def rate_limited_sleep(delay):
    time.sleep(delay)

# -------------------------
# Validation
# -------------------------

def validate_install_tag(tag, mod):
    if tag and tag not in VALID_INSTALL_TAGS:
        raise ValueError(
            f"Invalid install tag '{tag}' in mod {mod}. "
            f"Valid values: {', '.join(sorted(VALID_INSTALL_TAGS))}"
        )

# -------------------------
# URL parsing
# -------------------------

def extract_slug_or_id(value):
    if not value:
        return None

    value = str(value).strip()
    if not value:
        return None

    parsed = urlparse(value)

    if parsed.scheme and parsed.netloc:
        host = parsed.netloc.replace("www.", "")
        if host != "modrinth.com":
            return None

        parts = [p for p in parsed.path.split("/") if p]
        for i, part in enumerate(parts):
            if part in PROJECT_TYPES and i + 1 < len(parts):
                return parts[i + 1]

        return parts[-1] if parts else None

    return value.replace("@", "").strip()

# -------------------------
# API
# -------------------------

def fetch_json(url, retries=3):
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode())

        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                retry_after = int(e.headers.get("Retry-After", 10))
                print(f"Rate limited. Sleeping {retry_after}s")
                time.sleep(retry_after)
                continue
            raise

        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 + attempt)
                continue
            raise

# -------------------------
# Fetch project
# -------------------------

def fetch_project(slug_or_id):
    encoded = quote(slug_or_id)

    project = fetch_json(f"{API_BASE}/project/{encoded}")

    versions = fetch_json(
        f"{API_BASE}/project/{quote(project.get('slug') or slug_or_id)}/version?include_changelog=false"
    )

    project["latestVersion"] = versions[0] if versions else None
    return project

# -------------------------
# Main
# -------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="mods.json")
    parser.add_argument("--output", default="mods-data.json")
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--workers", type=int, default=4)

    args = parser.parse_args()

    config = json.loads(Path(args.input).read_text())

    raw_mods = config["mods"] if isinstance(config, dict) else config

    cache = load_cache()
    input_hash = hash_input(raw_mods)

    generated = []
    failures = []
    tasks = []

    seen = set()

    def process(item, index):
        if isinstance(item, dict):
            url = item.get("url")
            install = item.get("install")
            note = item.get("note")
        else:
            url = item
            install = None
            note = None

        validate_install_tag(install, url)

        slug = extract_slug_or_id(url)
        if not slug:
            return None

        if slug in seen:
            return None

        seen.add(slug)

        cache_entry = cache.get(slug)

        if cache_entry and cache_entry.get("input") == item:
            print(f"[{index}] Using cache for {slug}")
            return cache_entry["data"]

        print(f"[{index}] Fetching {slug} ({install})")

        project = fetch_project(slug)

        project["manualInstall"] = install
        project["manualNote"] = note

        cache[slug] = {
            "input": item,
            "data": project
        }

        rate_limited_sleep(args.delay)

        return project

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(process, item, i): item
            for i, item in enumerate(raw_mods, start=1)
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                if result:
                    generated.append(result)
            except Exception as e:
                failures.append(str(e))
                print(f"Failed: {e}")

    save_cache(cache)

    output = {
        "serverName": config.get("serverName", "Minecraft Server"),
        "description": config.get("description", ""),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mods": generated
    }

    if failures:
        output["failures"] = failures

    Path(args.output).write_text(json.dumps(output, indent=2))

    print(f"\nGenerated {args.output}")
    print(f"Mods: {len(generated)} | Failures: {len(failures)}")


if __name__ == "__main__":
    main()