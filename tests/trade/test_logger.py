import csv, os, pytest
import trade.config as cfg
from trade.logger import log_trade, log_snapshot
from trade.state import MatchState

@pytest.fixture(autouse=True)
def tmp_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "LOG_DIR", str(tmp_path))
    monkeypatch.setattr(cfg, "LOG_SUFFIX", "")
    yield tmp_path

def test_trade_log_creates_file(tmp_log_dir):
    log_trade("TICK", "P1", "P2", "yes", "entry", 0.55, None, 0.65, 1.50, 0.03, None, 3.50)
    assert os.path.exists(tmp_log_dir / "trade_log.csv")

def test_trade_log_has_header_and_row(tmp_log_dir):
    log_trade("TICK", "P1", "P2", "yes", "entry", 0.55, None, 0.65, 1.50, 0.03, None, 3.50)
    with open(tmp_log_dir / "trade_log.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TICK"
    assert rows[0]["direction"] == "yes"
    assert rows[0]["event"] == "entry"

def test_trade_log_appends(tmp_log_dir):
    log_trade("TICK", "P1", "P2", "yes", "entry",     0.55, None, 0.65, 1.50, 0.03, None, 3.50)
    log_trade("TICK", "P1", "P2", "yes", "stop_loss", 0.55, 0.46, 0.65, 1.50, 0.01, -0.27, 2.00)
    with open(tmp_log_dir / "trade_log.csv") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2

def _stats(first_in, win_first, win_second, return_first, return_second):
    d = {"first_in": first_in, "win_first": win_first, "win_second": win_second,
         "return_first": return_first, "return_second": return_second}
    for k in list(d):
        d[k + "_num"] = 0
        d[k + "_den"] = 0
    return d

def _snap(ms, pos_side=None, pos_value=None):
    p1 = _stats(0.6, 0.7, 0.5, 0.3, 0.45)
    p2 = _stats(0.6, 0.65, 0.48, 0.28, 0.42)
    log_snapshot("TICK", "P1", "P2", "3-2", "40-15", "p1", p1, p2, 6, 4,
                 0.62, 0.58, 0.72, 0.54, 0.52, 0.47, 0.45, ms, pos_side, pos_value)

def test_snapshot_log_creates_file(tmp_log_dir):
    _snap(MatchState(budget_remaining=5.0))
    assert os.path.exists(tmp_log_dir / "match_snapshots.csv")

def test_snapshot_log_has_all_columns(tmp_log_dir):
    _snap(MatchState(budget_remaining=5.0))
    with open(tmp_log_dir / "match_snapshots.csv") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["p1_first_serve_won_pct"] == "0.7"
    assert rows[0]["mc_prob_p1"] == "0.62"
    assert rows[0]["kalshi_p1_ask"] == "0.54"
    assert rows[0]["kalshi_p2_bid"] == "0.45"
    assert rows[0]["position_side"] == ""
    assert rows[0]["budget_remaining"] == "5.0"

def test_snapshot_log_position_columns(tmp_log_dir):
    ms = MatchState(budget_remaining=3.0)
    ms.position = {"ticker": "TICK-P2", "entry_price": 0.40, "count": 5.0,
                   "entry_time": 0.0, "high_water": 0.50}
    _snap(ms, pos_side="p2", pos_value=0.45)
    with open(tmp_log_dir / "match_snapshots.csv") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["position_side"] == "p2"
    assert rows[0]["position_entry_price"] == "0.4"
    assert rows[0]["position_high_water"] == "0.5"
    assert rows[0]["position_current_value"] == "0.45"
    assert rows[0]["position_unrealized_pnl"] == "0.25"

def test_timestamp_is_readable(tmp_log_dir):
    log_trade("TICK", "P1", "P2", "p1", "entry", 0.55, None, 0.65, 1.50, 0.03, None, 3.50)
    with open(tmp_log_dir / "trade_log.csv") as f:
        ts = list(csv.DictReader(f))[0]["timestamp"]
    assert "T" not in ts and "." not in ts and len(ts) == 19
