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


def _raw(field):
    d, div = field.get("Dividend"), field.get("Divisor")
    if div and div > 0 and d is not None and d >= 0:
        return int(d), int(div)
    return None, None


def _player_stats(sets_array):
    agg = next(s for s in sets_array if s["SetNumber"] == 0)
    svc = agg["Stats"]["ServiceStats"]
    ret = agg["Stats"]["ReturnStats"]

    fi_d,  fi_n  = _raw(svc["FirstServe"])
    wf_d,  wf_n  = _raw(svc["FirstServePointsWon"])
    ws_d,  ws_n  = _raw(svc["SecondServePointsWon"])
    rf_d,  rf_n  = _raw(ret["FirstServeReturnPointsWon"])
    rs_d,  rs_n  = _raw(ret["SecondServeReturnPointsWon"])

    return {
        "first_in":           _ratio(svc["FirstServe"]),
        "win_first":          _ratio(svc["FirstServePointsWon"]),
        "win_second":         _ratio(svc["SecondServePointsWon"]),
        "return_first":       _ratio(ret["FirstServeReturnPointsWon"]),
        "return_second":      _ratio(ret["SecondServeReturnPointsWon"]),
        "first_in_num":       fi_d,  "first_in_den":       fi_n,
        "win_first_num":      wf_d,  "win_first_den":      wf_n,
        "win_second_num":     ws_d,  "win_second_den":      ws_n,
        "return_first_num":   rf_d,  "return_first_den":   rf_n,
        "return_second_num":  rs_d,  "return_second_den":  rs_n,
    }


def _parse_state_from_json(data):
    match = data["Match"]
    t1    = match["PlayerTeam1"]
    t2    = match["PlayerTeam2"]
    return {
        "p1_name":  f"{t1['PlayerFirstNameFull']} {t1['PlayerLastName']}",
        "p2_name":  f"{t2['PlayerFirstNameFull']} {t2['PlayerLastName']}",
        "p1_stats": _player_stats(t1["Sets"]),
        "p2_stats": _player_stats(t2["Sets"]),
        "best_of":  match["NumberOfSets"],
    }


_STAT_KEYS = ["first_in", "win_first", "win_second", "return_first", "return_second"]


def stats_ready(stats):
    """False if any stat's num/den is missing, num is negative, or den <= 2."""
    for key in _STAT_KEYS:
        num, den = stats.get(f"{key}_num"), stats.get(f"{key}_den")
        if num is None or den is None or num < 2 or den <= 2:
            return False
    return True


def fetch_match_state(url):
    """Return match state dict if match is in-progress, else None."""
    data = _get_json(url)
    if data is None:
        return None
    if data.get("Match", {}).get("MatchStatus") != "P":
        return None
    return _parse_state_from_json(data)
