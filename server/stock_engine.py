"""
server/stock_engine.py
──────────────────────
Simulates live stock prices using Geometric Brownian Motion (GBM):

    dS = mu * S * dt + sigma * S * dW

where dW ~ N(0, sqrt(dt)) is a Wiener process increment.

Parameters (per tick):
    mu    = 0.0001   (drift: slight upward bias)
    sigma = 0.002    (volatility: 0.2% per tick)
"""

import datetime
import random
import threading

from .database import db

# ── Universe of tracked symbols ───────────────────────────────────────────────
SYMBOLS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "NFLX"]

BASE_PRICES = {
    "AAPL": 189.50, "GOOGL": 175.20, "MSFT": 415.30, "AMZN": 198.70,
    "TSLA": 175.80, "NVDA":  875.40, "META": 520.10, "NFLX": 630.25,
}


class StockEngine:
    """
    Produces price ticks for all symbols on every call to `tick()`.

    State maintained per symbol:
        prices  — current price (updated by GBM each tick)
        open_p  — session open price (fixed at engine init)
        highs   — session high
        lows    — session low
        vols    — cumulative session volume
    """

    def __init__(self):
        self.prices = dict(BASE_PRICES)
        self.open_p = dict(BASE_PRICES)
        self.highs  = dict(BASE_PRICES)
        self.lows   = dict(BASE_PRICES)
        self.vols   = {s: 0 for s in SYMBOLS}
        self._lock  = threading.Lock()

    def tick(self) -> list:
        """
        Advance prices by one time step and persist each update to the DB.

        Returns a list of price-update dicts (one per symbol).
        """
        updates = []
        with self._lock:
            for sym in SYMBOLS:
                mu, sigma = 0.0001, 0.002
                dW = random.gauss(0, 1)
                dS = self.prices[sym] * (mu + sigma * dW)
                self.prices[sym] = max(1.0, self.prices[sym] + dS)
                p = round(self.prices[sym], 2)

                self.highs[sym] = max(self.highs[sym], p)
                self.lows[sym]  = min(self.lows[sym],  p)
                self.vols[sym] += random.randint(100, 5000)
                chg = round((p - self.open_p[sym]) / self.open_p[sym] * 100, 3)

                updates.append({
                    "type":       "price_update",
                    "symbol":     sym,
                    "price":      p,
                    "open":       round(self.open_p[sym], 2),
                    "high":       round(self.highs[sym],  2),
                    "low":        round(self.lows[sym],   2),
                    "volume":     self.vols[sym],
                    "change_pct": chg,
                    "ts":         datetime.datetime.utcnow().isoformat() + "Z",
                })
                # Every tick persisted to DB for historical analysis
                db.insert_price(
                    sym, p, self.open_p[sym],
                    self.highs[sym], self.lows[sym],
                    self.vols[sym], chg,
                )
        return updates


# Module-level singleton — import `engine` from here elsewhere
engine = StockEngine()
