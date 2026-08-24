"""Exact win probabilities via dynamic programming (Barnett & Clarke 2005; O'Malley 2008).

iid points at per-server win rates, stats drawn from Beta posteriors, each draw
evaluated exactly (no dice-rolling), so the only remaining noise is stat-draw
variance. All computations are vectorized across draws: probabilities are numpy
arrays of shape (n_draws,).
"""
from collections import defaultdict

import numpy as np

import trade.config as cfg

_EPS = 1e-12


# ── Score-string parsing ──────────────────────────────────────────────────────

def _parse_score(score_str):
    result = []
    for s in score_str.strip().split():
        p1g, p2g = map(int, s.split('-'))
        result.append((p1g, p2g))
    return result


def _is_set_complete(p1g, p2g):
    if p1g == 7 and p2g == 6: return True
    if p2g == 7 and p1g == 6: return True
    if p1g >= 6 and p1g - p2g >= 2: return True
    if p2g >= 6 and p2g - p1g >= 2: return True
    return False


def _parse_match_state(score_str, best_of):
    sets_won = [0, 0]
    current_set_games = None
    for p1g, p2g in _parse_score(score_str):
        if _is_set_complete(p1g, p2g):
            if p1g > p2g: sets_won[0] += 1
            else:         sets_won[1] += 1
        else:
            current_set_games = (p1g, p2g)
            break
    return sets_won, current_set_games


_NOTATION = {'0': 0, '15': 1, '30': 2, '40': 3, 'Ad': 4, 'AD': 4, 'A': 4}


def _parse_game_score(game_score_str, is_tiebreak=False):
    s = game_score_str.strip()
    if not s or s == "0-0":
        return (0, 0)
    left, right = s.split('-')
    if is_tiebreak:
        return (int(left), int(right))
    return (_NOTATION[left], _NOTATION[right])


def point_win_prob(server, receiver):
    """Effective point-win prob for the server (1st/2nd serve blended with returner)."""
    p_first  = (server["win_first"]  + (1 - receiver["return_first"]))  / 2
    p_second = (server["win_second"] + (1 - receiver["return_second"])) / 2
    return server["first_in"] * p_first + (1 - server["first_in"]) * p_second


def game_win_prob(p, a=0, b=0):
    """Server wins the game from point score (a, b); ad scoring (Ad -> 4).
    At break points (receiver one point from winning) the server's point-win prob
    is reduced by cfg.BP_PRESSURE to model serving under pressure (0 = off)."""
    if a >= 4 and a - b >= 2:
        return 1.0 + 0 * p
    if b >= 4 and b - a >= 2:
        return 0.0 * p
    p_bp = np.clip(p - getattr(cfg, "BP_PRESSURE", 0.0), 0.0, 1.0)
    if a >= 3 and b >= 3:
        deuce = p * p / (1 - (1 - p) * (p + p_bp))
        if a == b:
            return deuce
        if a > b:                       # advantage server (not a break point)
            return p + (1 - p) * deuce
        return p_bp * deuce             # advantage receiver (break point)
    p_pt = p_bp if (b >= 3 and b - a >= 1) else p
    return p_pt * game_win_prob(p, a + 1, b) + (1 - p_pt) * game_win_prob(p, a, b + 1)


