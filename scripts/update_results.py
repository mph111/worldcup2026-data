#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
World Cup 2026 — auto-update SCORES into worldcup2026.json
Source: football-data.org (free tier, competition WC).

Hybrid mode: updates ONLY the match result (goals + status="finished").
It does NOT touch detailed stats (possession, shots, passes...) — you enter
those by hand — and does NOT recompute standings, because your app computes
standings/positions automatically from the results.

Usage:
    FOOTBALL_DATA_TOKEN=xxxx python scripts/update_results.py

Optional env:
    JSON_PATH   path to the JSON file (default: worldcup2026.json)
    FORCE       "1" to re-write every finished match (e.g. fix a wrong score)
"""

import os
import sys
import json
import urllib.request
import urllib.parse

API = "https://api.football-data.org/v4/competitions/WC/matches"
DONE = {"FINISHED", "AWARDED"}

# football-data.org uses a 3-letter code (tla) per team. If any code differs
# from the code used in your JSON, map it here:  "FD_TLA": "YOUR_CODE"
CODE_OVERRIDES = {
    # "KOR": "KOR",
    # "CZE": "CZE",
}


def api_get_matches():
    token = os.environ.get("FOOTBALL_DATA_TOKEN")
    if not token:
        sys.exit("ERROR: set FOOTBALL_DATA_TOKEN environment variable.")
    url = API + "?" + urllib.parse.urlencode({"status": "FINISHED"})
    req = urllib.request.Request(url, headers={"X-Auth-Token": token})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("matches", [])


def main():
    json_path = os.environ.get("JSON_PATH", "worldcup2026.json")
    force = os.environ.get("FORCE") == "1"

    with open(json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    valid_codes = {t["code"] for g in doc["groups"].values() for t in g}

    # index schedule matches by unordered code pair
    by_pair = {}
    for m in doc["schedule"]:
        by_pair[frozenset((m["t1"], m["t2"]))] = m

    def to_code(tla):
        tla = (tla or "").upper()
        tla = CODE_OVERRIDES.get(tla, tla)
        return tla if tla in valid_codes else None

    try:
        matches = api_get_matches()
    except Exception as e:
        sys.exit(f"ERROR calling football-data.org: {e}")
    print(f"Fetched {len(matches)} finished match(es) from football-data.org.")

    changed = False
    for fx in matches:
        if fx.get("status") not in DONE:
            continue
        hc = to_code(fx.get("homeTeam", {}).get("tla"))
        ac = to_code(fx.get("awayTeam", {}).get("tla"))
        if not hc or not ac:
            print(f"  skip (no code): {fx.get('homeTeam',{}).get('name')} "
                  f"vs {fx.get('awayTeam',{}).get('name')}")
            continue
        match = by_pair.get(frozenset((hc, ac)))
        if not match:
            continue
        if match.get("status") == "finished" and not force:
            continue  # already recorded

        ft = fx.get("score", {}).get("fullTime", {})
        gh, ga = ft.get("home"), ft.get("away")
        if gh is None or ga is None:
            continue

        # orient home/away to your team1/team2
        if hc == match["t1"]:
            g1, g2 = gh, ga
        else:
            g1, g2 = ga, gh

        st = match["stats"]
        st["goalsTeam1"] = g1
        st["goalsTeam2"] = g2
        match["status"] = "finished"
        changed = True
        print(f"  updated #{match['matchNum']}: {match['t1']} {g1}-{g2} {match['t2']}")

    if changed:
        from datetime import datetime, timezone
        doc["lastUpdated"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print("JSON updated (scores only).")
    else:
        print("No new finished matches. Nothing changed.")


if __name__ == "__main__":
    main()
