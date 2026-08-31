"""Interactive point-by-point match simulator.

Usage:
    python simulation/sim_interactive.py [pA] [pB] [best_of]

At each point, press:
    Enter   — random outcome (using the correct serve probability)
    a       — A wins the point
    b       — B wins the point
    q       — quit
"""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from trade.exact import win_probs, game_win_prob, _tb, _tb_first_server
import trade.config as cfg

cfg.BP_PRESSURE = 0.0  # clean probabilities for illustration

# ── parameters ────────────────────────────────────────────────────────────────
PA      = float(sys.argv[1]) if len(sys.argv) > 1 else 0.66
PB      = float(sys.argv[2]) if len(sys.argv) > 2 else 0.62
BEST_OF = int(sys.argv[3])   if len(sys.argv) > 3 else 3


# ── helpers ───────────────────────────────────────────────────────────────────
NEED = BEST_OF // 2 + 1
_SCORE = {0: "0", 1: "15", 2: "30", 3: "40"}


def _game_over(a, b, in_tb):
    if in_tb:
        return (a >= 7 and a - b >= 2) or (b >= 7 and b - a >= 2)
    return (a >= 4 and a - b >= 2) or (b >= 4 and b - a >= 2)


def _game_score_str(a, b, in_tb, p1_serves):
    # (a, b) = (p1_pts, p2_pts) always; show A – B throughout, serve label is elsewhere
    if in_tb:
        return f"A {a} – {b} B  (TB)"
    if a >= 3 and b >= 3:
        if a == b:  return "Deuce"
        elif a > b: return "Ad A"
        else:       return "Ad B"
    return f"A {_SCORE[a]} – {_SCORE[b]} B"


