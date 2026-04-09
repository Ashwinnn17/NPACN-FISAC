// ──────────────────────────────────────────────────────────────────────────────
// NEXUS TRADE — Client-Side Application
// Implements: WebSocket client, auth, real-time chart, ticker, event log
// ──────────────────────────────────────────────────────────────────────────────

const SYMBOLS = ["AAPL","GOOGL","MSFT","AMZN","TSLA","NVDA","META","NFLX"];
// MOCK DATA — commented out (requires live server)
// const DEMO_BASE = {
//   AAPL:189.50, GOOGL:175.20, MSFT:415.30, AMZN:198.70,
//   TSLA:175.80, NVDA:875.40,  META:520.10, NFLX:630.25
// };

// ── State ────────────────────────────────────────────────────────────────────
const state = {
  ws: null, connected: false, username: null,
  selectedSymbol: "AAPL",
  prices: {}, history: {},        // symbol → [{price,change_pct,ts}]
  sparkData: {},                  // symbol → [last 20 prices]
  updateCount: 0, msgCount: 0,
  bytesReceived: 0,
  latencies: [],
  connectedAt: null,
  tickCount: 0,
  demoMode: false,                // True when no server available
  demoInterval: null,
};

// State init — prices start blank until server sends data
SYMBOLS.forEach(s => {
  state.prices[s]   = { price: 0, change_pct: 0, open: 0, high: 0, low: 0, volume: 0 };
  state.history[s]  = [];
  state.sparkData[s] = [];
});

// ── Clock ────────────────────────────────────────────────────────────────────
function updateClock() {
  const n = new Date();
  document.getElementById('clock').textContent =
    n.toUTCString().split(' ')[4] + ' UTC';
}
setInterval(updateClock, 1000); updateClock();

// ── Chart (Chart.js) ─────────────────────────────────────────────────────────
const chartCtx = document.getElementById('priceChart').getContext('2d');
Chart.defaults.color = '#6b7280';
Chart.defaults.font.family = "'Courier New', monospace";
Chart.defaults.font.size = 10;

const priceChart = new Chart(chartCtx, {
  type: 'line',
  data: {
    labels: Array(60).fill(''),
    datasets: [{
      label: 'AAPL',
      data: Array(60).fill(null),
      borderColor: '#3b82f6',
      backgroundColor: ctx => {
        const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, ctx.chart.height);
        g.addColorStop(0, 'rgba(59,130,246,0.12)');
        g.addColorStop(1, 'rgba(59,130,246,0)');
        return g;
      },
      borderWidth: 1.5,
      pointRadius: 0,
      fill: true,
      tension: 0.3,
    }]
  },
  options: {
    responsive: true, maintainAspectRatio: false, animation: { duration: 200 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#161b25',
        borderColor: '#252e3d', borderWidth: 1,
        titleColor: '#3b82f6', bodyColor: '#d1d5db',
        callbacks: {
          label: ctx => ` $${ctx.parsed.y?.toFixed(2) ?? 'N/A'}`
        }
      }
    },
    scales: {
      x: { grid:{ color:'rgba(37,46,61,0.5)', drawBorder:false },
           ticks:{ maxTicksLimit:8 } },
      y: { grid:{ color:'rgba(37,46,61,0.5)', drawBorder:false },
           position:'right',
           ticks:{ callback: v => '$'+v.toFixed(0) } }
    }
  }
});

function updateChart(symbol) {
  const h = state.history[symbol] || [];
  const prices = [...h].reverse().slice(-60);
  priceChart.data.labels = prices.map(p => p.ts ? p.ts.slice(11,19) : '');
  priceChart.data.datasets[0].label = symbol;
  priceChart.data.datasets[0].data  = prices.map(p => p.price);
  const chg = state.prices[symbol]?.change_pct ?? 0;
  priceChart.data.datasets[0].borderColor =
    chg >= 0 ? '#3b82f6' : '#ef4444';
  priceChart.update('none');
  document.getElementById('chart-title').textContent =
    `PRICE HISTORY — ${symbol}`;
}

