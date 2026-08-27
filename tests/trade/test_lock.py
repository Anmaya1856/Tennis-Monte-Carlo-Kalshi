import os, time
import pytest
import trade.config as cfg
from trade.trade_bot import claim_match, _heartbeat, _release, _lock_path


@pytest.fixture(autouse=True)
def tmp_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "LOG_DIR", str(tmp_path))
    yield tmp_path


def test_first_bot_claims_the_match():
    assert claim_match("EV-A") is True
    assert os.path.exists(_lock_path("EV-A"))


def test_second_bot_is_refused():
    assert claim_match("EV-A") is True
    assert claim_match("EV-A") is False          # duplicate must not start


def test_different_matches_do_not_block_each_other():
    assert claim_match("EV-A") is True
    assert claim_match("EV-B") is True


def test_a_stale_lock_is_reclaimed(monkeypatch):
    """A bot that died leaves its lock behind; the next one must take over."""
    assert claim_match("EV-A") is True
    old = time.time() - cfg.BOT_LOCK_STALE_SECS - 5
    os.utime(_lock_path("EV-A"), (old, old))
    assert claim_match("EV-A") is True


def test_heartbeat_keeps_the_lock_alive():
    assert claim_match("EV-A") is True
    old = time.time() - cfg.BOT_LOCK_STALE_SECS - 5
    os.utime(_lock_path("EV-A"), (old, old))
    _heartbeat("EV-A")                            # bot is alive and ticking
    assert claim_match("EV-A") is False


def test_release_frees_the_match():
    assert claim_match("EV-A") is True
    _release("EV-A")
    assert not os.path.exists(_lock_path("EV-A"))
    assert claim_match("EV-A") is True


def test_release_is_safe_when_no_lock_exists():
    _release("NEVER-CLAIMED")                     # must not raise
