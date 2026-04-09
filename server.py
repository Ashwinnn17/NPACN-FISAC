#!/usr/bin/env python3
"""
Secure Real-Time Stock Price Tracker — WebSocket Server
Group-14 | CN Assignment

Architecture: TCP Socket -> TLS Negotiation -> HTTP Upgrade (RFC 6455)
              -> WebSocket frames -> Authenticated broadcast
"""

import asyncio, ssl, json, time, random, hashlib, logging, logging.handlers
import sqlite3, socket, threading, os, datetime
from collections import defaultdict, deque
from typing import Dict, Optional
import websockets
from websockets.exceptions import ConnectionClosed

# ─── 1. SYSLOG-STYLE ROTATING LOG ───────────────────────────────────────────
def setup_logging(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("StockServer")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                             datefmt="%Y-%m-%dT%H:%M:%S")
    ch = logging.StreamHandler()
    ch.setFormatter(fmt); ch.setLevel(logging.INFO)
    rfh = logging.handlers.RotatingFileHandler(
        f"{log_dir}/stockserver.log", maxBytes=5*1024*1024, backupCount=5)
    rfh.setFormatter(fmt); rfh.setLevel(logging.DEBUG)
    logger.addHandler(ch); logger.addHandler(rfh)
    return logger

log = setup_logging()

# ─── 2. DATABASE: SQLite with WAL mode ───────────────────────────────────────
class Database:
    """
    Thread-safe SQLite wrapper.
    Tables: stock_prices (historical OHLCV), connections (syslog),
            auth_events (security log), socket_metrics (performance log)
    Uses WAL (Write-Ahead Logging) for concurrent read-write access.
    """
    def __init__(self, path="db/stocks.db"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._path = path
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                self._path, check_same_thread=False, isolation_level=None)
            # WAL mode: readers don't block writers; critical for high-frequency inserts
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            # NORMAL sync: crash-safe but not power-loss-safe (acceptable for logs)
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA cache_size=-8000")  # 8MB page cache
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

    def _ts(self):
        return datetime.datetime.utcnow().isoformat()

    def insert_price(self, sym, price, open_p, high, low, vol, chg):
        with self._lock:
            self._conn().execute(
                "INSERT INTO stock_prices "
                "(symbol,price,open_price,high_price,low_price,volume,change_pct,timestamp) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (sym, price, open_p, high, low, vol, chg, self._ts()))

    def log_connection(self, event, ip, port, cid=None, detail=None):
        with self._lock:
            self._conn().execute(
                "INSERT INTO connections "
                "(event,client_ip,client_port,client_id,detail,timestamp) "
                "VALUES(?,?,?,?,?,?)",
                (event, ip, port, cid, detail, self._ts()))

    def log_auth(self, event, ip, tok_hash=None, reason=None):
        with self._lock:
            self._conn().execute(
                "INSERT INTO auth_events "
                "(event,client_ip,token_hash,reason,timestamp) "
                "VALUES(?,?,?,?,?)",
                (event, ip, tok_hash, reason, self._ts()))

    def log_metric(self, name, value):
        with self._lock:
            self._conn().execute(
                "INSERT INTO socket_metrics (metric_name,metric_val,timestamp) "
                "VALUES(?,?,?)",
                (name, str(value), self._ts()))

    def get_history(self, symbol, limit=30):
        with self._lock:
            cur = self._conn().execute(
                "SELECT price,change_pct,timestamp FROM stock_prices "
                "WHERE symbol=? ORDER BY id DESC LIMIT ?",
                (symbol, limit))
            return [{"price": r[0], "change_pct": r[1], "ts": r[2]}
                    for r in cur.fetchall()]

    def get_stats(self):
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

db = Database()

# ─── 3. AUTHENTICATION (Token-based + Rate Limiting) ─────────────────────────
VALID_TOKENS = {
    "TOKEN-ASHWIN-001": "ashwin",
    "TOKEN-ADMIN-002":  "admin",
    "TOKEN-DEMO-003":   "demo",
    "TOKEN-TEST-004":   "test",
}
# Per-IP sliding window: max 5 failures per 60s
BRUTE_FORCE: Dict[str, deque] = defaultdict(lambda: deque(maxlen=10))

def verify_token(token: str, client_ip: str) -> Optional[str]:
    now = time.time()
    recent = [t for t in BRUTE_FORCE[client_ip] if now - t < 60]
    if len(recent) >= 5:
        db.log_auth("BLOCKED", client_ip, reason="Rate limit exceeded")
        log.warning(f"[AUTH] {client_ip} BLOCKED — brute-force protection")
        return None
    tok_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    if token in VALID_TOKENS:
        db.log_auth("SUCCESS", client_ip, tok_hash)
        log.info(f"[AUTH] {client_ip} OK as '{VALID_TOKENS[token]}'")
        return VALID_TOKENS[token]
    BRUTE_FORCE[client_ip].append(now)
    db.log_auth("FAILURE", client_ip, tok_hash, "Invalid token")
    log.warning(f"[AUTH] {client_ip} FAILED ({len(recent)+1}/5 attempts)")
    return None

# ─── 4. STOCK PRICE ENGINE (Geometric Brownian Motion) ───────────────────────
SYMBOLS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "NFLX"]
BASE_PRICES = {
    "AAPL": 189.50, "GOOGL": 175.20, "MSFT": 415.30, "AMZN": 198.70,
    "TSLA": 175.80, "NVDA":  875.40, "META": 520.10, "NFLX": 630.25
}

