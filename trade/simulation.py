import numpy as np


def _sample_stats(stats):
    sampled = {}
    for key, num_key, den_key in [
        ("first_in",      "first_in_num",      "first_in_den"),
        ("win_first",     "win_first_num",      "win_first_den"),
        ("win_second",    "win_second_num",     "win_second_den"),
        ("return_first",  "return_first_num",   "return_first_den"),
        ("return_second", "return_second_num",  "return_second_den"),
    ]:
        num = stats.get(num_key)
        den = stats.get(den_key)
        if num is not None and den is not None:
            sampled[key] = np.random.beta(num + 0.5, (den - num) + 0.5)
        else:
            sampled[key] = stats[key]
    return sampled


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


def _sim_point(p1_serving, p1_stats, p2_stats):
    server, opp = (p1_stats, p2_stats) if p1_serving else (p2_stats, p1_stats)
    p_win_1st = (server['win_first']  + (1 - opp['return_first']))  / 2
    p_win_2nd = (server['win_second'] + (1 - opp['return_second'])) / 2
    if np.random.random() < server['first_in']:
        server_won = np.random.random() < p_win_1st
    else:
        server_won = np.random.random() < p_win_2nd
    return server_won if p1_serving else not server_won

def _sim_game(p1_serving, p1_stats, p2_stats, start_score=(0, 0)):
    score = list(start_score)
    while True:
        p1_won = _sim_point(p1_serving, p1_stats, p2_stats)
        score[0 if p1_won else 1] += 1
        if score[0] >= 4 and score[0] - score[1] >= 2: return True
        if score[1] >= 4 and score[1] - score[0] >= 2: return False

def _sim_tiebreak(p1_serves_first, p1_stats, p2_stats, start_score=(0, 0)):
    score = list(start_score)
    point_count = score[0] + score[1]
    while True:
        # Rule: first server serves 1 point, then players alternate every 2 points.
        # Pattern: A, B,B, A,A, B,B, A,A, ...
        # For point n>=1: number of 2-point blocks elapsed = (n+1)//2
        if point_count == 0:
            p1_serves = p1_serves_first
        else:
            switches = (point_count + 1) // 2
            p1_serves = p1_serves_first if switches % 2 == 0 else not p1_serves_first
        p1_won = _sim_point(p1_serves, p1_stats, p2_stats)
        score[0 if p1_won else 1] += 1
        point_count += 1
        if score[0] >= 7 and score[0] - score[1] >= 2: return True
        if score[1] >= 7 and score[1] - score[0] >= 2: return False

def _sim_set(p1_serving, p1_stats, p2_stats, start_games=(0, 0), first_game_score=(0, 0)):
    games = list(start_games)
    first_game = True
    while True:
        score = first_game_score if first_game else (0, 0)
        first_game = False
        if games[0] == 6 and games[1] == 6:
            # p1_serving is the CURRENT tiebreak server, not necessarily point-1 server.
            # Invert the tiebreak formula to recover who actually served point 1.
            N = score[0] + score[1]
            p1_serves_tb_first = p1_serving if (N + 1) // 2 % 2 == 0 else not p1_serving
            p1_won_tb = _sim_tiebreak(p1_serves_tb_first, p1_stats, p2_stats, score)
            games[0 if p1_won_tb else 1] += 1
            # Rule: tiebreak server receives in next set → flip
            return (games[0] > games[1]), not p1_serves_tb_first
        p1_won_game = _sim_game(p1_serving, p1_stats, p2_stats, score)
        games[0 if p1_won_game else 1] += 1
        p1_serving = not p1_serving
        if games[0] == 6 and games[1] == 6:
            p1_won_tb = _sim_tiebreak(p1_serving, p1_stats, p2_stats)
            games[0 if p1_won_tb else 1] += 1
            # Rule: tiebreak server receives in next set → flip
            return (games[0] > games[1]), not p1_serving
        if games[0] >= 6 and games[0] - games[1] >= 2: return True,  p1_serving
        if games[1] >= 6 and games[1] - games[0] >= 2: return False, p1_serving

def _sim_match_once(sets_won, current_set_games, first_game_score, p1_serving,
                    best_of, p1_stats, p2_stats):
    sets_needed = best_of // 2 + 1
    sets = list(sets_won)
    first_set_games = current_set_games if current_set_games is not None else (0, 0)
    p1_won_set, p1_serving = _sim_set(p1_serving, p1_stats, p2_stats,
                                       first_set_games, first_game_score)
    sets[0 if p1_won_set else 1] += 1
    while sets[0] < sets_needed and sets[1] < sets_needed:
        p1_won_set, p1_serving = _sim_set(p1_serving, p1_stats, p2_stats)
        sets[0 if p1_won_set else 1] += 1
    return sets[0] > sets[1]


def estimate_win_prob(p1_stats, p2_stats, score_str, game_score_str,
                      p1_serves, best_of, n_sims=10_000):
    """Return {"match", "set", "game"} win probabilities for p1."""
    sets_won, current_set_games = _parse_match_state(score_str, best_of)
    in_tiebreak      = (current_set_games == (6, 6))
    first_game_score = _parse_game_score(game_score_str, is_tiebreak=in_tiebreak)
    set_games        = current_set_games if current_set_games is not None else (0, 0)

    if in_tiebreak:
        N = first_game_score[0] + first_game_score[1]
        p1_tb_first = p1_serves if (N + 1) // 2 % 2 == 0 else not p1_serves

    game_wins = set_wins = match_wins = 0
    for _ in range(n_sims):
        p1_s = _sample_stats(p1_stats)
        p2_s = _sample_stats(p2_stats)
        if in_tiebreak:
            game_wins += _sim_tiebreak(p1_tb_first, p1_s, p2_s, first_game_score)
        else:
            game_wins += _sim_game(p1_serves, p1_s, p2_s, first_game_score)
        set_wins   += _sim_set(p1_serves, p1_s, p2_s, set_games, first_game_score)[0]
        match_wins += _sim_match_once(sets_won, current_set_games, first_game_score,
                                      p1_serves, best_of, p1_s, p2_s)
    return {
        "match": match_wins / n_sims,
        "set":   set_wins   / n_sims,
        "game":  game_wins  / n_sims,
    }
