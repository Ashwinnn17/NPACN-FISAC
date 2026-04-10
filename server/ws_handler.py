"""
server/ws_handler.py
────────────────────
Per-connection WebSocket coroutine.

Lifecycle:
    [TCP SYN/SYN-ACK/ACK] → [HTTP 101 Upgrade] → [Auth] → [Subscribe] → [Stream]

The TCP handshake and HTTP upgrade are handled transparently by the `websockets`
library before this coroutine is invoked.

Concurrent tasks per client:
    sender()   — drains the client's asyncio.Queue → WebSocket wire
    receiver() — handles incoming control frames (ping / stats / history)
"""

import asyncio
import datetime
import json
import time

from websockets.exceptions import ConnectionClosed

from .auth import verify_token, VALID_TOKENS
from .client_manager import manager
from .database import db
from .logger import log
from .stock_engine import SYMBOLS


async def handle_client(websocket):
    """
    Main handler coroutine — one instance per connected client.

    Registered with `websockets.serve()` in server.py.
    """
    ip, port = websocket.remote_address[:2]
    cid = f"{ip}:{port}"
    log.info(f"[WS] New connection: {cid}")

    # ── Phase 1: Authentication ───────────────────────────────────────────────
    try:
        raw   = await asyncio.wait_for(websocket.recv(), timeout=10.0)
        data  = json.loads(raw)
        token = data.get("token", "")
    except asyncio.TimeoutError:
        log.warning(f"[WS] {cid} auth timeout")
        db.log_connection("ERROR", ip, port, cid, "Auth timeout")
        await websocket.close(1008, "Auth timeout")
        return
    except Exception as e:
        log.warning(f"[WS] {cid} auth error: {e}")
        await websocket.close(1008, "Auth error")
        return

    username = verify_token(token, ip)
    if not username:
        await websocket.send(json.dumps({
            "type": "auth", "status": "fail",
            "reason": "Invalid credentials or rate-limited",
        }))
        await websocket.close(1008, "Unauthorized")
        return

    await websocket.send(json.dumps({
        "type":        "auth",
        "status":      "ok",
        "username":    username,
        "symbols":     SYMBOLS,
        "server_time": datetime.datetime.utcnow().isoformat() + "Z",
    }))

    # ── Phase 2: Register client ──────────────────────────────────────────────
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    info = {
        "username":     username,
        "ip":           ip,
        "port":         port,
        "connected_at": datetime.datetime.utcnow().isoformat(),
    }
    await manager.add(cid, q, info)

    # ── Phase 3: Send historical snapshot on connect ──────────────────────────
    hist = {sym: db.get_history(sym, 30) for sym in SYMBOLS}
    await websocket.send(json.dumps({"type": "history", "data": hist}))

    # ── Phase 4: Concurrent send / receive ────────────────────────────────────
    async def sender():
        """Drain per-client queue → WebSocket wire."""
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
                        "type": "history_single", "symbol": sym, "data": hist,
                    }))

            except json.JSONDecodeError:
                pass

    # Run both coroutines; cancel the other when either finishes
    st = asyncio.create_task(sender())
    rt = asyncio.create_task(receiver())
    try:
        _, pending = await asyncio.wait([st, rt], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
    except Exception as e:
        log.error(f"[WS] {cid} error: {e}")
        db.log_connection("ERROR", ip, port, cid, str(e))
    finally:
        await manager.remove(cid)
        log.info(f"[WS] {username}@{cid} fully disconnected")
