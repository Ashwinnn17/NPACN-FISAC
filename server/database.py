"""
server/database.py
──────────────────
Thread-safe SQLite wrapper with WAL mode.

Tables:
    stock_prices   — historical OHLCV data
    connections    — connect/disconnect syslog
    auth_events    — auth success/failure/blocked log
    socket_metrics — performance counters
"""

import datetime
import os
import sqlite3
import threading

from .logger import log


class Database:
    """
    Thread-safe SQLite wrapper.

    Uses WAL (Write-Ahead Logging) for concurrent read-write access so that
    the high-frequency price insert loop never blocks client reads.
    Per-thread connections avoid the need for a connection pool.
    """

    def __init__(self, path: str = "db/stocks.db"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._path  = path
        self._local = threading.local()
        self._lock  = threading.Lock()
        self._init_schema()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                self._path, check_same_thread=False, isolation_level=None
            )
            # WAL: readers never block writers — critical for high-frequency inserts
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            # NORMAL sync: crash-safe but not power-loss-safe (acceptable for logs)
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA cache_size=-8000")  # 8 MB page cache
        return self._local.conn

    def _init_schema(self):
        with self._lock:
            self._conn().executescript("""
                CREATE TABLE IF NOT EXISTS stock_prices (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol     TEXT NOT NULL,
                    price      REAL NOT NULL,
                    open_price REAL,
                    high_price REAL,
                    low_price  REAL,
                    volume     INTEGER DEFAULT 0,
                    change_pct REAL,
                    timestamp  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sym_ts
                    ON stock_prices(symbol, timestamp);

                CREATE TABLE IF NOT EXISTS connections (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    event       TEXT NOT NULL,
                    client_ip   TEXT,
                    client_port INTEGER,
                    client_id   TEXT,
                    detail      TEXT,
                    timestamp   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS auth_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    event      TEXT NOT NULL,
                    client_ip  TEXT,
                    token_hash TEXT,
                    reason     TEXT,
                    timestamp  TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS socket_metrics (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    metric_name TEXT NOT NULL,
                    metric_val  TEXT,
                    timestamp   TEXT NOT NULL
                );
            """)

    @staticmethod
    def _ts() -> str:
        return datetime.datetime.utcnow().isoformat()

    # ── Write operations ──────────────────────────────────────────────────────

    def insert_price(self, sym, price, open_p, high, low, vol, chg):
        with self._lock:
            self._conn().execute(
                "INSERT INTO stock_prices "
                "(symbol,price,open_price,high_price,low_price,volume,change_pct,timestamp) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (sym, price, open_p, high, low, vol, chg, self._ts()),
            )

    def log_connection(self, event, ip, port, cid=None, detail=None):
        with self._lock:
            self._conn().execute(
                "INSERT INTO connections "
                "(event,client_ip,client_port,client_id,detail,timestamp) "
                "VALUES(?,?,?,?,?,?)",
                (event, ip, port, cid, detail, self._ts()),
            )

    def log_auth(self, event, ip, tok_hash=None, reason=None):
        with self._lock:
            self._conn().execute(
                "INSERT INTO auth_events "
                "(event,client_ip,token_hash,reason,timestamp) "
                "VALUES(?,?,?,?,?)",
                (event, ip, tok_hash, reason, self._ts()),
            )

    def log_metric(self, name, value):
        with self._lock:
            self._conn().execute(
                "INSERT INTO socket_metrics (metric_name,metric_val,timestamp) "
                "VALUES(?,?,?)",
                (name, str(value), self._ts()),
            )

    # ── Read operations ───────────────────────────────────────────────────────

    def get_history(self, symbol: str, limit: int = 30) -> list:
        with self._lock:
            cur = self._conn().execute(
                "SELECT price,change_pct,timestamp FROM stock_prices "
                "WHERE symbol=? ORDER BY id DESC LIMIT ?",
                (symbol, limit),
            )
            return [{"price": r[0], "change_pct": r[1], "ts": r[2]}
                    for r in cur.fetchall()]

    def get_stats(self) -> dict:
        with self._lock:
            c = self._conn()
            return {
                "price_records": c.execute(
                    "SELECT COUNT(*) FROM stock_prices").fetchone()[0],
                "connections":   c.execute(
                    "SELECT COUNT(*) FROM connections WHERE event='CONNECT'").fetchone()[0],
                "auth_failures": c.execute(
                    "SELECT COUNT(*) FROM auth_events WHERE event='FAILURE'").fetchone()[0],
                "auth_blocked":  c.execute(
                    "SELECT COUNT(*) FROM auth_events WHERE event='BLOCKED'").fetchone()[0],
            }


# Module-level singleton — import `db` from here everywhere
db = Database()
