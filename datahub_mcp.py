#!/usr/bin/env python3
"""AQVC Hub read-only MCP server.

Exposes the AQVC Hub investor database (api.datahub.aqvc.com, ~35k records) to
Claude as READ-ONLY tools: search, fetch, count, duplicate-check. No write
tools exist by design (company rule: never push to the Hub).

Run locally (stdio, for Claude Code / Claude Desktop):
    uv run --with mcp python datahub_mcp.py
Run hosted (streamable HTTP on $PORT, for claude.ai custom connectors):
    DATAHUB_API_TOKEN=dh_... uv run --with mcp python datahub_mcp.py --http

Token: DATAHUB_API_TOKEN env var, else ~/.datahub_token, else the
datahub-investors/.datahub_token file next to this repo.
"""
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

BASE = "https://api.datahub.aqvc.com/api/v1"


def _token():
    t = os.environ.get("DATAHUB_API_TOKEN")
    if t:
        return t.strip()
    for p in (os.path.expanduser("~/.datahub_token"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".datahub_token")):
        if os.path.exists(p):
            return open(p).read().strip()
    sys.exit("No API token: set DATAHUB_API_TOKEN or create ~/.datahub_token "
             "(mint one at https://datahub.aqvc.com/settings/api-tokens)")


TOKEN = _token()
_last_req = 0.0


def _get(path):
    """GET with 60/min rate-limit spacing and 429 retry."""
    global _last_req
    wait = 1.05 - (time.time() - _last_req)
    if wait > 0:
        time.sleep(wait)
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN})
    for _ in range(3):
        _last_req = time.time()
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(int(e.headers.get("Retry-After", 15)) + 1)
                continue
            if e.code == 401:
                raise RuntimeError("401 Unauthorized — token missing/revoked; mint a new one "
                                   "at https://datahub.aqvc.com/settings/api-tokens")
            raise RuntimeError(f"Hub API HTTP {e.code} on {url}")
    raise RuntimeError("Hub API rate limit: retries exhausted (60 req/min per token)")


# byte-identical to dedup/MATCH_CONTRACT.md — do NOT "improve"
def norm_name(s):
    if not s: return ''
    s = unicodedata.normalize('NFKC', s)
    s = s.lower()
    s = re.sub(r'&', ' and ', s)
    s = re.sub(r'[^a-z0-9 ]', ' ', s)
    s = re.sub(r'\b(the|gmbh|ltd|llc|inc|sa|ag|bv|plc|lp|llp|co|company|capital|'
               r'ventures|partners|group|holdings|fund|invest|investments)\b', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def norm_domain(u):
    if not u: return ''
    u = u.strip().lower()
    u = re.sub(r'^https?://', '', u)
    u = re.sub(r'^www\.', '', u)
    return u.split('/')[0].split('?')[0].split('#')[0].strip()


def _slim(res):
    return {"id": res["id"],
            "name": (res.get("investor_name") or {}).get("value"),
            "website": (res.get("website") or {}).get("value")}


mcp = FastMCP(
    "aqvc-datahub",
    instructions="Read-only access to the AQVC Hub investor database (~35k investor records: "
                 "LPs, family offices, funds-of-funds, pensions, etc.). Use check_duplicates "
                 "before proposing any new investor/LP — if it is OWNED, it is already in the "
                 "database. This server has NO write tools by design; records are never "
                 "created or modified through it.")


@mcp.tool()
def search_investors(query: str, page: int = 1) -> str:
    """Search the AQVC Hub investor database by name or website (case-insensitive
    substring match). Returns total match count and up to 25 results per page,
    each with id, name and website."""
    d = _get(f"/investors?search={urllib.parse.quote(query)}&page={page}")
    return json.dumps({"total_matches": d.get("count", 0),
                       "page": page,
                       "has_more": bool(d.get("next")),
                       "results": [_slim(r) for r in d.get("results", [])]},
                      ensure_ascii=False)


@mcp.tool()
def get_investor(investor_id: str) -> str:
    """Fetch one investor's full Hub record by UUID (name, type, website,
    locations, primary-contact email, etc.)."""
    return json.dumps(_get(f"/investors/{investor_id}"), ensure_ascii=False)


@mcp.tool()
def count_investors() -> str:
    """Total number of investors currently in the AQVC Hub database."""
    return json.dumps({"total_investors": _get("/investors?page=1").get("count", 0)})


@mcp.tool()
def check_duplicates(candidates: str) -> str:
    """Duplicate-check candidate investors/LPs against the live Hub database.
    Input: one candidate per line, formatted 'Name' or 'Name | website'.
    Output per candidate: OWNED (already in Hub, with the matching record) or
    NET-NEW. Matching is normalized (case/punctuation/legal-suffix insensitive,
    domain-based when a website is given). Max 25 candidates per call."""
    lines = [l.strip() for l in candidates.splitlines() if l.strip()]
    if not lines:
        return json.dumps({"error": "no candidates given"})
    if len(lines) > 25:
        return json.dumps({"error": f"{len(lines)} candidates — max 25 per call, split it up"})
    out = []
    for line in lines:
        name, _, site = (p.strip() for p in line.partition("|"))
        nn, dom = norm_name(name), norm_domain(site)
        hit = None
        for q in filter(None, [name, dom]):
            d = _get("/investors?search=" + urllib.parse.quote(q))
            for res in d.get("results", []):
                hname = (res.get("investor_name") or {}).get("value") or ""
                hsite = (res.get("website") or {}).get("value") or ""
                if (nn and norm_name(hname) == nn) or (dom and norm_domain(hsite) == dom):
                    hit = _slim(res)
                    break
            if hit:
                break
        out.append({"candidate": name, "status": "OWNED" if hit else "NET-NEW",
                    **({"hub_match": hit} if hit else {})})
    owned = sum(1 for o in out if o["status"] == "OWNED")
    return json.dumps({"checked": len(out), "owned": owned, "net_new": len(out) - owned,
                       "results": out}, ensure_ascii=False)


if __name__ == "__main__":
    if "--http" in sys.argv:
        from mcp.server.transport_security import TransportSecuritySettings
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = int(os.environ.get("PORT", 8000))
        # ponytail: DNS-rebinding protection guards localhost servers from browser
        # attacks; off here because this is a hosted public server reached over a
        # proxied Host (Render/custom domain) — the guard only ever 421s valid hosts.
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()
