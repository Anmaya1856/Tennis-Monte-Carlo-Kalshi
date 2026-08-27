import trade.config as cfg
from trade.decision import compute_entry, on_serve

TICKER = "KXATPMATCH-26AUG25ABCDEF-ABC"


# ── on_serve gate ─────────────────────────────────────────────────────────────

def test_on_serve_when_level_or_server_one_down():
    # level: the first server of the set is about to serve
    assert on_serve((0, 0), p1_serves=True) is True
    assert on_serve((1, 1), p1_serves=False) is True
    assert on_serve((3, 3), p1_serves=True) is True
    assert on_serve((6, 6), p1_serves=False) is True      # tiebreak
    # server one game down: the second server is about to serve
    assert on_serve((1, 2), p1_serves=True) is True
    assert on_serve((2, 1), p1_serves=False) is True
    assert on_serve((5, 6), p1_serves=True) is True

def test_off_when_the_server_is_already_ahead():
    """An unbroken set never has the upcoming server leading: the first server
    leads by one only while the SECOND server is about to serve."""
    assert on_serve((2, 1), p1_serves=True) is False
    assert on_serve((1, 2), p1_serves=False) is False
    assert on_serve((6, 5), p1_serves=True) is False

def test_tiebreak_uses_points_not_games():
    """At 6-6 the games are level forever. Judged on points, the same rule works:
    an unbroken tiebreak runs 1-0, 1-1, 1-2, 2-2, 3-2 ... and the upcoming server
    is never ahead, so a mini-break always reads as off-serve."""
    # unbroken tiebreak, A served first (see the sequence above)
    assert on_serve((0, 0), p1_serves=True) is True      # p1 to serve pt1
    assert on_serve((1, 0), p1_serves=False) is True     # p2 serves pts 2-3, one down
    assert on_serve((1, 1), p1_serves=False) is True     # level, p2 still serving
    assert on_serve((1, 2), p1_serves=True) is True      # p1 serves pts 4-5, one down
    assert on_serve((2, 2), p1_serves=True) is True
    assert on_serve((3, 2), p1_serves=False) is True
    assert on_serve((5, 6), p1_serves=True) is True      # still on serve at 5-6


def test_tiebreak_mini_break_reads_as_off_serve():
    # p1 mini-breaks pt1 (p2 served): 1-0 with p2 to serve pt2 -> server ahead? no,
    # p2 has 0. The break shows up on the NEXT point instead:
    assert on_serve((2, 0), p1_serves=False) is False    # 2-pt gap
    assert on_serve((2, 1), p1_serves=True) is False     # server p1 ahead
    assert on_serve((5, 2), p1_serves=False) is False    # 3-pt gap
    assert on_serve((5, 4), p1_serves=True) is False     # server p1 ahead


def test_real_mejdra_tiebreak_states():
    """The bot traded all five service blocks; only three were on serve."""
    obs = [((0, 0), False, True), ((1, 0), True, False), ((2, 0), True, False),
           ((2, 1), False, True), ((3, 1), False, False), ((3, 2), True, False),
           ((4, 2), True, False), ((5, 2), False, False), ((5, 3), False, False),
           ((5, 4), True, False), ((5, 5), True, True), ((5, 6), False, False)]
    for pts, p1s, want in obs:
        assert on_serve(pts, p1_serves=p1s) is want, f"{pts} p1_serves={p1s}"
    assert sum(1 for _, _, w in obs if w) == 3


def test_live_maycho_case_is_off():
    # Chopra 1 - Mayo 2 with Mayo (p2) serving: Mayo leads and is about to
    # serve, so the set has been broken even though the gap is only one game.
    assert on_serve((1, 2), p1_serves=False) is False

def test_on_serve_false_once_the_gap_opens():
    for games, srv in (((3, 1), True), ((3, 1), False), ((4, 0), False), ((0, 2), True)):
        assert on_serve(games, p1_serves=srv) is False

def test_on_serve_false_when_no_set_in_progress():
    assert on_serve(None, p1_serves=True) is False

def test_on_serve_respects_config():
    assert on_serve((1, 3), p1_serves=True) is False
    cfg_backup = cfg.MAX_GAME_DIFF
    try:
        cfg.MAX_GAME_DIFF = 2
        assert on_serve((1, 3), p1_serves=True) is True     # server still behind
        assert on_serve((3, 1), p1_serves=True) is False    # but never when ahead
    finally:
        cfg.MAX_GAME_DIFF = cfg_backup


# ── fixed-size entry ──────────────────────────────────────────────────────────

def test_buys_exactly_the_configured_size():
    r = compute_entry(0.30, 5.0, TICKER)
    assert r["count"] == float(cfg.CONTRACTS_PER_TRADE)
    assert r["entry_price"] == 0.30
    assert r["price_cents"] == 30

def test_size_is_the_same_at_every_price():
    sizes = {compute_entry(p, 100.0, TICKER)["count"]
             for p in (0.05, 0.22, 0.50, 0.83, 0.95)}
    assert sizes == {float(cfg.CONTRACTS_PER_TRADE)}

def test_no_edge_test_it_always_trades_when_affordable():
    """Kelly is gone: a price the model would call terrible still trades."""
    assert compute_entry(0.99, 100.0, TICKER) is not None
    assert compute_entry(0.01, 100.0, TICKER) is not None

def test_skips_when_the_budget_cannot_cover_stake_plus_fee():
    from trade.kalshi_client import fill_fee
    n = float(cfg.CONTRACTS_PER_TRADE)
    price = 0.83
    exact = n * price + fill_fee(n, price, TICKER)
    assert compute_entry(price, exact * 0.99, TICKER) is None
    assert compute_entry(price, exact * 1.01, TICKER) is not None

def test_stake_plus_fee_fits_the_budget():
    from trade.kalshi_client import fill_fee as taker_fee
    for price in (0.05, 0.22, 0.50, 0.83, 0.95):
        r = compute_entry(price, 5.0, TICKER)
        if r is None:
            continue
        cost = r["count"] * price
        assert cost + taker_fee(r["count"], price, TICKER) <= 5.0 + 1e-9

def test_degenerate_prices_are_rejected():
    assert compute_entry(0.0, 5.0, TICKER) is None
    assert compute_entry(1.0, 5.0, TICKER) is None