// ── Stock Cards ───────────────────────────────────────────────────────────────
function buildCards() {
  const grid = document.getElementById('stocks-grid');
  grid.innerHTML = '';
  SYMBOLS.forEach(sym => {
    const d = document.createElement('div');
    d.className = 'stock-card' + (sym === state.selectedSymbol ? ' active' : '');
    d.id = `card-${sym}`;
    d.onclick = () => selectSymbol(sym);
    d.innerHTML = `
      <div class="sc-sym">${sym}</div>
      <div class="sc-price" id="price-${sym}">—</div>
      <div class="sc-change">
        <span class="sc-arrow" id="arrow-${sym}">—</span>
        <span id="chg-${sym}">—</span>
      </div>
      <div class="sc-ohlv">
        <div style="color:#6b7280">O <span id="open-${sym}">—</span></div>
        <div style="color:#22c55e">H <span id="high-${sym}">—</span></div>
        <div style="color:#ef4444">L <span id="low-${sym}">—</span></div>
        <div style="color:#6b7280">V <span id="vol-${sym}">—</span></div>
      </div>
      <div class="sc-spark"><svg id="spark-${sym}" viewBox="0 0 100 30" preserveAspectRatio="none"></svg></div>`;
    grid.appendChild(d);
  });
}

function selectSymbol(sym) {
  document.querySelectorAll('.stock-card').forEach(c => c.classList.remove('active'));
  const el = document.getElementById(`card-${sym}`);
  if (el) el.classList.add('active');
  state.selectedSymbol = sym;
  document.getElementById('selected-label').textContent = sym + ' SELECTED';
  updateChart(sym);
}

function updateCard(sym, data) {
  const prev = state.prices[sym]?.price ?? data.price;
  state.prices[sym] = data;

  // Sparkline data
  if (!state.sparkData[sym]) state.sparkData[sym] = [];
  state.sparkData[sym].push(data.price);
  if (state.sparkData[sym].length > 20) state.sparkData[sym].shift();

  // History
  if (!state.history[sym]) state.history[sym] = [];
  state.history[sym].unshift({ price: data.price, change_pct: data.change_pct, ts: data.ts });
  if (state.history[sym].length > 200) state.history[sym].pop();

  // DOM updates
  const priceEl = document.getElementById(`price-${sym}`);
  const chgEl   = document.getElementById(`chg-${sym}`);
  const arrEl   = document.getElementById(`arrow-${sym}`);
  const card    = document.getElementById(`card-${sym}`);

  if (!priceEl) return;
  priceEl.textContent = '$' + data.price.toFixed(2);
  const up = data.change_pct >= 0;
  chgEl.textContent = (up ? '+' : '') + data.change_pct.toFixed(3) + '%';
  chgEl.className   = up ? 'up' : 'down';
  arrEl.textContent = up ? '▲' : '▼';
  arrEl.className   = 'sc-arrow ' + (up ? 'up' : 'down');

  document.getElementById(`open-${sym}`).textContent = data.open?.toFixed(2) ?? '—';
  document.getElementById(`high-${sym}`).textContent = data.high?.toFixed(2) ?? '—';
  document.getElementById(`low-${sym}`).textContent  = data.low?.toFixed(2) ?? '—';
  document.getElementById(`vol-${sym}`).textContent  =
    data.volume ? (data.volume/1000).toFixed(1)+'K' : '0';

  // Flash animation
  if (card) {
    card.classList.remove('stock-flash-up','stock-flash-dn');
    void card.offsetWidth;
    card.classList.add(data.price >= prev ? 'stock-flash-up' : 'stock-flash-dn');
  }

  // Sparkline SVG
  drawSparkline(sym);

  // Update chart if selected
  if (sym === state.selectedSymbol) updateChart(sym);
}

function drawSparkline(sym) {
  const data = state.sparkData[sym] || [];
  if (data.length < 2) return;
  const svg  = document.getElementById(`spark-${sym}`);
  if (!svg) return;
  const min  = Math.min(...data), max = Math.max(...data);
  const rng  = max - min || 1;
  const pts  = data.map((v, i) =>
    `${(i/(data.length-1)*100).toFixed(1)},${(30 - (v-min)/rng*28).toFixed(1)}`).join(' ');
  const isUp = data[data.length-1] >= data[0];
  svg.innerHTML = `<polyline points="${pts}" fill="none"
    stroke="${isUp ? '#22c55e' : '#ef4444'}" stroke-width="1.5"
    stroke-linecap="round" stroke-linejoin="round"/>`;
}

