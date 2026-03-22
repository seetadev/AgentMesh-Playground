"""SQLite persistence for transaction logs."""

import sqlite3
import json
from datetime import datetime, UTC
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _get_db(name: str) -> sqlite3.Connection:
    DB_DIR.mkdir(exist_ok=True)
    db_path = DB_DIR / f"{name}.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_alpha_db() -> sqlite3.Connection:
    """Initialize the Alpha Agent's database."""
    conn = _get_db("alpha_agent")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            peer_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            tx_hash TEXT,
            amount TEXT,
            payer_address TEXT,
            direction TEXT,
            confidence INTEGER,
            status TEXT NOT NULL,
            error TEXT
        )
    """)
    conn.commit()
    return conn


def init_trader_db() -> sqlite3.Connection:
    """Initialize the Trading Agent's database."""
    conn = _get_db("trading_agent")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            alpha_peer_id TEXT NOT NULL,
            asset TEXT NOT NULL,
            tx_hash TEXT,
            amount TEXT,
            direction TEXT,
            confidence INTEGER,
            price REAL,
            status TEXT NOT NULL,
            error TEXT
        )
    """)
    conn.commit()
    return conn


def log_alpha_tx(conn: sqlite3.Connection, *, peer_id: str, asset: str,
                 tx_hash: str = None, amount: str = None, payer_address: str = None,
                 direction: str = None, confidence: int = None,
                 status: str, error: str = None) -> None:
    """Log a transaction on the Alpha Agent side."""
    conn.execute(
        """INSERT INTO transactions
           (timestamp, peer_id, asset, tx_hash, amount, payer_address, direction, confidence, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(UTC).isoformat(), peer_id, asset, tx_hash, amount,
         payer_address, direction, confidence, status, error),
    )
    conn.commit()


def log_trader_tx(conn: sqlite3.Connection, *, alpha_peer_id: str, asset: str,
                  tx_hash: str = None, amount: str = None,
                  direction: str = None, confidence: int = None, price: float = None,
                  status: str, error: str = None) -> None:
    """Log a transaction on the Trading Agent side."""
    conn.execute(
        """INSERT INTO transactions
           (timestamp, alpha_peer_id, asset, tx_hash, amount, direction, confidence, price, status, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.now(UTC).isoformat(), alpha_peer_id, asset, tx_hash, amount,
         direction, confidence, price, status, error),
    )
    conn.commit()


def get_recent_transactions(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Get recent transactions."""
    rows = conn.execute(
        "SELECT * FROM transactions ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(row) for row in rows]