def _who_serves_tb_point(p1_started_tb, a, b):
    """Who serves this specific tiebreak point? Returns True if A/p1."""
    n = a + b
    first_A = p1_started_tb
    return first_A if ((n + 1) // 2) % 2 == 0 else not first_A


def _probs(sets, set_games, game_state, p1_serves, in_tb):
    """Return match prob + win/lose conditionals at every level.

    Point-level conditionals derived from g_w/g_l (game-win prob after winning/
    losing the next point) exactly as win_probs does internally — no extra
    win_probs calls, which avoids redundant _set_vec recursion.
    """
    sa, sb = sets
    ga, gb = set_games
    a, b   = game_state
    pA_arr = np.array([PA])
    pB_arr = np.array([PB])

    r = win_probs(pA_arr, pB_arr, (sa, sb), (ga, gb), in_tb, (a, b), p1_serves, BEST_OF)
    match_p = float(r["match"])
    cond_wg = float(r["cond"]["win_game"])
    cond_lg = float(r["cond"]["lose_game"])
    cond_ws = float(r["cond"]["win_set"])
    cond_ls = float(r["cond"]["lose_set"])

    # g_w/g_l: p1's game-win probability from the state AFTER winning/losing
    # the next point.  This mirrors win_probs lines 251-260 exactly.
    if in_tb:
        first_A = _tb_first_server(a, b, p1_serves)
        g_w = float(_tb(pA_arr, pB_arr, a + 1, b,     first_A, {}))
        g_l = float(_tb(pA_arr, pB_arr, a,     b + 1, first_A, {}))
    elif p1_serves:
        g_w = float(game_win_prob(pA_arr, a + 1, b))
        g_l = float(game_win_prob(pA_arr, a,     b + 1))
    else:
        # p2 serves: game_win_prob(pB, server_pts=b, receiver_pts=a) → p2 wins game
        # g_w: p1 wins point → receiver (p1) gains → (a+1, b) in p1/p2 terms
        #   server_pts stays b, receiver_pts becomes a+1
        g_w = float(1 - game_win_prob(pB_arr, b, a + 1))
        g_l = float(1 - game_win_prob(pB_arr, b + 1, a))

    # P(A wins match | A wins/loses this point) = weighted avg over game outcome
    cond_wp = g_w * cond_wg + (1 - g_w) * cond_lg
    cond_lp = g_l * cond_wg + (1 - g_l) * cond_lg

    return match_p, cond_wg, cond_lg, cond_ws, cond_ls, cond_wp, cond_lp


def _bar(v, width=20):
    filled = round(v * width)
    return "█" * filled + "░" * (width - filled)


def _clear():
    os.system("cls" if os.name == "nt" else "clear")


def _print_state(sets, set_games, game_state, p1_serves, in_tb, history):
    _clear()
    sa, sb = sets
    ga, gb = set_games

    print(f"  pA={PA:.3f}  pB={PB:.3f}  Best-of-{BEST_OF}")
    print(f"  Match:  A {sa} – {sb} B")
    print(f"  Set {sa+sb+1}:  A {ga} – {gb} B")
    srv_lbl = "A serves" if p1_serves else "B serves"
    gs = _game_score_str(*game_state, in_tb, p1_serves)
    print(f"  Game:   {gs}   ({srv_lbl})")
    print()

    mp, cwg, clg, cws, cls, cwp, clp = _probs(sets, set_games, game_state, p1_serves, in_tb)

    print(f"  P(A wins match)  {mp*100:5.1f}%   {_bar(mp)}")
    print()
    print(f"  {'':4s}  {'Win →':>10s}  {'Lose →':>10s}  {'Swing':>8s}")
    print(f"  Point  {cwp*100:8.1f}%  {clp*100:8.1f}%  {(cwp-clp)*100:6.1f}pp")
    print(f"  Game   {cwg*100:8.1f}%  {clg*100:8.1f}%  {(cwg-clg)*100:6.1f}pp")
    print(f"  Set    {cws*100:8.1f}%  {cls*100:8.1f}%  {(cws-cls)*100:6.1f}pp")
    print()

    # last 6 points
    if history:
        print("  Recent points:")
        for h in history[-6:]:
            winner_lbl = "A" if h["p1_won"] else "B"
            print(f"    {h['game_score']:14s}  → {winner_lbl} wins  "
                  f"({h['match_p_before']*100:.1f}% → {h['match_p_after']*100:.1f}%)")
        print()

    print("  [Enter]=random  [a]=A wins  [b]=B wins  [q]=quit")


# ── main loop ─────────────────────────────────────────────────────────────────
def main():
    sets = [0, 0]
    p1_serves = random.choice([True, False])
    history = []

    while max(sets) < NEED:
        set_games = [0, 0]

        while True:
            ga, gb = set_games
            if (ga >= 6 and ga - gb >= 2) or (ga == 7 and gb == 6) or (gb == 7 and ga == 6):
                break

            in_tb = (ga == 6 and gb == 6)
            game_state = [0, 0]
            p1_started_tb = p1_serves  # who served the first TB point

            while True:
                a, b = game_state
                _print_state(tuple(sets), tuple(set_games), tuple(game_state),
                             p1_serves, in_tb, history)

                mp_before = _probs(tuple(sets), tuple(set_games), tuple(game_state),
                                   p1_serves, in_tb)[0]
                gs_label = _game_score_str(a, b, in_tb, p1_serves)

                cmd = input("  > ").strip().lower()
                if cmd == "q":
                    print("\n  Quit.")
                    return
                elif cmd == "a":
                    p1_wins = True
                elif cmd == "b":
                    p1_wins = False
                else:
                    # random: correct serve probability
                    if in_tb:
                        A_serves_now = _who_serves_tb_point(p1_started_tb, a, b)
                        p_p1 = PA if A_serves_now else (1 - PB)
                    else:
                        p_p1 = PA if p1_serves else (1 - PB)
                    p1_wins = random.random() < p_p1

                # advance game state
                if in_tb:
                    game_state[0 if p1_wins else 1] += 1
                    a, b = game_state
                    if _game_over(a, b, True):
                        set_games[0 if a > b else 1] += 1
                        p1_serves = not p1_serves
                        game_over = True
                    else:
                        game_over = False
                else:
                    # game_state is always (p1_pts, p2_pts); p1 winning always increments [0]
                    game_state[0 if p1_wins else 1] += 1
                    a, b = game_state
                    if _game_over(a, b, False):
                        set_games[0 if a > b else 1] += 1
                        p1_serves = not p1_serves
                        game_over = True
                    else:
                        game_over = False

                mp_after = _probs(tuple(sets), tuple(set_games), tuple(game_state),
                                  p1_serves, False)[0] if game_over else \
                           _probs(tuple(sets), tuple(set_games), tuple(game_state),
                                  p1_serves, in_tb)[0]

                history.append({
                    "game_score":    gs_label,
                    "p1_won":        p1_wins,
                    "match_p_before": mp_before,
                    "match_p_after":  mp_after,
                })

                if game_over:
                    break
            # end points loop
        # end games loop

        ga, gb = set_games
        sets[0 if ga > gb else 1] += 1
    # end sets loop

    _clear()
    winner = "A" if sets[0] > sets[1] else "B"
    print(f"\n  Match over!  {winner} wins  ({sets[0]}–{sets[1]} sets)\n")
    print("  Last 10 points:")
    for h in history[-10:]:
        w = "A" if h["p1_won"] else "B"
        print(f"    {h['game_score']:14s}  → {w} wins  "
              f"({h['match_p_before']*100:.1f}% → {h['match_p_after']*100:.1f}%)")
    print()


if __name__ == "__main__":
    main()