// ── Ticker Tape ───────────────────────────────────────────────────────────────
function updateTicker() {
  const inner = document.getElementById('ticker-inner');
  if (!inner) return;
  const items = SYMBOLS.map(sym => {
    const p = state.prices[sym];
    const up = p.change_pct >= 0;
    return `<div class="ticker-item">
      <span class="ticker-sym">${sym}</span>
      <span class="ticker-price">$${p.price.toFixed(2)}</span>
      <span class="ticker-chg ${up?'up':'down'}">${up?'+':''}${p.change_pct.toFixed(2)}%</span>
    </div>`;
  }).join('');
  inner.innerHTML = items + items; // doubled for seamless loop
}

// ── Event Logger ──────────────────────────────────────────────────────────────
let logCount = 0;
function addLog(msg, level='info') {
  logCount++;
  const el = document.getElementById('log-entries');
  if (!el) return;
  const ts  = new Date().toISOString().slice(11,23);
  const div = document.createElement('div');
  div.className = 'log-entry';
  div.innerHTML = `<span class="log-ts">${ts}</span><span class="log-msg ${level}">${msg}</span>`;
  el.insertBefore(div, el.firstChild);
  if (el.children.length > 80) el.removeChild(el.lastChild);
  document.getElementById('log-count').textContent = logCount + ' EVENTS';
}

// ── Connection Info Updates ───────────────────────────────────────────────────
function setConnected(username, proto) {
  state.connected = true; state.username = username;
  state.connectedAt = Date.now();
  const badge = document.getElementById('conn-badge');
  badge.className = 'conn-badge connected';
  document.getElementById('conn-text').textContent = 'CONNECTED';
  document.getElementById('inf-proto').textContent = proto || 'WS';
  document.getElementById('inf-state').textContent = 'ESTABLISHED';
  document.getElementById('inf-state').className = 'info-val ok';
  document.getElementById('inf-user').textContent = username.toUpperCase();
  document.getElementById('auth-panel').style.display = 'none';
  // Source badge — only set LIVE if not demo
  if (!state.demoMode) {
    const sb = document.getElementById('source-badge');
    sb.className = 'source-badge source-live';
    sb.textContent = '\u25CF LIVE SERVER (' + (proto || 'WS') + ')';
  }
  addLog(`TCP handshake + WS upgrade complete`, 'ok');
  addLog(`Authenticated as '${username}'`, 'ok');
  addLog(`WebSocket ESTABLISHED`, 'ok');
}

function setDisconnected(reason) {
  state.connected = false;
  document.getElementById('conn-badge').className = 'conn-badge';
  document.getElementById('conn-text').textContent = 'DISCONNECTED';
  document.getElementById('inf-state').textContent = 'CLOSED';
  document.getElementById('inf-state').className = 'info-val err';
  const sb = document.getElementById('source-badge');
  sb.className = 'source-badge source-none';
  sb.textContent = '\u25CF NO SOURCE';
  addLog(`Connection closed: ${reason}`, 'err');
  if (state.demoMode && state.demoInterval) {
    clearInterval(state.demoInterval); state.demoInterval = null;
  }
}

function updateMetrics(latency) {
  state.latencies.push(latency);
  if (state.latencies.length > 60) state.latencies.shift();
  const avg = state.latencies.reduce((a,b)=>a+b,0)/state.latencies.length;

  document.getElementById('inf-lat').textContent   = latency.toFixed(1) + ' ms';
  document.getElementById('inf-msgs').textContent  = state.msgCount;
  document.getElementById('inf-bytes').textContent = formatBytes(state.bytesReceived);
  document.getElementById('stat-lat').textContent  = avg.toFixed(0) + 'ms';
  document.getElementById('stat-ticks').textContent = state.tickCount;
  document.getElementById('update-counter').textContent = state.updateCount + ' UPDATES';

  // Latency bar: green <5ms, amber <50ms, red >50ms
  const pct = Math.min(100, latency / 100 * 100);
  const fill = document.getElementById('lat-fill');
  fill.style.width = pct + '%';
  fill.style.background = latency < 5 ? '#22c55e' : latency < 50 ? '#f59e0b' : '#ef4444';

  if (state.connectedAt) {
    const sec = Math.floor((Date.now() - state.connectedAt)/1000);
    document.getElementById('inf-uptime').textContent =
      `${Math.floor(sec/60)}m ${sec%60}s`;
    const mps = (state.msgCount / Math.max(1, sec)).toFixed(1);
    document.getElementById('stat-mps').textContent = mps;
  }
}

