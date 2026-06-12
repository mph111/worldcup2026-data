#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
World Cup 2026 — auto-update results into worldcup2026.json
Source: API-Football (api-sports.io), competition league=1 season=2026.

Usage (locally or in CI):
    API_FOOTBALL_KEY=xxxx python scripts/update_results.py

Optional env:
    JSON_PATH   path to the JSON file (default: worldcup2026.json)
    FORCE       "1" to re-fetch every finished match (default: only new ones)

It updates ONLY the group-stage `schedule` matches and recomputes group
standings. Knockout matches are left untouched (add them the same way later).
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse

API_BASE = "https://v3.football.api-sports.io"
LEAGUE = 1          # FIFA World Cup
SEASON = 2026
FINISHED = {"FT", "AET", "PEN"}   # match-finished statuses in API-Football

# Some API-Football team names differ from the names in your JSON.
# Map API-Football name -> your JSON `code`. Extend this as needed.
NAME_OVERRIDES = {
    "Korea Republic": "KOR",
    "South Korea": "KOR",
    "Czech Republic": "CZE",
    "Czechia": "CZE",
    "USA": "USA",
    "United States": "USA",
    "IR Iran": "IRN",
    "Iran": "IRN",
    "Cote d'Ivoire": "CIV",
    "Ivory Coast": "CIV",
    # add more here if a team is skipped with a "no code" warning
}

YELLOW_PENALTY = -1   # fair-play points
RED_PENALTY = -3


def api_get(path, **params):
    key = os.environ.get("API_FOOTBALL_KEY")
    if not key:
        sys.exit("ERROR: set API_FOOTBALL_KEY environment variable.")
    url = API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"x-apisports-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode("utf-8"))
    if data.get("errors"):
        print(f"  API errors on {path}: {data['errors']}", file=sys.stderr)
    return data.get("response", [])


def build_name_to_code(doc):
    """Map every team name (and overrides) to its JSON code."""
    m = {}
    for group in doc["groups"].values():
        for t in group:
            m[t["name"].strip().lower()] = t["code"]
    for name, code in NAME_OVERRIDES.items():
        m[name.strip().lower()] = code
    return m


def resolve_code(name, name2code):
    return name2code.get((name or "").strip().lower())


def stat_lookup(stat_block):
    """API /fixtures/statistics returns [{type, value}, ...] -> dict."""
    out = {}
    for item in stat_block.get("statistics", []):
        out[item.get("type")] = item.get("value")
    return out


def to_int(v, default=0):
    if v is None:
        return default
    if isinstance(v, str):
        v = v.replace("%", "").strip()
        if v == "":
            return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def map_stats_for_side(s):
    """One team's API stat dict -> your per-team stat values."""
    return {
        "possession": to_int(s.get("Ball Possession"), 50),
        "shots": to_int(s.get("Total Shots")),
        "shotsOnTarget": to_int(s.get("Shots on Goal")),
        "corners": to_int(s.get("Corner Kicks")),
        "fouls": to_int(s.get("Fouls")),
        "yellowCards": to_int(s.get("Yellow Cards")),
        "redCards": to_int(s.get("Red Cards")),
        "offsides": to_int(s.get("Offsides")),
        "passes": to_int(s.get("Total passes")),
        "passAccuracy": to_int(s.get("Passes %")),
    }


