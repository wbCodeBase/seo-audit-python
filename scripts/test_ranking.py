#!/usr/bin/env python3
"""
Local smoke test for the citation-grounded ranking logic in api/ai_rank_run.py.
Calls the real Claude API with web search enabled and prints what actually
comes back — no Vercel dev server, no Redis required.

Setup:
  1. cp .env.example .env   and fill in ANTHROPIC_API_KEY (never paste it in chat)
  2. pip install -r requirements.txt
  3. python scripts/test_ranking.py [domain] [brand] [query1] [query2] ...

With no args, runs one built-in example query.

This file lives outside api/ on purpose — Vercel's build (vercel.json) treats
every *.py directly under api/ as a deployable function, so a dev script
placed there would either break the build or become a public endpoint.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))


def _load_env_file(path=".env"):
    p = ROOT / path
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env_file()

from ai_rank_run import _rank_one, _worker_domain, _find_rank, _find_rank_by_brand  # noqa: E402


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("ANTHROPIC_API_KEY not set. Put it in a .env file (copy .env.example) — do not paste it in chat.")
        sys.exit(1)

    args = sys.argv[1:]
    domain = args[0] if len(args) > 0 else "example.com"
    brand = args[1] if len(args) > 1 else ""
    queries = args[2:] if len(args) > 2 else ["best project management software"]

    print(f"\n=== Domain profile: {domain} ===")
    bucket = {}
    _worker_domain(domain, api_key, bucket)
    profile = bucket.get("domain", {})
    if profile.get("_error"):
        print("ERROR:", profile["_error"])
    else:
        print(json.dumps(profile.get("domain_knowledge", {}), indent=2)[:1500])

    for q in queries:
        print(f'\n=== Query: "{q}" ===')
        result = _rank_one(q, api_key)
        if result.get("_error"):
            print("ERROR:", result["_error"])
            continue

        print("grounded (actually searched the web):", result["grounded"])
        rankings = result["rankings"]
        if not rankings:
            print("No sources cited." if result["grounded"] else "Claude answered from memory, no search performed.")
        for item in rankings:
            print(f"  #{item['rank']}  {item['title']}  —  {item['domain']}  ({item['url']})")

        print("domain_rank:", _find_rank(rankings, domain))
        if brand:
            print("brand_rank:", _find_rank_by_brand(rankings, brand))

        print("--- answer text (first 800 chars) ---")
        print(result["answer"][:800])


if __name__ == "__main__":
    main()