function formatBytes(b) {
  if (b < 1024) return b + ' B';
  if (b < 1048576) return (b/1024).toFixed(1) + ' KB';
  return (b/1048576).toFixed(2) + ' MB';
}

// ── WebSocket Connection ──────────────────────────────────────────────────────
function connect(server, token) {
  addLog(`Initiating TCP connection to ${server}…`, 'info');
  addLog(`→ SYN sent (three-way handshake)`, 'info');

  let ws;
  try {
    ws = new WebSocket(server);
  } catch(e) {
    addLog(`WS construction failed: ${e.message}`, 'err');
    addLog('Start server.py to stream live data', 'warn');
    return;
  }
  state.ws = ws;

  // const connTimeout = setTimeout(() => {
  //   if (ws.readyState !== WebSocket.OPEN) {
  //     addLog('Connection timeout — DEMO mode disabled', 'warn');
  //     ws.close();
  //     startDemoMode(token); // MOCK — commented out
  //   }
  // }, 4000);

  ws.onopen = () => {
    // clearTimeout(connTimeout);
    addLog(`→ SYN-ACK received`, 'info');
    addLog(`→ ACK sent — TCP ESTABLISHED`, 'ok');
    addLog(`HTTP/1.1 101 Switching Protocols (WS Upgrade)`, 'data');
    ws.send(JSON.stringify({ token }));
    document.getElementById('inf-proto').textContent = server.startsWith('wss') ? 'WSS/TLS' : 'WS';
  };

  ws.onmessage = evt => {
    const t0 = performance.now();
    state.msgCount++;
    state.bytesReceived += evt.data.length;

    let msg;
    try { msg = JSON.parse(evt.data); }
    catch { return; }

    if (msg.type === 'auth') {
      if (msg.status === 'ok') {
        setConnected(msg.username, server.startsWith('wss') ? 'WSS (TLS 1.2+)' : 'WS');
        addLog(`Server time: ${msg.server_time}`, 'data');
        addLog(`Subscribed to ${(msg.symbols||[]).length} symbols`, 'ok');
      } else {
        addLog(`Auth FAILED: ${msg.reason}`, 'err');
        document.getElementById('auth-error').style.display = 'block';
        document.getElementById('auth-error').textContent  =
          '⚠ ' + (msg.reason || 'Authentication failed');
      }
      return;
    }

    if (msg.type === 'history') {
      Object.entries(msg.data||{}).forEach(([sym, hist]) => {
        state.history[sym] = hist;
        if (hist.length > 0) {
          state.sparkData[sym] = hist.slice(0,20).reverse().map(h=>h.price);
        }
      });
      addLog(`Historical data loaded (${Object.keys(msg.data||{}).length} symbols)`, 'data');
      updateChart(state.selectedSymbol);
      return;
    }

    if (msg.type === 'batch') {
      const lat = (performance.now() - t0);
      const serverLat = msg.srv_ts ? (Date.now()/1000 - msg.srv_ts) * 1000 : lat;
      state.tickCount++;
      state.updateCount += (msg.updates||[]).length;
      (msg.updates||[]).forEach(u => updateCard(u.symbol, u));
      updateMetrics(serverLat > 0 ? serverLat : lat);
      updateTicker();

      if (state.tickCount % 30 === 0) {
        addLog(`Tick #${state.tickCount} | lat=${serverLat.toFixed(1)}ms | clients=${state.msgCount}`, 'data');
        ws.send(JSON.stringify({type:'stats'}));
      }
    }

    if (msg.type === 'stats') {
      const db = msg.data?.db;
      if (db) {
        document.getElementById('stat-db').textContent = db.price_records || '—';
        addLog(`DB: ${db.price_records} records | auth_fail=${db.auth_failures}`, 'info');
      }
    }
  };

  ws.onerror = err => {
    // clearTimeout(connTimeout);
    addLog(`WebSocket error — check server is running`, 'err');
    // startDemoMode(token); // MOCK — commented out
  };

  ws.onclose = evt => {
    // clearTimeout(connTimeout);
    const reason = evt.reason || `code=${evt.code}`;
    if (state.connected) {
      setDisconnected(reason);
      addLog(`TCP FIN sent → FIN-ACK received (TIME_WAIT)`, 'info');
    }
  };
}

