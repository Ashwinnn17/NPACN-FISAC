"""
server/broadcast.py
───────────────────
Price-tick broadcast loop.

Runs as a background asyncio task in main().
Produces price updates at 1 Hz (configurable) and fans them out to all
connected clients via ClientManager.broadcast().

Adaptive sleep: compensates for DB write overhead to maintain the target rate.
Every 60 ticks (~1 minute) a summary is logged and written to socket_metrics.
"""

import asyncio
import json
import time

from .client_manager import manager
from .database import db
from .logger import log
from .stock_engine import engine


async def broadcast_loop(interval: float = 1.0):
    """
    Produce price ticks at `1/interval` Hz and fan out to all clients.

    Args:
        interval: Seconds between ticks (default 1.0 → 1 Hz).
    """
    log.info(f"[BROADCAST] Starting at {1 / interval:.1f} Hz")
    tick = 0

    while True:
        t0 = time.monotonic()

        updates = engine.tick()
        await manager.broadcast(updates)
        tick += 1

        # Periodic stats log (every ~60 s)
        if tick % 60 == 0:
            s = manager.snapshot()
            log.info(
                f"[TICK={tick}] clients={manager.count()} "
                f"msgs={s['msgs_sent']} bytes={s['bytes_sent']}"
            )
            db.log_metric("server_stats_60s", json.dumps({
                "tick":    tick,
                "clients": manager.count(),
                "msgs":    s["msgs_sent"],
                "bytes":   s["bytes_sent"],
            }))

        elapsed = time.monotonic() - t0
        await asyncio.sleep(max(0.0, interval - elapsed))
