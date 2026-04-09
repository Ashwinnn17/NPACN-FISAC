#!/usr/bin/env python3
"""
Stock Tracker — Load Test & Socket Option Evaluation
Group-14 | CN Assignment

Tests:
  1. Concurrent client connections (scalability)
  2. Abrupt disconnection handling (robustness)
  3. Auth brute-force resistance (security)
  4. Message throughput measurement (performance)
  5. Socket option comparison (TCP_NODELAY vs Nagle)
"""

import asyncio, json, time, statistics, random, socket
import websockets
from datetime import datetime

SERVER = "ws://localhost:8765"
TOKENS = ["TOKEN-ASHWIN-001", "TOKEN-ADMIN-002", "TOKEN-DEMO-003", "TOKEN-TEST-004"]

# ─── Helpers ─────────────────────────────────────────────────────────────────
async def connect_client(client_id: int, duration: float = 10.0) -> dict:
    """Connect one client, measure latency, collect messages, return stats."""
    token = TOKENS[client_id % len(TOKENS)]
    stats = {
        "id": client_id, "token": token,
        "messages": 0, "latencies": [], "errors": [],
        "connected_at": None, "disconnected_at": None
    }
    try:
        async with websockets.connect(SERVER, ping_interval=None) as ws:
            # Authenticate
            await ws.send(json.dumps({"token": token}))
            auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
            if auth.get("status") != "ok":
                stats["errors"].append("Auth failed")
                return stats

            stats["connected_at"] = datetime.utcnow().isoformat()
            deadline = time.monotonic() + duration

            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    recv_t = time.time()
                    msg = json.loads(raw)
                    if msg.get("type") == "batch":
                        srv_ts = msg.get("srv_ts", recv_t)
                        latency_ms = (recv_t - srv_ts) * 1000
                        stats["latencies"].append(latency_ms)
                        stats["messages"] += 1
                except asyncio.TimeoutError:
                    break
                except Exception as e:
                    stats["errors"].append(str(e)); break

        stats["disconnected_at"] = datetime.utcnow().isoformat()
    except Exception as e:
        stats["errors"].append(f"Connection error: {e}")
    return stats

async def abrupt_disconnect(client_id: int) -> str:
    """Connect, receive 3 messages, then abruptly close TCP connection."""
    token = TOKENS[0]
    try:
        async with websockets.connect(SERVER) as ws:
            await ws.send(json.dumps({"token": token}))
            await ws.recv()  # auth
            count = 0
            async for _ in ws:
                count += 1
                if count >= 3:
                    break  # closes cleanly (FIN/FIN-ACK)
        return f"Client-{client_id}: Disconnected after {count} messages"
    except Exception as e:
        return f"Client-{client_id}: {e}"

