from curl_cffi import requests as cffi_requests

_HEADERS = {
    "Referer": "https://www.atptour.com/en/scores/current",
    "Accept":  "application/json, text/plain, */*",
}


def _get_json(url):
    """Fetch Hawkeye URL; return parsed JSON or None on error."""
    try:
        resp = cffi_requests.get(url, headers=_HEADERS, impersonate="chrome120", timeout=10)
        if not resp.ok:
            return None
        return resp.json()
    except Exception:
        return None


def _ratio(field):
    d, div = field.get("Dividend"), field.get("Divisor")
    if div and div > 0:
        return d / div
    return field["Percent"] / 100


def _player_stats(sets_array):
    agg = next(s for s in sets_array if s["SetNumber"] == 0)
    svc = agg["Stats"]["ServiceStats"]
    ret = agg["Stats"]["ReturnStats"]
    return {
        "first_in":      _ratio(svc["FirstServe"]),
        "win_first":     _ratio(svc["FirstServePointsWon"]),
        "win_second":    _ratio(svc["SecondServePointsWon"]),
        "return_first":  _ratio(ret["FirstServeReturnPointsWon"]),
        "return_second": _ratio(ret["SecondServeReturnPointsWon"]),
    }


def _parse_state_from_json(data):
    match = data["Match"]
    t1    = match["PlayerTeam1"]
    t2    = match["PlayerTeam2"]

    p1_name = f"{t1['PlayerFirstNameFull']} {t1['PlayerLastName']}"
    p2_name = f"{t2['PlayerFirstNameFull']} {t2['PlayerLastName']}"

    p1_stats = _player_stats(t1["Sets"])
    p2_stats = _player_stats(t2["Sets"])

    p1_map = {s["SetNumber"]: s["SetScore"] for s in t1["Sets"]
              if s["SetNumber"] > 0 and s["SetScore"] is not None}
    p2_map = {s["SetNumber"]: s["SetScore"] for s in t2["Sets"]
              if s["SetNumber"] > 0 and s["SetScore"] is not None}
    set_nums  = sorted(set(p1_map) | set(p2_map))
    score_str = " ".join(f"{int(p1_map.get(n,0))}-{int(p2_map.get(n,0))}" for n in set_nums)

    pt = match["PlayerTeam"]
    ot = match["OpponentTeam"]
    if pt["Player"]["PlayerId"] == t1["PlayerId"]:
        g1, g2 = pt["GameScore"], ot["GameScore"]
    else:
        g1, g2 = ot["GameScore"], pt["GameScore"]
    game_score_str = f"{g1}-{g2}"

    last_server      = (match.get("LastServer") or "").upper()
    p1_id            = t1["PlayerId"].upper()
    game_in_progress = not (g1 == "0" and g2 == "0")

    if last_server in (p1_id, t2["PlayerId"].upper()):
        last_server_is_p1 = (last_server == p1_id)
        p1_serves = last_server_is_p1 if game_in_progress else not last_server_is_p1
    else:
        p1_serves = (match["ServerTeam"] == 1)

    return {
        "p1_name":        p1_name,
        "p2_name":        p2_name,
        "p1_stats":       p1_stats,
        "p2_stats":       p2_stats,
        "score_str":      score_str,
        "game_score_str": game_score_str,
        "p1_serves":      bool(p1_serves),
        "best_of":        match["NumberOfSets"],
    }


def fetch_match_state(url):
    """Return match state dict if match is in-progress, else None."""
    data = _get_json(url)
    if data is None:
        return None
    if data.get("Match", {}).get("MatchStatus") != "P":
        return None
    return _parse_state_from_json(data)
