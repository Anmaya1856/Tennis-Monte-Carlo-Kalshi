"""Read-only access to the recorder DB, safe while ws_recorder.py is running.

Usage:
    from mm.db import connect_ro
    df = pd.read_sql("SELECT * FROM spreads", connect_ro())
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "orderbooks.db"


def connect_ro():
    """Read-only connection: cannot write, cannot block the recorder (WAL)."""
    return sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True, timeout=30)