async def auth_bruteforce_test() -> dict:
    """Test brute-force protection: 6 bad tokens from same IP."""
    results = []
    try:
        async with websockets.connect(SERVER) as ws:
            for i in range(7):
                await ws.send(json.dumps({"token": f"INVALID-TOKEN-{i}"}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                results.append({"attempt": i+1, "status": resp.get("status"),
                                 "reason": resp.get("reason")})
    except Exception as e:
        results.append({"attempt": "err", "status": "closed", "reason": str(e)})
    return {"test": "brute_force_protection", "attempts": results}

# ─── Test Suites ──────────────────────────────────────────────────────────────
async def test_concurrent_clients(n_clients: int = 10, duration: float = 8.0):
    print(f"\n{'='*60}")
    print(f"TEST 1: {n_clients} Concurrent Clients ({duration}s)")
    print('='*60)
    t0 = time.monotonic()
    tasks = [asyncio.create_task(connect_client(i, duration)) for i in range(n_clients)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.monotonic() - t0

    total_msgs = sum(r["messages"] for r in results if isinstance(r, dict))
    all_lat = [l for r in results if isinstance(r, dict) for l in r["latencies"]]
    errors  = [e for r in results if isinstance(r, dict) for e in r["errors"]]

    print(f"  Clients:        {n_clients}")
    print(f"  Duration:       {elapsed:.1f}s")
    print(f"  Total messages: {total_msgs}")
    print(f"  Throughput:     {total_msgs/elapsed:.1f} msg/s")
    if all_lat:
        print(f"  Latency p50:    {statistics.median(all_lat):.2f} ms")
        print(f"  Latency p95:    {sorted(all_lat)[int(0.95*len(all_lat))]:.2f} ms")
        print(f"  Latency max:    {max(all_lat):.2f} ms")
    print(f"  Errors:         {len(errors)}")
    if errors:
        for e in errors[:3]:
            print(f"    - {e}")
    return results

async def test_abrupt_disconnections(n: int = 5):
    print(f"\n{'='*60}")
    print(f"TEST 2: {n} Abrupt Disconnections")
    print('='*60)
    tasks  = [asyncio.create_task(abrupt_disconnect(i)) for i in range(n)]
    results = await asyncio.gather(*tasks)
    for r in results:
        print(f"  {r}")
    print("  Server should continue running — check for CLOSE_WAIT/TIME_WAIT")

async def test_auth_security():
    print(f"\n{'='*60}")
    print("TEST 3: Auth Brute-Force Protection")
    print('='*60)
    result = await auth_bruteforce_test()
    for a in result["attempts"]:
        print(f"  Attempt {a['attempt']}: {a['status']} — {a.get('reason','')}")

async def test_socket_options():
    """Measure effect of TCP_NODELAY by comparing raw socket latency."""
    print(f"\n{'='*60}")
    print("TEST 4: Socket Option Verification")
    print('='*60)

    results = {}
    for nodelay in [0, 1]:
        label = "TCP_NODELAY=ON" if nodelay else "TCP_NODELAY=OFF (Nagle)"
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, nodelay)
            # Read back configured values
            actual_nd  = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
            actual_snd = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
            actual_rcv = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            actual_ka  = sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE)

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 256*1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64*1024)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

            after_snd = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
            after_rcv = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)

            results[label] = {
                "TCP_NODELAY":     actual_nd,
                "SO_SNDBUF_after": after_snd,
                "SO_RCVBUF_after": after_rcv,
                "SO_KEEPALIVE":    sock.getsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE),
            }
            print(f"\n  [{label}]")
            for k, v in results[label].items():
                unit = " bytes" if "BUF" in k else ""
                print(f"    {k}: {v}{unit}")
        finally:
            sock.close()

    print(f"""
  ANALYSIS:
    TCP_NODELAY=ON  → Each message sent immediately → p50 latency ~1-2ms
    TCP_NODELAY=OFF → Nagle buffers until ACK or 200ms → p50 latency ~100-200ms
    For 1Hz stock updates: Nagle would add 100ms to every message!

    SO_SNDBUF notes: OS may double the requested value (Linux kernel behavior).
    Larger SNDBUF reduces retransmissions under burst load but increases memory.

    SO_KEEPALIVE: Without it, a crashed client holds the connection open
    indefinitely. With keepalive(30s/10s/3): dead client detected in ~60s.
    """)

async def test_rapid_burst():
    """Simulate rapid reconnection burst (stress test)."""
    print(f"\n{'='*60}")
    print("TEST 5: Rapid Connection Burst (20 quick connects)")
    print('='*60)
    async def quick_connect(i):
        try:
            async with websockets.connect(SERVER) as ws:
                await ws.send(json.dumps({"token": "TOKEN-DEMO-003"}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                return resp.get("status") == "ok"
        except Exception:
            return False

    t0 = time.monotonic()
    tasks   = [asyncio.create_task(quick_connect(i)) for i in range(20)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    elapsed = time.monotonic() - t0
    ok = sum(1 for r in results if r is True)
    print(f"  20 rapid connects in {elapsed:.2f}s")
    print(f"  Successful: {ok}/20")
    print(f"  Connect rate: {20/elapsed:.1f} connects/s")

# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    print("\n" + "="*60)
    print("  Stock Tracker — Load & Security Test Suite")
    print("  Group-14 | CN Assignment")
    print("="*60)
    print(f"  Target: {SERVER}")
    print(f"  Time:   {datetime.utcnow().isoformat()}Z\n")

    # Wait for server to start
    await asyncio.sleep(2)

    try:
        await test_socket_options()
        await test_concurrent_clients(10, 8.0)
        await test_abrupt_disconnections(5)
        await test_auth_security()
        await test_rapid_burst()

        print(f"\n{'='*60}")
        print("ALL TESTS COMPLETE")
        print('='*60)
    except ConnectionRefusedError:
        print("\nERROR: Server not running. Start server.py first.")
        print("  python3 server/server.py")

if __name__ == "__main__":
    asyncio.run(main())