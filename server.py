#!/usr/bin/env python3
"""
Secure Real-Time Stock Price Tracker — WebSocket Server
Group-14 | CN Assignment

Architecture: TCP Socket -> TLS Negotiation -> HTTP Upgrade (RFC 6455)
              -> WebSocket frames -> Authenticated broadcast

Entry point only — all logic lives in the server/ package:
    server/logger.py         — rotating log setup
    server/database.py       — SQLite thread-safe wrapper
    server/auth.py           — token auth + brute-force rate limiting
    server/stock_engine.py   — GBM price simulation
    server/socket_config.py  — TCP socket option tuning
    server/client_manager.py — WebSocket subscriber registry
    server/ws_handler.py     — per-client coroutine
    server/broadcast.py      — 1 Hz price broadcast loop
"""

import asyncio
import os
import ssl

import websockets

from server.auth import VALID_TOKENS
from server.broadcast import broadcast_loop
from server.database import db
from server.logger import log
from server.ws_handler import handle_client


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
        max_size=2 ** 20,    # 1 MB max WebSocket frame
        ping_interval=20,    # Send WS ping every 20s (RFC 6455 heartbeat)
        ping_timeout=10,     # Close if no pong in 10s
        close_timeout=5,
        compression=None,    # Disable permessage-deflate for lower latency
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


if __name__ == "__main__":
    asyncio.run(main())