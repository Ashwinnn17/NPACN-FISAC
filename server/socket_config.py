"""
server/socket_config.py
───────────────────────
Applies and measures critical TCP/socket options on the server socket.

Options explained:
    SO_REUSEADDR    Allow rebinding while previous socket is in TIME_WAIT.
                    Without this, server restart after crash fails for ~4 min.

    SO_SNDBUF       Send buffer (bytes). Kernel may double the value.
                    Larger → better burst throughput.
                    Smaller → lower latency (less bufferbloat).

    SO_RCVBUF       Receive buffer. Client→server traffic here is small
                    (control messages), so 64 KB is sufficient.

    SO_KEEPALIVE    Enables TCP keep-alive probes. Combined with the
                    TCP_KEEP* options below, detects dead clients without
                    application-level heartbeats.

    TCP_NODELAY     Disables Nagle's algorithm. Nagle coalesces small packets
                    into larger ones — adds up to 200 ms latency per message.
                    For real-time stock prices: MUST be disabled.
"""

import json
import socket

from .database import db
from .logger import log


class SocketConfigurator:
    """Static helper — call `SocketConfigurator.configure(sock)` once on startup."""

    @staticmethod
    def configure(sock: socket.socket) -> dict:
        """
        Apply all socket options and return a dict of applied values
        (useful for logging / DB metrics).
        """
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
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE,  30)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT,    3)
                applied.update({"TCP_KEEPIDLE": 30, "TCP_KEEPINTVL": 10, "TCP_KEEPCNT": 3})

            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            applied["TCP_NODELAY"] = 1

            log.info(f"[SOCKET] Options applied: {applied}")
            db.log_metric("socket_options", json.dumps(applied))

        except Exception as e:
            log.error(f"[SOCKET] Error applying options: {e}")

        return applied