// ── DEMO MODE — commented out (requires live server) ────────────────────────
// function startDemoMode(token) {
//   state.demoMode = true;
//   const sb = document.getElementById('source-badge');
//   sb.className = 'source-badge source-demo';
//   sb.textContent = '\u25CF DEMO \u2014 SIMULATED';
//   const users = {
//     "TOKEN-ASHWIN-001":"ashwin","TOKEN-ADMIN-002":"admin",
//     "TOKEN-DEMO-003":"demo","TOKEN-TEST-004":"test"
//   };
//   const username = users[token] || "demo";
//   setConnected(username, "WS (DEMO)");
//   addLog(`DEMO mode active — simulating server locally`, 'warn');
//   addLog(`All socket options & DB features require Python server`, 'warn');
//
//   let tick = 0;
//   const openPrices = {...DEMO_BASE};
//   const prices     = {...DEMO_BASE};
//   const highs      = {...DEMO_BASE};
//   const lows       = {...DEMO_BASE};
//   const vols       = Object.fromEntries(SYMBOLS.map(s=>[s,0]));
//
//   state.demoInterval = setInterval(() => {
//     tick++;
//     const updates = [];
//     SYMBOLS.forEach(sym => {
//       const dS = prices[sym] * (0.0001 + 0.002 * (Math.random()*2-1));
//       prices[sym] = Math.max(1, prices[sym] + dS);
//       const p = parseFloat(prices[sym].toFixed(2));
//       highs[sym] = Math.max(highs[sym], p);
//       lows[sym]  = Math.min(lows[sym], p);
//       vols[sym] += Math.floor(Math.random()*5000 + 100);
//       const chg = parseFloat(((p - openPrices[sym]) / openPrices[sym] * 100).toFixed(3));
//       updates.push({
//         type:"price_update", symbol:sym, price:p,
//         open:parseFloat(openPrices[sym].toFixed(2)),
//         high:parseFloat(highs[sym].toFixed(2)),
//         low:parseFloat(lows[sym].toFixed(2)),
//         volume:vols[sym], change_pct:chg,
//         ts: new Date().toISOString()
//       });
//     });
//     state.tickCount++;
//     state.updateCount += updates.length;
//     state.msgCount++;
//     state.bytesReceived += JSON.stringify(updates).length;
//     updates.forEach(u => updateCard(u.symbol, u));
//     updateMetrics(Math.random() * 3 + 0.5);
//     updateTicker();
//     if (tick % 30 === 0) {
//       addLog(`[DEMO] Tick #${tick} | GBM simulation active`, 'data');
//       document.getElementById('stat-db').textContent = tick * 8 + ' (sim)';
//     }
//   }, 1000);
// }

// ── Auth Form ─────────────────────────────────────────────────────────────────
document.getElementById('auth-user-select').addEventListener('change', function() {
  document.getElementById('auth-token').value = this.value;
});

document.getElementById('auth-btn').addEventListener('click', () => {
  const token  = document.getElementById('auth-token').value.trim();
  const server = document.getElementById('auth-server').value.trim();
  document.getElementById('auth-error').style.display = 'none';
  if (!token) {
    document.getElementById('auth-error').style.display = 'block';
    document.getElementById('auth-error').textContent = '⚠ Token is required';
    return;
  }
  connect(server, token);
});

document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.getElementById('auth-panel').style.display !== 'none') {
    document.getElementById('auth-btn').click();
  }
});

// ── Startup ───────────────────────────────────────────────────────────────────
buildCards();
updateTicker();
addLog('NEXUS TRADE terminal initialized', 'ok');
addLog('TCP socket layer ready', 'info');
addLog('Awaiting authentication…', 'info');