class StockEngine:
    """
    Simulates stock prices via Geometric Brownian Motion:
        dS = mu * S * dt + sigma * S * dW
    where dW ~ N(0, sqrt(dt)) is a Wiener process increment.
    """
    def __init__(self):
        self.prices = dict(BASE_PRICES)
        self.open_p = dict(BASE_PRICES)
        self.highs  = dict(BASE_PRICES)
        self.lows   = dict(BASE_PRICES)
        self.vols   = {s: 0 for s in SYMBOLS}
        self._lock  = threading.Lock()

    def tick(self):
        updates = []
        with self._lock:
            for sym in SYMBOLS:
                # GBM: mu=drift (0.01% per tick), sigma=volatility (0.2% per tick)
                mu, sigma = 0.0001, 0.002
                dW = random.gauss(0, 1)
                dS = self.prices[sym] * (mu + sigma * dW)
                self.prices[sym] = max(1.0, self.prices[sym] + dS)
                p = round(self.prices[sym], 2)

                self.highs[sym] = max(self.highs[sym], p)
                self.lows[sym]  = min(self.lows[sym], p)
                self.vols[sym] += random.randint(100, 5000)
                chg = round((p - self.open_p[sym]) / self.open_p[sym] * 100, 3)

                updates.append({
                    "type":       "price_update",
                    "symbol":     sym,
                    "price":      p,
                    "open":       round(self.open_p[sym], 2),
                    "high":       round(self.highs[sym], 2),
                    "low":        round(self.lows[sym], 2),
                    "volume":     self.vols[sym],
                    "change_pct": chg,
                    "ts":         datetime.datetime.utcnow().isoformat() + "Z"
                })
                # Every tick persisted to DB for historical analysis
                db.insert_price(sym, p, self.open_p[sym],
                                self.highs[sym], self.lows[sym],
                                self.vols[sym], chg)
        return updates

engine = StockEngine()

# ─── 5. SOCKET OPTIONS CONFIGURATOR ──────────────────────────────────────────
class SocketConfigurator:
    """
    Applies and measures critical TCP/socket options.

    SO_REUSEADDR:   Allow rebinding while previous socket is in TIME_WAIT state.
                    Without this, server restart after crash fails for ~4 minutes.

    SO_SNDBUF:      Send buffer size (bytes). Kernel may double the value.
                    Larger buffer = better throughput under burst load.
                    Smaller buffer = lower latency (less bufferbloat).

    SO_RCVBUF:      Receive buffer. For stock server: client->server traffic is
                    small (control msgs), so 64KB is sufficient.

    SO_KEEPALIVE:   Enables TCP keep-alive probes. Combined with TCP_KEEPIDLE,
                    TCP_KEEPINTVL, TCP_KEEPCNT: detects dead clients without
                    application-level heartbeats.

    TCP_NODELAY:    Disables Nagle's algorithm. Nagle coalesces small packets
                    into larger ones — adds up to 200ms latency per message.
                    For real-time stock prices: MUST be disabled.
    """
    @staticmethod
    def configure(sock: socket.socket) -> dict:
        applied = {}
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            applied["SO_REUSEADDR"] = 1

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256 * 1024)
            applied["SO_SNDBUF"] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
            applied["SO_RCVBUF"] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            applied["SO_KEEPALIVE"] = 1
            if hasattr(socket, "TCP_KEEPIDLE"):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 30)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
                applied.update({
                    "TCP_KEEPIDLE": 30, "TCP_KEEPINTVL": 10, "TCP_KEEPCNT": 3
                })

            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            applied["TCP_NODELAY"] = 1

            log.info(f"[SOCKET] Options applied: {applied}")
            db.log_metric("socket_options", json.dumps(applied))
        except Exception as e:
            log.error(f"[SOCKET] Error applying options: {e}")
        return applied

