"""taker_fee must reproduce Kalshi's published General Trading Fees table."""
import pytest
from trade.kalshi_client import taker_fee

# (price, fee for 1 contract, fee for 100 contracts) straight off the fee schedule
TABLE = [
    (0.01, 0.01, 0.07),
    (0.05, 0.01, 0.34),
    (0.10, 0.01, 0.63),
    (0.15, 0.01, 0.90),
    (0.20, 0.02, 1.12),
    (0.25, 0.02, 1.32),
    (0.30, 0.02, 1.47),
    (0.35, 0.02, 1.60),
]


@pytest.mark.parametrize("price,fee1,fee100", TABLE)
def test_matches_published_table(price, fee1, fee100):
    assert taker_fee(1, price) == pytest.approx(fee1, abs=1e-9)
    assert taker_fee(100, price) == pytest.approx(fee100, abs=1e-9)


def test_round_up_is_per_order_not_per_contract():
    """The headline quirk: 100 contracts at 1c cost $0.07, not 100 x $0.01."""
    assert taker_fee(1, 0.01) == pytest.approx(0.01)
    assert taker_fee(100, 0.01) == pytest.approx(0.07)
    assert taker_fee(100, 0.01) < 100 * taker_fee(1, 0.01)


def test_exact_cent_values_are_not_bumped_up():
    """0.07*100*0.10*0.90 is exactly 0.63; float noise must not push it to 0.64."""
    assert taker_fee(100, 0.10) == pytest.approx(0.63, abs=1e-9)
    assert taker_fee(100, 0.20) == pytest.approx(1.12, abs=1e-9)
    assert taker_fee(100, 0.30) == pytest.approx(1.47, abs=1e-9)


def test_fee_is_always_a_whole_number_of_cents():
    for c in (1, 5, 37.5, 100):
        for p in (0.01, 0.13, 0.5, 0.66, 0.99):
            cents = taker_fee(c, p) * 100
            assert abs(cents - round(cents)) < 1e-6


def test_fee_is_symmetric_about_fifty_cents():
    assert taker_fee(50, 0.30) == taker_fee(50, 0.70)


def test_fee_peaks_at_fifty_cents():
    mid = taker_fee(100, 0.50)
    assert mid > taker_fee(100, 0.20)
    assert mid > taker_fee(100, 0.80)


def test_never_free_for_a_real_trade():
    assert taker_fee(1, 0.99) > 0


# ── execution mode ────────────────────────────────────────────────────────────
import trade.config as cfg
from trade.kalshi_client import maker_fee, fill_fee


ATP = "KXATPMATCH-26AUG25ABCDEF-ABC"
CHA = "KXATPCHALLENGERMATCH-26AUG25ABCDEF-ABC"


def test_challengers_pay_no_maker_fee():
    for c, p in ((5, 0.30), (100, 0.50), (1, 0.95)):
        assert maker_fee(c, p, CHA) == 0.0


def test_atp_main_tour_does_pay_a_maker_fee():
    # 1 * 0.0175 * 100 * 0.5 * 0.5 = 0.4375 -> next cent
    assert maker_fee(100, 0.50, ATP) == pytest.approx(0.44)
    assert maker_fee(100, 0.50, ATP) < taker_fee(100, 0.50)


def test_unknown_series_defaults_to_charged():
    """Never understate costs for a series we have not classified."""
    assert cfg.MAKER_FEE_MULTIPLIER_DEFAULT >= 1
    assert maker_fee(100, 0.50, "KXSOMETHINGNEW-26AUG25XY-X") > 0


def test_small_atp_lots_pay_a_heavy_rounding_surcharge():
    raw = 0.0175 * 5 * 0.5 * 0.5           # 2.1875c
    assert maker_fee(5, 0.50, ATP) == pytest.approx(0.03)
    assert maker_fee(5, 0.50, ATP) > raw * 1.35


def test_fill_fee_follows_the_execution_mode():
    old = cfg.MAKER_MODE
    try:
        cfg.MAKER_MODE = True
        assert fill_fee(5, 0.50, CHA) == 0.0
        assert fill_fee(5, 0.50, ATP) == pytest.approx(0.03)
        cfg.MAKER_MODE = False
        assert fill_fee(5, 0.50, CHA) == taker_fee(5, 0.50)
        assert fill_fee(5, 0.50, ATP) == taker_fee(5, 0.50)
    finally:
        cfg.MAKER_MODE = old


def test_series_of_handles_market_and_event_tickers():
    from trade.kalshi_client import series_of
    assert series_of(ATP) == "KXATPMATCH"
    assert series_of("KXATPMATCH-26AUG25ABCDEF") == "KXATPMATCH"
    assert series_of(None) == ""
