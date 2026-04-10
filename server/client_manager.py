"""
server/client_manager.py
────────────────────────
Thread-safe registry of all connected WebSocket clients.

Design:
    Each client gets its own asyncio.Queue (maxsize=100) for outbound messages.
    Broadcast fan-out enqueues via `put_nowait`: slow clients get messages
    dropped (back-pressure) rather than blocking the server event loop.
"""

import asyncio
import json
import time
from typing import Dict

from .database import db
from .logger import log


class ClientManager:
    """
    Manages the set of currently connected clients and provides
    a broadcast primitive for fan-out delivery.
    """

    def __init__(self):
        self._clients: Dict[str, asyncio.Queue] = {}
        self._info:    Dict[str, dict]           = {}
        self._lock  = asyncio.Lock()
        self._stats = {"total_connected": 0, "msgs_sent": 0, "bytes_sent": 0}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def add(self, cid: str, queue: asyncio.Queue, info: dict):
        async with self._lock:
            self._clients[cid] = queue
            self._info[cid]    = info
            self._stats["total_connected"] += 1
        log.info(f"[CM] +{cid} ({info.get('username')}) | active={len(self._clients)}")
        db.log_connection("CONNECT", info.get("ip"), info.get("port"), cid)

    async def remove(self, cid: str):
        async with self._lock:
            info = self._info.pop(cid, {})
            self._clients.pop(cid, None)
        log.info(f"[CM] -{cid} | active={len(self._clients)}")
        db.log_connection("DISCONNECT", info.get("ip"), info.get("port"), cid)

    # ── Broadcast ─────────────────────────────────────────────────────────────

    async def broadcast(self, updates: list):
        """
        Fan-out a batch of price updates to every connected client.
        Uses put_nowait so a slow client never stalls the loop.
        """
        msg  = json.dumps({"type": "batch", "updates": updates, "srv_ts": time.time()})
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

    # ── Queries ───────────────────────────────────────────────────────────────

    def count(self) -> int:
        return len(self._clients)

    def snapshot(self) -> dict:
        s = dict(self._stats)
        s["active_clients"] = [
            {"id": cid, "user": info.get("username"), "ip": info.get("ip"),
             "since": info.get("connected_at")}
            for cid, info in self._info.items()
        ]
        return s


# Module-level singleton — import `manager` from here elsewhere
manager = ClientManager()