def main():
    json_path = os.environ.get("JSON_PATH", "worldcup2026.json")
    force = os.environ.get("FORCE") == "1"

    with open(json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    name2code = build_name_to_code(doc)

    # index group-stage matches by unordered code pair
    by_pair = {}
    for m in doc["schedule"]:
        by_pair[frozenset((m["t1"], m["t2"]))] = m

    fixtures = api_get("/fixtures", league=LEAGUE, season=SEASON)
    print(f"Fetched {len(fixtures)} fixtures from API-Football.")

    changed = False
    for fx in fixtures:
        if fx["fixture"]["status"]["short"] not in FINISHED:
            continue
        home, away = fx["teams"]["home"], fx["teams"]["away"]
        hc = resolve_code(home["name"], name2code)
        ac = resolve_code(away["name"], name2code)
        if not hc or not ac:
            print(f"  skip (no code): {home['name']} vs {away['name']}")
            continue
        match = by_pair.get(frozenset((hc, ac)))
        if not match:
            continue
        if match.get("status") == "finished" and not force:
            continue  # already recorded, save API quota

        fid = fx["fixture"]["id"]
        # which API side is team1 / team2 in YOUR schedule?
        code_by_id = {home["id"]: hc, away["id"]: ac}
        goals_by_id = {home["id"]: fx["goals"]["home"] or 0,
                       away["id"]: fx["goals"]["away"] or 0}
        side_of_id = {tid: ("team1" if code == match["t1"] else "team2")
                      for tid, code in code_by_id.items()}

        # ---- statistics (1 request) ----
        stats_resp = api_get("/fixtures/statistics", fixture=fid)
        per_side = {"team1": map_stats_for_side({}),
                    "team2": map_stats_for_side({})}
        for block in stats_resp:
            tid = block["team"]["id"]
            if tid in side_of_id:
                per_side[side_of_id[tid]] = map_stats_for_side(stat_lookup(block))

        st = match["stats"]
        for tid, side in side_of_id.items():
            st[f"goals{side.capitalize()}"] = goals_by_id[tid]
        for side in ("team1", "team2"):
            cap = side.capitalize()
            for k, v in per_side[side].items():
                st[f"{k}{cap}"] = v

        # ---- events -> scorers (1 request) ----
        events = api_get("/fixtures/events", fixture=fid)
        scorers = []
        for ev in events:
            if ev.get("type") != "Goal":
                continue
            detail = ev.get("detail", "")
            if detail == "Missed Penalty":
                continue
            ev_tid = ev["team"]["id"]
            side = side_of_id.get(ev_tid)
            if side is None:
                continue
            if detail == "Own Goal":   # counts for the other team
                side = "team2" if side == "team1" else "team1"
            scorers.append({
                "name": ev["player"]["name"] or "",
                "team": side,
                "time": to_int(ev.get("time", {}).get("elapsed")),
                "type": "normal",
            })
        scorers.sort(key=lambda s: s["time"])
        match["scorers"] = scorers
        match["status"] = "finished"
        changed = True
        print(f"  updated #{match['matchNum']}: {hc} "
              f"{st['goalsTeam1']}-{st['goalsTeam2']} {ac}")
        time.sleep(1)  # be gentle with rate limit

    if changed:
        recompute_standings(doc)
        from datetime import datetime, timezone
        doc["lastUpdated"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
        print("JSON updated.")
    else:
        print("No new finished matches. Nothing changed.")


def recompute_standings(doc):
    """Rebuild numeric standings for every group from finished matches."""
    # which group each code belongs to
    group_of = {}
    teams = {}
    for gname, group in doc["groups"].items():
        for t in group:
            group_of[t["code"]] = gname
            teams[t["code"]] = t
            t.update(points=0, played=0, won=0, drawn=0, lost=0,
                     goalsFor=0, goalsAgainst=0, goalDifference=0,
                     yellowCards=0, redCards=0)

    for m in doc["schedule"]:
        if m.get("status") != "finished":
            continue
        a, b = m["t1"], m["t2"]
        if a not in teams or b not in teams:
            continue
        s = m["stats"]
        ga, gb = s["goalsTeam1"], s["goalsTeam2"]
        for code, gf, gag, yc, rc in (
            (a, ga, gb, s["yellowCardsTeam1"], s["redCardsTeam1"]),
            (b, gb, ga, s["yellowCardsTeam2"], s["redCardsTeam2"]),
        ):
            t = teams[code]
            t["played"] += 1
            t["goalsFor"] += gf
            t["goalsAgainst"] += gag
            t["yellowCards"] += yc
            t["redCards"] += rc
        if ga > gb:
            teams[a]["won"] += 1; teams[b]["lost"] += 1
        elif ga < gb:
            teams[b]["won"] += 1; teams[a]["lost"] += 1
        else:
            teams[a]["drawn"] += 1; teams[b]["drawn"] += 1

    for t in teams.values():
        t["points"] = t["won"] * 3 + t["drawn"]
        t["goalDifference"] = t["goalsFor"] - t["goalsAgainst"]
        t["fairPlay"] = t["yellowCards"] * YELLOW_PENALTY + t["redCards"] * RED_PENALTY

    # positions within each group: points, GD, GF, fairPlay, code
    for group in doc["groups"].values():
        ordered = sorted(group, key=lambda t: (
            -t["points"], -t["goalDifference"], -t["goalsFor"],
            -t["fairPlay"], t["code"]))
        for i, t in enumerate(ordered, 1):
            t["position"] = i


if __name__ == "__main__":
    main()

