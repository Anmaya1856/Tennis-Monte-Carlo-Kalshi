"""_game_id defines when a position is squared off."""
from trade.trade_bot import _game_id


def st(score, p1_serves):
    return {"score_str": score, "p1_serves": p1_serves}


def test_normal_game_id_is_just_the_score():
    assert _game_id(st("3-2", True), 3) == "3-2"
    assert _game_id(st("6-4 2-2", False), 3) == "6-4 2-2"


def test_server_does_not_split_a_normal_game():
    """Outside a tiebreak the server is constant within a game, so folding it in
    would only expose us to a lagging server field. It must not change the id."""
    assert _game_id(st("3-2", True), 3) == _game_id(st("3-2", False), 3)


def test_game_id_changes_when_a_game_ends():
    assert _game_id(st("2-2", True), 3) != _game_id(st("3-2", False), 3)


def test_tiebreak_id_includes_the_server():
    a = _game_id(st("6-6", True), 3)
    b = _game_id(st("6-6", False), 3)
    assert a != b
    assert a.startswith("6-6|") and b.startswith("6-6|")


def test_tiebreak_holds_within_one_service_block():
    """Serve rotates every two points; the id must be stable across the two
    points of a block so we do not churn mid-block."""
    assert _game_id(st("6-6", False), 3) == _game_id(st("6-6", False), 3)


def test_tiebreak_rolls_on_each_service_rotation():
    # p1 serves pt 1; p2 serves pts 2-3; p1 serves pts 4-5; ...
    servers = [True, False, False, True, True, False, False, True]
    ids = [_game_id(st("6-6", s), 3) for s in servers]
    rolls = sum(1 for x, y in zip(ids, ids[1:]) if x != y)
    assert rolls == 4          # one exit+re-entry per rotation, not per point


def test_tiebreak_id_changes_when_the_set_completes():
    during = _game_id(st("6-6", True), 3)
    after = _game_id(st("7-6 0-0", False), 3)
    assert during != after


def test_second_set_tiebreak_also_rolls():
    a = _game_id(st("6-4 6-6", True), 3)
    b = _game_id(st("6-4 6-6", False), 3)
    assert a != b


# ── what on_serve is judged on ────────────────────────────────────────────────
from trade.trade_bot import _serve_score


def test_serve_score_is_games_outside_a_tiebreak():
    assert _serve_score("4-3", "30-15", 3) == (4, 3)
    assert _serve_score("6-4 2-2", "40-40", 3) == (2, 2)


def test_serve_score_switches_to_points_in_a_tiebreak():
    """The whole fix: at 6-6 the games are level forever, so judging on games
    called every tiebreak point on-serve no matter how far down we were.
    Pins TRADE_TIEBREAKS on, since this tests the tiebreak logic itself."""
    import trade.config as _cfg
    old = _cfg.TRADE_TIEBREAKS
    try:
        _cfg.TRADE_TIEBREAKS = True
        assert _serve_score("6-6", "5-2", 3) == (5, 2)
        assert _serve_score("6-4 6-6", "0-3", 3) == (0, 3)
    finally:
        _cfg.TRADE_TIEBREAKS = old


def test_serve_score_survives_a_malformed_point_score():
    assert _serve_score("6-6", "not-a-score", 3) is None
    assert _serve_score("6-6", None, 3) is None


# ── tiebreak kill switch ──────────────────────────────────────────────────────
import trade.config as cfg


def test_tiebreaks_are_skipped_when_disabled():
    """TRADE_TIEBREAKS off -> no serve score -> on_serve False -> never enters.
    The logic is kept, not deleted; this only gates it."""
    from trade.decision import on_serve
    old = cfg.TRADE_TIEBREAKS
    try:
        cfg.TRADE_TIEBREAKS = False
        for pts in ("0-0", "2-1", "5-5", "6-5"):
            assert _serve_score("6-6", pts, 3) is None
            assert on_serve(_serve_score("6-6", pts, 3), True) is False
    finally:
        cfg.TRADE_TIEBREAKS = old


def test_the_tiebreak_logic_still_works_when_re_enabled():
    from trade.decision import on_serve
    old = cfg.TRADE_TIEBREAKS
    try:
        cfg.TRADE_TIEBREAKS = True
        assert _serve_score("6-6", "5-2", 3) == (5, 2)
        assert on_serve(_serve_score("6-6", "2-2", 3), True) is True    # on serve
        assert on_serve(_serve_score("6-6", "5-2", 3), False) is False  # broken
    finally:
        cfg.TRADE_TIEBREAKS = old


def test_normal_games_are_unaffected_by_the_switch():
    old = cfg.TRADE_TIEBREAKS
    try:
        for flag in (True, False):
            cfg.TRADE_TIEBREAKS = flag
            assert _serve_score("4-3", "30-15", 3) == (4, 3)
            assert _serve_score("6-4 2-2", "40-40", 3) == (2, 2)
    finally:
        cfg.TRADE_TIEBREAKS = old