def _tb_first_server(a, b, a_serves_next):
    """Recover who served point 1 of the tiebreak from the current point count
    and who serves next (rotation: 1 point, then alternate every 2)."""
    n = a + b
    return a_serves_next if ((n + 1) // 2) % 2 == 0 else not a_serves_next


def tiebreak_win_prob(pA, pB, a=0, b=0, a_serves_next=True):
    """P(A wins the tiebreak) from point score (a, b). First-to-7, win by 2."""
    first_A = _tb_first_server(a, b, a_serves_next)
    return _tb(pA, pB, a, b, first_A, {})


def _tb(pA, pB, a, b, first_A, memo):
    if a >= 7 and a - b >= 2:
        return 1.0 + 0 * pA
    if b >= 7 and b - a >= 2:
        return 0.0 * pA
    if a == b and a >= 6:
        # win-by-2 race; every subsequent pair of points has one serve by each player
        win_pair  = pA * (1 - pB)
        lose_pair = (1 - pA) * pB
        return win_pair / (win_pair + lose_pair + _EPS)
    key = (a, b)
    if key in memo:
        return memo[key]
    n = a + b
    A_serves = first_A if ((n + 1) // 2) % 2 == 0 else not first_A
    pa = pA if A_serves else (1 - pB)
    r = pa * _tb(pA, pB, a + 1, b, first_A, memo) + (1 - pa) * _tb(pA, pB, a, b + 1, first_A, memo)
    memo[key] = r
    return r


def _set_vec(pA, pB, ga, gb, srv_A, holdA, holdB, memo):
    """4-vector DP over game states: (A wins & A serves first next set,
    A wins & B first, B wins & A first, B wins & B first).
    srv_A: whether A serves the NEXT game from this state. Serve alternation
    continues across sets, so the state's next-server IS the next set's first
    server when the set ends here."""
    zero = 0 * pA
    if (ga >= 6 and ga - gb >= 2) or (ga == 7 and gb == 6):
        w = 1.0 + zero
        return (w, zero, zero, zero) if srv_A else (zero, w, zero, zero)
    if (gb >= 6 and gb - ga >= 2) or (gb == 7 and ga == 6):
        w = 1.0 + zero
        return (zero, zero, w, zero) if srv_A else (zero, zero, zero, w)
    if ga == 6 and gb == 6:
        tA = _tb(pA, pB, 0, 0, srv_A, {})
        # after a tiebreak the TB's first server receives first next set
        next_A = not srv_A
        wA, wB = tA, 1 - tA
        return (wA, zero, wB, zero) if next_A else (zero, wA, zero, wB)
    key = (ga, gb, srv_A)
    if key in memo:
        return memo[key]
    p_hold = holdA if srv_A else holdB
    p_A_wins_game = p_hold if srv_A else 1 - p_hold
    v_win  = _set_vec(pA, pB, ga + 1, gb, not srv_A, holdA, holdB, memo)
    v_lose = _set_vec(pA, pB, ga, gb + 1, not srv_A, holdA, holdB, memo)
    r = tuple(p_A_wins_game * w + (1 - p_A_wins_game) * l for w, l in zip(v_win, v_lose))
    memo[key] = r
    return r


def _match_prob(sa, sb, first_A, need, f00_A, f00_B, memo):
    """P(A wins match) from sets (sa, sb), given who serves first in the next set.
    f00_X: set 4-vector from 0-0 when X serves first."""
    if sa >= need:
        return 1.0
    if sb >= need:
        return 0.0
    key = (sa, sb, first_A)
    if key in memo:
        return memo[key]
    v = f00_A if first_A else f00_B
    r = (v[0] * _match_prob(sa + 1, sb, True,  need, f00_A, f00_B, memo)
         + v[1] * _match_prob(sa + 1, sb, False, need, f00_A, f00_B, memo)
         + v[2] * _match_prob(sa, sb + 1, True,  need, f00_A, f00_B, memo)
         + v[3] * _match_prob(sa, sb + 1, False, need, f00_A, f00_B, memo))
    memo[key] = r
    return r


def _scoreline_dist(sa, sb, first_A, need, f00_A, f00_B, memo):
    """Distribution over final set scores {(fa, fb): prob} from sets (sa, sb)."""
    if sa >= need or sb >= need:
        return {(sa, sb): 1.0}
    key = (sa, sb, first_A)
    if key in memo:
        return memo[key]
    v = f00_A if first_A else f00_B
    branches = [(v[0], sa + 1, sb, True), (v[1], sa + 1, sb, False),
                (v[2], sa, sb + 1, True), (v[3], sa, sb + 1, False)]
    out = {}
    for w, na, nb, nf in branches:
        for score, p in _scoreline_dist(na, nb, nf, need, f00_A, f00_B, memo).items():
            out[score] = out.get(score, 0.0) + w * p
    memo[key] = out
    return out


def win_probs(pA, pB, sets_won, set_games, in_tiebreak, game_state, p1_serves, best_of):
    """Exact probabilities for P1, for given point probs (scalars or arrays).
    Returns {'match','set','game',
             'cond': {win_game, lose_game, win_set, lose_set}  (match prob conditionals),
             'scorelines': {(fa, fb): prob}}."""
    need = best_of // 2 + 1
    holdA = game_win_prob(pA)
    holdB = game_win_prob(pB)
    ga, gb = set_games
    a, b = game_state
    zero = 0 * pA

    set_memo = {}
    if in_tiebreak:
        game_p1 = _tb(pA, pB, a, b, _tb_first_server(a, b, p1_serves), {})
        next_A = not _tb_first_server(a, b, p1_serves)
        one = 1.0 + zero
        # winning/losing the TB decides the set
        vw = (one, zero, zero, zero) if next_A else (zero, one, zero, zero)
        vl = (zero, zero, one, zero) if next_A else (zero, zero, zero, one)
        cur_vec = tuple(game_p1 * w + (1 - game_p1) * l for w, l in zip(vw, vl))
    else:
        g_srv = game_win_prob(pA if p1_serves else pB,
                              a if p1_serves else b,
                              b if p1_serves else a)
        game_p1 = g_srv if p1_serves else 1 - g_srv
        vw = _set_vec(pA, pB, ga + 1, gb, not p1_serves, holdA, holdB, set_memo)
        vl = _set_vec(pA, pB, ga, gb + 1, not p1_serves, holdA, holdB, set_memo)
        cur_vec = tuple(game_p1 * w + (1 - game_p1) * l for w, l in zip(vw, vl))

    set_p1 = cur_vec[0] + cur_vec[1]

    f00_A = _set_vec(pA, pB, 0, 0, True,  holdA, holdB, set_memo)
    f00_B = _set_vec(pA, pB, 0, 0, False, holdA, holdB, set_memo)
    m_memo = {}
    sa, sb = sets_won

    def match_from(vec):
        return (vec[0] * _match_prob(sa + 1, sb, True,  need, f00_A, f00_B, m_memo)
                + vec[1] * _match_prob(sa + 1, sb, False, need, f00_A, f00_B, m_memo)
                + vec[2] * _match_prob(sa, sb + 1, True,  need, f00_A, f00_B, m_memo)
                + vec[3] * _match_prob(sa, sb + 1, False, need, f00_A, f00_B, m_memo))

    match_p1 = match_from(cur_vec)

    # conditionals on the current game (vw/vl are set-vectors given the game outcome)
    cond = {"win_game": match_from(vw), "lose_game": match_from(vl)}
    # conditionals on the current set, from the components of cur_vec
    pw = cur_vec[0] + cur_vec[1]
    pl = cur_vec[2] + cur_vec[3]
    cond["win_set"] = (cur_vec[0] * _match_prob(sa + 1, sb, True,  need, f00_A, f00_B, m_memo)
                       + cur_vec[1] * _match_prob(sa + 1, sb, False, need, f00_A, f00_B, m_memo)) / (pw + _EPS)
    cond["lose_set"] = (cur_vec[2] * _match_prob(sa, sb + 1, True,  need, f00_A, f00_B, m_memo)
                        + cur_vec[3] * _match_prob(sa, sb + 1, False, need, f00_A, f00_B, m_memo)) / (pl + _EPS)

    # match-win volatility: size of the swing in match_p1 from the next point / current game
    cwg, clg = cond["win_game"], cond["lose_game"]
    if in_tiebreak:
        first_A = _tb_first_server(a, b, p1_serves)
        A_serves = first_A if ((a + b + 1) // 2) % 2 == 0 else not first_A
        q = pA if A_serves else (1 - pB)
        g_w = _tb(pA, pB, a + 1, b, first_A, {})
        g_l = _tb(pA, pB, a, b + 1, first_A, {})
    elif p1_serves:
        q = pA
        g_w = game_win_prob(pA, a + 1, b)
        g_l = game_win_prob(pA, a, b + 1)
    else:
        q = 1 - pB
        g_w = 1 - game_win_prob(pB, b, a + 1)
        g_l = 1 - game_win_prob(pB, b + 1, a)
    vol_game = np.sqrt(game_p1 * (1 - game_p1)) * np.abs(cwg - clg)
    vol_point = np.sqrt(q * (1 - q)) * np.abs(g_w - g_l) * np.abs(cwg - clg)

    # final scoreline distribution
    sl_memo = {}
    scorelines = {}
    branches = [(cur_vec[0], sa + 1, sb, True), (cur_vec[1], sa + 1, sb, False),
                (cur_vec[2], sa, sb + 1, True), (cur_vec[3], sa, sb + 1, False)]
    for w, na, nb, nf in branches:
        for score, p in _scoreline_dist(na, nb, nf, need, f00_A, f00_B, sl_memo).items():
            scorelines[score] = scorelines.get(score, 0.0) + w * p

    return {"match": match_p1, "set": set_p1, "game": game_p1,
            "cond": cond, "scorelines": scorelines,
            "vol": {"point": vol_point, "game": vol_game}}


def weighted_quantile(pairs, q):
    """q-quantile of a list of (value, prob) pairs (probs need not sum to 1)."""
    if not pairs:
        return None
    pairs = sorted(pairs)
    total = sum(p for _, p in pairs) or 1.0
    cum = 0.0
    for v, p in pairs:
        cum += p
        if cum >= q * total:
            return v
    return pairs[-1][0]


def win_prob_forward(pA, pB, sets_won, set_games, in_tiebreak, game_state,
                     p1_serves, best_of, max_games=6):
    """Exact forward distribution of p1's match win prob at each of the next
    `max_games` game boundaries, plus its distribution when the current set ends.

    An exact DP forward walk over game-boundary states (each carrying its
    probability), evaluating the win prob at every node via win_probs. pA/pB are
    scalar point-estimate point-win probs. Returns
      {"levels":   [ [(winpct, prob), ...] for k in 0..max_games ],
       "set_dist": [(winpct, prob), ...] at current-set completion}.
    levels[0] is the current win prob (a single point mass). Because win prob is a
    martingale, each level's probability-weighted mean equals levels[0]."""
    need = best_of // 2 + 1
    holdA, holdB = float(game_win_prob(pA)), float(game_win_prob(pB))
    sa0, sb0 = sets_won
    ga0, gb0 = set_games
    a, b = game_state

    wp_memo = {}

    def winpct(sa, sb, ga, gb, srv):
        if sa >= need:
            return 1.0
        if sb >= need:
            return 0.0
        key = (sa, sb, ga, gb, srv)
        if key not in wp_memo:
            wp_memo[key] = float(win_probs(pA, pB, (sa, sb), (ga, gb),
                                           ga == 6 and gb == 6, (0, 0), srv, best_of)["match"])
        return wp_memo[key]

    def step(state):
        """Successors (state, prob) of a fresh-game boundary state."""
        sa, sb, ga, gb, srv = state
        if sa >= need or sb >= need:
            return [(state, 1.0)]
        if ga == 6 and gb == 6:
            tb1 = float(_tb(pA, pB, 0, 0, srv, {}))
            return [((sa + 1, sb, 0, 0, not srv), tb1),
                    ((sa, sb + 1, 0, 0, not srv), 1 - tb1)]
        p1g = holdA if srv else 1 - holdB
        w = ((sa + 1, sb, 0, 0, not srv) if ga + 1 >= 6 and ga + 1 - gb >= 2
             else (sa, sb, ga + 1, gb, not srv))
        l = ((sa, sb + 1, 0, 0, not srv) if gb + 1 >= 6 and gb + 1 - ga >= 2
             else (sa, sb, ga, gb + 1, not srv))
        return [(w, p1g), (l, 1 - p1g)]

    p0 = float(win_probs(pA, pB, (sa0, sb0), (ga0, gb0), in_tiebreak,
                         game_state, p1_serves, best_of)["match"])
    levels = [[(p0, 1.0)]]

    # level 1: resolve the current (possibly partial) game / tiebreak
    if in_tiebreak:
        first_A = _tb_first_server(a, b, p1_serves)
        gp1 = float(_tb(pA, pB, a, b, first_A, {}))
        succ = [((sa0 + 1, sb0, 0, 0, not first_A), gp1),
                ((sa0, sb0 + 1, 0, 0, not first_A), 1 - gp1)]
    else:
        gp1 = float(game_win_prob(pA, a, b)) if p1_serves else 1 - float(game_win_prob(pB, b, a))
        nsrv = not p1_serves
        w = ((sa0 + 1, sb0, 0, 0, nsrv) if ga0 + 1 >= 6 and ga0 + 1 - gb0 >= 2
             else (sa0, sb0, ga0 + 1, gb0, nsrv))
        l = ((sa0, sb0 + 1, 0, 0, nsrv) if gb0 + 1 >= 6 and gb0 + 1 - ga0 >= 2
             else (sa0, sb0, ga0, gb0 + 1, nsrv))
        succ = [(w, gp1), (l, 1 - gp1)]
    dist = defaultdict(float)
    for st, pr in succ:
        dist[st] += pr
    levels.append([(winpct(*st), pr) for st, pr in dist.items()])

    cur = dict(dist)
    for _ in range(2, max_games + 1):
        nxt = defaultdict(float)
        for st, pr in cur.items():
            for st2, pr2 in step(st):
                nxt[st2] += pr * pr2
        cur = nxt
        levels.append([(winpct(*st), pr) for st, pr in cur.items()])

    # distribution of the win prob at the moment the current set completes
    setdone = defaultdict(float)
    frontier = dict(dist)
    while frontier:
        nf = defaultdict(float)
        for st, pr in frontier.items():
            if st[0] + st[1] > sa0 + sb0:
                setdone[st] += pr
            else:
                for st2, pr2 in step(st):
                    nf[st2] += pr * pr2
        frontier = nf
    set_dist = [(winpct(*st), pr) for st, pr in setdone.items()]

    return {"levels": levels, "set_dist": set_dist}


_STAT_KEYS = ["first_in", "win_first", "win_second", "return_first", "return_second"]


def _draw_stats(stats, n):
    """Draw n samples per stat from its Beta(num+0.5, den-num+0.5) posterior,
    or the raw rate when counts are missing."""
    out = {}
    for k in _STAT_KEYS:
        num, den = stats.get(k + "_num"), stats.get(k + "_den")
        if num is not None and den is not None:
            out[k] = np.random.beta(num + 0.5, (den - num) + 0.5, n)
        else:
            out[k] = np.full(n, float(stats[k]))
    return out


def estimate_win_prob(p1_stats, p2_stats, score_str, game_score_str,
                      p1_serves, best_of, n_draws=None):
    """Match/set/game win probs for p1, averaged over N_DRAWS stat draws (each
    evaluated exactly)."""
    n = n_draws or getattr(cfg, "N_DRAWS", 500)
    sets_won, current_set_games = _parse_match_state(score_str, best_of)
    in_tiebreak = current_set_games == (6, 6)
    game_state = _parse_game_score(game_score_str, is_tiebreak=in_tiebreak)
    set_games = current_set_games if current_set_games is not None else (0, 0)

    s1 = _draw_stats(p1_stats, n)
    s2 = _draw_stats(p2_stats, n)
    pA = point_win_prob(s1, s2)
    pB = point_win_prob(s2, s1)

    probs = win_probs(pA, pB, sets_won, set_games, in_tiebreak, game_state, p1_serves, best_of)
    out = {k: float(np.mean(probs[k])) for k in ("match", "set", "game")}
    out["cond"] = {k: float(np.mean(v)) for k, v in probs["cond"].items()}
    out["scorelines"] = {k: float(np.mean(v)) for k, v in probs["scorelines"].items()}
    out["vol"] = {k: float(np.mean(v)) for k, v in probs["vol"].items()}
    return out


def implied_point_probs(price, best_of, prior_n=None, base=None, n=4000):
    """Invert the market's pre-match match-win price into symmetric per-server
    point-win probs (base + d, base - d). The base fixes the overall serve level
    (one equation short otherwise); the price only pins the skill gap d.

    Inversion targets the *draw-averaged* 0-0 value at the configured prior_n — not
    the point estimate — so the live model reproduces the market at t=0. (A point
    estimate would be pulled toward 0.5 by prior uncertainty, i.e. Jensen shrinkage,
    making the bot think every favorite is overpriced on the first tick.)"""
    b = base if base is not None else getattr(cfg, "INVERSION_BASE", 0.64)
    N = prior_n if prior_n is not None else getattr(cfg, "MARKET_PRIOR_N", 40)
    price = min(max(float(price), 0.02), 0.98)
    half = min(b, 1 - b) - 0.02
    lo, hi = -half, half
    for _ in range(45):
        d = (lo + hi) / 2
        rng = np.random.default_rng(12345)  # seeded -> m(d) deterministic & monotone
        pA = rng.beta(N * (b + d) + 0.5, N * (1 - b - d) + 0.5, n)
        pB = rng.beta(N * (b - d) + 0.5, N * (1 - b + d) + 0.5, n)
        m = float(np.mean(win_probs(pA, pB, (0, 0), (0, 0), False, (0, 0), True, best_of)["match"]))
        if m < price:
            lo = d
        else:
            hi = d
    d = (lo + hi) / 2
    return b + d, b - d


def estimate_win_prob_market(pA0, pB0, wonA, playedA, wonB, playedB,
                             score_str, game_score_str, p1_serves, best_of,
                             prior_n=None, n_draws=None):
    """Blend market-implied point probs (pA0, pB0) with in-match service counts as
    Beta pseudo-counts (prior_n virtual points), draw n samples, evaluate the exact
    DP per draw, average. Posterior mean per server tapers from pA0 (0 points played)
    to the empirical rate (many points). Returns the usual dict plus blended probs
    and the current market weight per player."""
    n = n_draws or getattr(cfg, "N_DRAWS", 500)
    N = prior_n if prior_n is not None else getattr(cfg, "MARKET_PRIOR_N", 40)
    sets_won, current_set_games = _parse_match_state(score_str, best_of)
    in_tiebreak = current_set_games == (6, 6)
    game_state = _parse_game_score(game_score_str, is_tiebreak=in_tiebreak)
    set_games = current_set_games if current_set_games is not None else (0, 0)

    pA = np.random.beta(N * pA0 + wonA + 0.5, N * (1 - pA0) + (playedA - wonA) + 0.5, n)
    pB = np.random.beta(N * pB0 + wonB + 0.5, N * (1 - pB0) + (playedB - wonB) + 0.5, n)

    probs = win_probs(pA, pB, sets_won, set_games, in_tiebreak, game_state, p1_serves, best_of)
    out = {k: float(np.mean(probs[k])) for k in ("match", "set", "game")}
    out["cond"] = {k: float(np.mean(v)) for k, v in probs["cond"].items()}
    out["scorelines"] = {k: float(np.mean(v)) for k, v in probs["scorelines"].items()}
    out["vol"] = {k: float(np.mean(v)) for k, v in probs["vol"].items()}
    out["pa_blend"] = float((N * pA0 + wonA) / (N + playedA)) if (N + playedA) else pA0
    out["pb_blend"] = float((N * pB0 + wonB) / (N + playedB)) if (N + playedB) else pB0
    out["wt_a"] = N / (N + playedA) if (N + playedA) else 1.0
    out["wt_b"] = N / (N + playedB) if (N + playedB) else 1.0
    return out


# ── Derived distributions for logging (scalar pA/pB; point estimate) ────────────
# These are ~1000x too expensive to draw-average (measured: 50s/tick vs 38ms), so
# they run once on the blended point estimate. Mutually consistent with each other;
# they differ ~1-2% from the draw-averaged match prob (Jensen). Logging only.

def _set_outcome_pmf(pA, pB, srv_A_first, ga0, gb0, first_game_state, first_in_tb):
    """Distribution over (a_won_set, total_games_in_set) from a (possibly partial)
    set state. srv_A_first: A serves the current/first game; first_game_state: its
    point score; first_in_tb: the current set is already in a tiebreak."""
    holdA, holdB = game_win_prob(pA), game_win_prob(pB)
    memo = {}

    def rec(ga, gb, srv_A, first):
        if (ga >= 6 and ga - gb >= 2) or (ga == 7 and gb in (5, 6)):
            return {(True, ga + gb): 1.0}
        if (gb >= 6 and gb - ga >= 2) or (gb == 7 and ga in (5, 6)):
            return {(False, ga + gb): 1.0}
        if ga == 6 and gb == 6:
            if first and first_in_tb:
                a, b = first_game_state
                tA = _tb(pA, pB, a, b, _tb_first_server(a, b, srv_A), {})
            else:
                tA = _tb(pA, pB, 0, 0, srv_A, {})
            return {(True, 13): tA, (False, 13): 1 - tA}
        key = (ga, gb, srv_A, first)
        if key in memo:
            return memo[key]
        if first and not first_in_tb:
            a, b = first_game_state
            g = game_win_prob(pA if srv_A else pB, a if srv_A else b, b if srv_A else a)
        else:
            g = holdA if srv_A else holdB
        p_A_game = g if srv_A else 1 - g
        out = {}
        for res, w in ((rec(ga + 1, gb, not srv_A, False), p_A_game),
                       (rec(ga, gb + 1, not srv_A, False), 1 - p_A_game)):
            for k, p in res.items():
                out[k] = out.get(k, 0.0) + w * p
        memo[key] = out
        return out

    return rec(ga0, gb0, srv_A_first, True)


def match_report(pA, pB, score_str, game_score_str, p1_serves, best_of,
                 game_thresholds=(), target_match=None):
    """Forward-looking (from the current state) derived distributions for p1:
      scorelines: {("p1"/"p2", sets_dropped_by_winner): prob}
      set_win:    list over set index (0-based) of (P(p1 wins & played), P(p2 wins & played))
      over_games: {threshold: P(total match games > threshold)}
    Future sets marginalize over first server (tiny effect; avoids server tracking).

    target_match: if given, the point probs are tilt-adjusted (symmetric around their
    mean) so this call's point-estimate match prob equals it — makes the distributions
    an exact decomposition of the draw-averaged (traded) match probability rather than
    overshooting it (Jensen). Pass the traded mc_prob here."""
    need = best_of // 2 + 1
    sets_won, current_set_games = _parse_match_state(score_str, best_of)
    in_tiebreak = current_set_games == (6, 6)
    game_state = _parse_game_score(game_score_str, is_tiebreak=in_tiebreak)
    set_games = current_set_games if current_set_games is not None else (0, 0)
    completed_games = sum(a + b for a, b in _parse_score(score_str) if _is_set_complete(a, b))
    sa0, sb0 = sets_won
    ga, gb = set_games

    if target_match is not None:
        base = (pA + pB) / 2
        tgt = min(max(float(target_match), 0.001), 0.999)
        half = min(base, 1 - base) - 0.01
        lo, hi = -half, half
        for _ in range(40):
            d = (lo + hi) / 2
            m = float(win_probs(base + d, base - d, sets_won, set_games, in_tiebreak,
                                game_state, p1_serves, best_of)["match"])
            if m < tgt:
                lo = d
            else:
                hi = d
        d = (lo + hi) / 2
        pA, pB = base + d, base - d

    cur_pmf = _set_outcome_pmf(pA, pB, p1_serves, ga, gb, game_state, in_tiebreak)
    fa = _set_outcome_pmf(pA, pB, True, 0, 0, (0, 0), False)
    fb = _set_outcome_pmf(pA, pB, False, 0, 0, (0, 0), False)
    future_pmf = {}
    for d in (fa, fb):
        for k, p in d.items():
            future_pmf[k] = future_pmf.get(k, 0.0) + 0.5 * p

    scorelines, set_win, games_pmf = {}, {}, {}

    def walk(sa, sb, gtot, prob):
        if sa >= need or sb >= need:
            winner = "p1" if sa > sb else "p2"
            dropped = min(sa, sb)
            scorelines[(winner, dropped)] = scorelines.get((winner, dropped), 0.0) + prob
            games_pmf[gtot] = games_pmf.get(gtot, 0.0) + prob
            return
        idx = sa + sb
        pmf = cur_pmf if idx == (sa0 + sb0) else future_pmf
        acc = set_win.setdefault(idx, [0.0, 0.0])
        for (a_won, g), p in pmf.items():
            acc[0 if a_won else 1] += prob * p
            if a_won:
                walk(sa + 1, sb, gtot + g, prob * p)
            else:
                walk(sa, sb + 1, gtot + g, prob * p)

    walk(sa0, sb0, 0, 1.0)

    over = {}
    for X in game_thresholds:
        rem = X - completed_games
        over[X] = sum(p for g, p in games_pmf.items() if g > rem)

    # per-set winners: completed sets are certain (from the score); current + future
    # come from the walk. set index i is 0-based.
    completed = [(a, b) for a, b in _parse_score(score_str) if _is_set_complete(a, b)]
    set_list = []
    for i in range(best_of):
        if i < len(completed):
            a, b = completed[i]
            set_list.append((1.0, 0.0) if a > b else (0.0, 1.0))
        else:
            set_list.append(tuple(set_win.get(i, [0.0, 0.0])))
    return {"scorelines": scorelines, "set_win": set_list, "over_games": over}