# ─── 6. CLIENT MANAGER (Thread-safe subscriber registry) ─────────────────────
class ClientManager:
    """
    Manages all connected WebSocket clients.
    Uses per-client asyncio.Queue for non-blocking fan-out broadcast.
    Queue size limit (maxsize=100) implements back-pressure:
    slow clients get dropped messages rather than blocking the server.
    """
    def __init__(self):
        self._clients: Dict[str, asyncio.Queue] = {}
        self._info:    Dict[str, dict]           = {}
        self._lock = asyncio.Lock()
        self._stats = {"total_connected": 0, "msgs_sent": 0, "bytes_sent": 0}

    async def add(self, cid, queue, info):
        async with self._lock:
            self._clients[cid] = queue
            self._info[cid]    = info
            self._stats["total_connected"] += 1
        log.info(f"[CM] +{cid} ({info.get('username')}) | active={len(self._clients)}")
        db.log_connection("CONNECT", info.get("ip"), info.get("port"), cid)

    async def remove(self, cid):
        async with self._lock:
            info = self._info.pop(cid, {})
            self._clients.pop(cid, None)
        log.info(f"[CM] -{cid} | active={len(self._clients)}")
        db.log_connection("DISCONNECT", info.get("ip"), info.get("port"), cid)

    async def broadcast(self, updates):
        msg  = json.dumps({"type": "batch", "updates": updates,
                           "srv_ts": time.time()})
        blen = len(msg.encode())
        async with self._lock:
            targets = list(self._clients.items())
        dropped = 0
        for cid, q in targets:
            try:
                q.put_nowait(msg)
                self._stats["msgs_sent"]  += 1
                self._stats["bytes_sent"] += blen
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            log.warning(f"[CM] {dropped}/{len(targets)} clients slow — dropped")

    def count(self): return len(self._clients)

    def snapshot(self):
        s = dict(self._stats)
        s["active_clients"] = [
            {"id": cid, "user": info.get("username"), "ip": info.get("ip"),
             "since": info.get("connected_at")}
            for cid, info in self._info.items()
        ]
        return s

manager = ClientManager()

