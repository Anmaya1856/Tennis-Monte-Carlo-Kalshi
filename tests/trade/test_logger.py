import csv, os, pytest
import trade.config as cfg
from trade.logger import log_trade, log_snapshot

@pytest.fixture(autouse=True)
def tmp_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "LOG_DIR", str(tmp_path))
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

def test_snapshot_log_creates_file(tmp_log_dir):
    p1 = {"first_in": 0.6, "win_first": 0.7, "win_second": 0.5, "return_first": 0.3, "return_second": 0.45}
    p2 = {"first_in": 0.6, "win_first": 0.65, "win_second": 0.48, "return_first": 0.28, "return_second": 0.42}
    log_snapshot("TICK", "P1", "P2", "3-2", "40-15", "p1", p1, p2, 0.62, 0.54, 0.52)
    assert os.path.exists(tmp_log_dir / "match_snapshots.csv")

def test_snapshot_log_has_all_columns(tmp_log_dir):
    p1 = {"first_in": 0.6, "win_first": 0.7, "win_second": 0.5, "return_first": 0.3, "return_second": 0.45}
    p2 = {"first_in": 0.6, "win_first": 0.65, "win_second": 0.48, "return_first": 0.28, "return_second": 0.42}
    log_snapshot("TICK", "P1", "P2", "3-2", "40-15", "p1", p1, p2, 0.62, 0.54, 0.52)
    with open(tmp_log_dir / "match_snapshots.csv") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["p1_first_serve_won_pct"] == "0.7"
    assert rows[0]["mc_prob_p1"] == "0.62"
    assert rows[0]["kalshi_yes_ask"] == "0.54"