# ─── 7. WEBSOCKET HANDLER ─────────────────────────────────────────────────────
async def handle_client(websocket):
    """
    Per-client coroutine. TCP handshake is complete before this is called.
    WebSocket upgrade (HTTP 101 Switching Protocols) is handled by the library.

    Lifecycle:
      [TCP SYN/SYN-ACK/ACK] -> [HTTP Upgrade] -> [Auth] -> [Subscribe] -> [Stream]
    """
    ip, port = websocket.remote_address[:2]
    cid = f"{ip}:{port}"
    log.info(f"[WS] New connection: {cid}")

    # Phase 1: Authentication
    try:
        raw   = await asyncio.wait_for(websocket.recv(), timeout=10.0)
        data  = json.loads(raw)
        token = data.get("token", "")
    except asyncio.TimeoutError:
        log.warning(f"[WS] {cid} auth timeout")
        db.log_connection("ERROR", ip, port, cid, "Auth timeout")
        await websocket.close(1008, "Auth timeout"); return
    except Exception as e:
        log.warning(f"[WS] {cid} auth error: {e}")
        await websocket.close(1008, "Auth error"); return

    username = verify_token(token, ip)
    if not username:
        await websocket.send(json.dumps({
            "type": "auth", "status": "fail",
            "reason": "Invalid credentials or rate-limited"
        }))
        await websocket.close(1008, "Unauthorized"); return

    await websocket.send(json.dumps({
        "type":        "auth",
        "status":      "ok",
        "username":    username,
        "symbols":     SYMBOLS,
        "server_time": datetime.datetime.utcnow().isoformat() + "Z"
    }))

    # Phase 2: Register client
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    info = {
        "username": username, "ip": ip, "port": port,
        "connected_at": datetime.datetime.utcnow().isoformat()
    }
    await manager.add(cid, q, info)

    # Phase 3: Send historical data immediately on connect
    hist = {sym: db.get_history(sym, 30) for sym in SYMBOLS}
    await websocket.send(json.dumps({"type": "history", "data": hist}))

    # Phase 4: Concurrent send/receive
    async def sender():
        """Drain per-client queue -> WebSocket.
        websockets library handles: framing, masking, partial writes internally."""
        while True:
            msg = await q.get()
            try:
                await websocket.send(msg)
            except ConnectionClosed:
                break

    async def receiver():
        """Handle client control messages."""
        async for raw in websocket:
            try:
                cmd = json.loads(raw)
                t   = cmd.get("type")
                if t == "ping":
                    await websocket.send(json.dumps({"type": "pong", "ts": time.time()}))
                elif t == "stats":
                    snap = manager.snapshot()
                    snap["db"] = db.get_stats()
                    await websocket.send(json.dumps({"type": "stats", "data": snap}))
                elif t == "history":
                    sym  = cmd.get("symbol", "AAPL")
                    hist = db.get_history(sym, 60)
                    await websocket.send(json.dumps({
                        "type": "history_single", "symbol": sym, "data": hist}))
            except json.JSONDecodeError:
                pass

    # Run both coroutines; if either ends (client disconnected), cancel the other
    st = asyncio.create_task(sender())
    rt = asyncio.create_task(receiver())
    try:
        _, pending = await asyncio.wait([st, rt],
                                         return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except Exception as e:
        log.error(f"[WS] {cid} error: {e}")
        db.log_connection("ERROR", ip, port, cid, str(e))
    finally:
        await manager.remove(cid)
        log.info(f"[WS] {username}@{cid} fully disconnected")

# ─── 8. BROADCAST LOOP ────────────────────────────────────────────────────────
async def broadcast_loop(interval: float = 1.0):
    """
    Produces price ticks at 1 Hz and fans out to all connected clients.
    Adaptive sleep compensates for DB write time to maintain target interval.
    """
    log.info(f"[BROADCAST] Starting at {1/interval:.1f} Hz")
    tick = 0
    while True:
        t0 = time.monotonic()
        updates = engine.tick()
        await manager.broadcast(updates)
        tick += 1

        if tick % 60 == 0:
            s = manager.snapshot()
            log.info(f"[TICK={tick}] clients={manager.count()} "
                     f"msgs={s['msgs_sent']} bytes={s['bytes_sent']}")
            db.log_metric("server_stats_60s", json.dumps({
                "tick": tick, "clients": manager.count(),
                "msgs": s["msgs_sent"], "bytes": s["bytes_sent"]
            }))

        elapsed = time.monotonic() - t0
        await asyncio.sleep(max(0.0, interval - elapsed))

# ─── 9. MAIN ENTRY POINT ─────────────────────────────────────────────────────
async def main():
    HOST, PORT = "0.0.0.0", 8765

    log.info("=" * 60)
    log.info("  Real-Time Stock Tracker — Group-14 CN Assignment")
    log.info("=" * 60)

    # Generate self-signed TLS certificate (production: use CA-signed cert)
    cert_p, key_p = "certs/server.crt", "certs/server.key"
    os.makedirs("certs", exist_ok=True)
    if not os.path.exists(cert_p):
        r = os.system(
            f'openssl req -x509 -newkey rsa:2048 -keyout {key_p} -out {cert_p} '
            f'-days 365 -nodes '
            f'-subj "/C=IN/ST=Karnataka/L=Udupi/O=Group14/CN=localhost" 2>/dev/null'
        )
        if r == 0:
            log.info("[TLS] Self-signed certificate generated")

    ssl_ctx = None
    if os.path.exists(cert_p):
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(cert_p, key_p)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        # Prefer ECDHE for forward secrecy; AESGCM for hardware acceleration
        ssl_ctx.set_ciphers("ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM")
        log.info("[TLS] TLS 1.2+ with ECDHE + AESGCM/ChaCha20")
    else:
        log.warning("[TLS] No cert found — running in plaintext WS (dev only)")

    server = await websockets.serve(
        handle_client,
        HOST, PORT,
        ssl=ssl_ctx,
        max_size=2 ** 20,      # 1 MB max WebSocket frame
        ping_interval=20,      # Send WS ping every 20s (RFC 6455 heartbeat)
        ping_timeout=10,       # Close if no pong in 10s
        close_timeout=5,
        compression=None,      # Disable permessage-deflate for lower latency
    )

    proto = "wss" if ssl_ctx else "ws"
    log.info(f"[SERVER] Listening: {proto}://{HOST}:{PORT}")
    log.info(f"[SERVER] Valid tokens: {list(VALID_TOKENS.keys())}")
    log.info(f"[SERVER] DB: {db._path}")

    broadcast_task = asyncio.create_task(broadcast_loop(1.0))
    try:
        await asyncio.Future()  # Block until cancelled
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("[SERVER] Shutdown...")
        broadcast_task.cancel()
        server.close()
        await server.wait_closed()
        log.info(f"[SERVER] Final DB stats: {db.get_stats()}")

if __name__ == "__main_p_":
    asyncio.run(main())