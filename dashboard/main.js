// dashboard/main.js

// --- Configuration ---
const WEBSOCKET_URL         = "ws://127.0.0.1:8000/api/v1/ws/dashboard";
const MAX_TRADES_DISPLAY    = 30;
const KNOWN_SYMBOLS         = ["ABC", "XYZ"];
const AVAILABLE_STRATEGIES  = ["noise", "market_maker", "momentum"];
const MAX_CHART_POINTS      = 100;

// --- DOM Elements ---
const statusDiv            = document.getElementById("status");
const tradesTableBody      = document.querySelector("#trades-table tbody");
const agentListUl          = document.getElementById("agent-list");
const totalTradesSpan      = document.getElementById("total-trades");
const totalValueSpan       = document.getElementById("total-value");
const statsTimestampSpan   = document.getElementById("stats-timestamp");
const pauseResumeButton    = document.getElementById("pause-resume-button");
const resetButton          = document.getElementById("reset-button");
const pausedOverlay        = document.getElementById("paused-overlay");
const eventSymbolInput     = document.getElementById("event-symbol");
const eventPercentInput    = document.getElementById("event-percent");
const triggerEventButton   = document.getElementById("trigger-event-button");

// --- Simulation State ---
let isPaused       = false;
const agentState   = {};
const priceCharts  = {};
const priceHistory = {};

// --- WebSocket Connection ---
let ws;
let reconnectAttempts         = 0;
const MAX_RECONNECT_ATTEMPTS  = 5;

function connectWebSocket() {
  console.log(`Connecting (Attempt ${reconnectAttempts + 1})…`);
  ws = new WebSocket(WEBSOCKET_URL);

  ws.onopen = () => {
    console.log("WS opened.");
    statusDiv.textContent  = "Connected";
    statusDiv.className    = "connected";
    reconnectAttempts      = 0;
    clearPlaceholders();
    initializeCharts();
    populateInitialAgentList();
    updatePauseResumeButton();
  };

  ws.onmessage = (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (e) {
      console.error("Msg err:", e, event.data);
    }
  };

  ws.onerror = (e) => {
    console.error("WS err:", e);
    statusDiv.textContent = "Error!";
    statusDiv.className   = "disconnected";
  };

  ws.onclose = (e) => {
    console.log("WS closed:", e.code);
    statusDiv.textContent = `Disconnected (${e.code}).`;
    statusDiv.className   = "disconnected";
    isPaused             = false;
    updatePauseResumeButton();
    destroyCharts();

    if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
      const delay = Math.min(1000 * Math.pow(2, reconnectAttempts++), 10000);
      setTimeout(connectWebSocket, delay);
    } else {
      console.error("Max reconnects.");
    }
  };
}

// --- Utility Functions ---
function clearPlaceholders() {
  const tradePh = tradesTableBody.querySelector("tr");
  if (tradePh && tradePh.cells.length === 1) {
    tradesTableBody.innerHTML = "";
  }
  KNOWN_SYMBOLS.forEach(s => {
    ["asks","bids"].forEach(side => {
      const el = document
        .getElementById(`${side}-display-${s}`)
        ?.querySelector(".book-placeholder");
      if (el) el.textContent = "Waiting...";
    });
  });
  const agentPh = agentListUl.querySelector("li");
  if (agentPh && agentPh.textContent.includes("Loading")) {
    agentListUl.innerHTML = "";
  }
}

function formatTimestamp(isoString) {
  if (!isoString) return "---";
  try {
    const opts = { hour: "2-digit", minute: "2-digit", second: "2-digit" };
    return new Date(isoString).toLocaleTimeString([], opts);
  } catch {
    return "Invalid";
  }
}

function formatCurrency(val) {
  if (val == null) return "---";
  return isNaN(val) ? "NaN" : Number(val).toFixed(2);
}

function formatNumber(val) {
  if (val == null) return "---";
  return isNaN(val) ? "NaN" : Number(val).toLocaleString();
}

// --- Chart Functions ---
function initializeCharts() {
  console.log("Initializing charts…");
  destroyCharts();
  KNOWN_SYMBOLS.forEach(symbol => {
    const canvas = document.getElementById(`chart-${symbol}`);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    priceHistory[symbol] = [];
    priceCharts[symbol]  = new Chart(ctx, {
      type: "line",
      data: {
        datasets: [{
          label:   `${symbol} Price`,
          data:    [],
          borderColor: symbol === "ABC" ? "rgb(54,162,235)" : "rgb(255,99,132)",
          borderWidth: 1.5,
          fill: false,
          tension: 0.1,
          pointRadius: 0,
          pointHoverRadius: 4
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        interaction: { intersect: false, mode: "index" },
        scales: {
          x: { type: "linear", display: false },
          y: { beginAtZero: false, ticks: { maxTicksLimit: 6 } }
        },
        plugins: {
          legend: { display: false },
          title:  { display: true, text: `${symbol} Price Chart` },
          tooltip: {
            enabled: true,
            mode: "index",
            intersect: false,
            callbacks: {
              title: (items) => {
                const i = items[0]?.dataIndex;
                const d = priceHistory[symbol]?.[i];
                return d ? formatTimestamp(d.x_iso) : "";
              },
              label: (ctx) =>
                ctx.parsed.y !== null
                  ? "$" + formatCurrency(ctx.parsed.y)
                  : ""
            }
          }
        }
      }
    });
    console.log(`Chart init ${symbol}`);
  });
}

function destroyCharts() {
  console.log("Destroying charts…");
  Object.keys(priceCharts).forEach(symbol => {
    priceCharts[symbol].destroy();
    delete priceCharts[symbol];
    delete priceHistory[symbol];
    console.log(`Destroyed ${symbol}`);
  });
}

function updateChart(symbol) {
  const chart   = priceCharts[symbol];
  const history = priceHistory[symbol];
  if (!chart || !history) return;
  chart.data.datasets[0].data = history.map((p,i) => ({ x: i, y: p.y }));
  chart.update("none");
}

// --- Message Handling ---
function handleMessage(msg) {
  if (!msg?.type || !msg.payload) return;
  const { type, payload } = msg;
  const symbol = payload.symbol;
  switch (type) {
    case "bbo_update":
      if (symbol) updateBboDisplay(symbol, payload);
      break;
    case "trade":
      addTradeToTable(payload);
      if (symbol && priceHistory[symbol] && payload.price) {
        try {
          const price = parseFloat(payload.price);
          priceHistory[symbol].push({ y: price, x_iso: payload.timestamp });
          if (priceHistory[symbol].length > MAX_CHART_POINTS) {
            priceHistory[symbol].shift();
          }
          updateChart(symbol);
        } catch(e) {
          console.error("Chart trade err:", e);
        }
      }
      break;
    case "book_snapshot":
      if (symbol) updateOrderBookDisplay(symbol, payload);
      break;
    case "agent_status":
      updateAgentStatus(payload);
      break;
    case "exchange_stats":
      updateExchangeStats(payload);
      break;
  }
}

// --- UI Update Functions ---
function updateBboDisplay(symbol, data) {
  const bidP = document.getElementById(`bbo-bid-price-${symbol}`);
  const bidQ = document.getElementById(`bbo-bid-qty-${symbol}`);
  const askP = document.getElementById(`bbo-ask-price-${symbol}`);
  const askQ = document.getElementById(`bbo-ask-qty-${symbol}`);
  const ts   = document.getElementById(`bbo-timestamp-${symbol}`);
  if (bidP) bidP.textContent = data.bid_price ?? "---";
  if (bidQ) bidQ.textContent = formatNumber(data.bid_qty);
  if (askP) askP.textContent = data.ask_price ?? "---";
  if (askQ) askQ.textContent = formatNumber(data.ask_qty);
  if (ts)   ts.textContent   = formatTimestamp(data.timestamp);
}

function addTradeToTable(trade) {
  clearPlaceholders();
  const row = tradesTableBody.insertRow(0);
  const time = formatTimestamp(trade.timestamp);
  row.innerHTML = `
    <td>${time}</td>
    <td>${trade.symbol}</td>
    <td>${trade.price}</td>
    <td>${formatNumber(trade.quantity)}</td>
    <td>${trade.trade_id.substring(0,8)}...</td>
  `;
  while (tradesTableBody.rows.length > MAX_TRADES_DISPLAY) {
    tradesTableBody.deleteRow(-1);
  }
}

function updateOrderBookDisplay(symbol, snap) {
  const asksEl = document.getElementById(`asks-display-${symbol}`);
  const bidsEl = document.getElementById(`bids-display-${symbol}`);
  if (!asksEl || !bidsEl) return;
  asksEl.innerHTML = "";
  bidsEl.innerHTML = "";
  if (snap.asks.length === 0) {
    asksEl.innerHTML = '<div class="book-placeholder">No asks</div>';
  } else {
    snap.asks.forEach(lvl => renderBookLevel(asksEl, lvl));
  }
  if (snap.bids.length === 0) {
    bidsEl.innerHTML = '<div class="book-placeholder">No bids</div>';
  } else {
    snap.bids.forEach(lvl => renderBookLevel(bidsEl, lvl));
  }
}

function renderBookLevel(container, lvl) {
  const el = document.createElement("div");
  el.className = "book-level";
  el.innerHTML = `
    <span class="book-price">${lvl.price}</span>
    <span class="book-qty">${formatNumber(lvl.quantity)}</span>
  `;
  container.appendChild(el);
}

// --- Agent List/Status Updates ---
function populateInitialAgentList() {
  agentListUl.innerHTML = "";
  const cfgs = {
    ABC: [
      { type:"noise", risk:1, bankroll:10000 },
      { type:"market_maker", risk:1, bankroll:10000 },
      { type:"momentum", risk:1.0, bankroll:10000 },
      { type:"noise", risk:1, bankroll:10000 },
      { type:"noise", risk:1, bankroll:10000 },
      { type:"market_maker", risk:1, bankroll:10000 },
      { type:"momentum", risk:1.0, bankroll:10000 },
      { type:"noise", risk:1, bankroll:10000 },
    ],
    XYZ: [
      { type:"noise", risk:1, bankroll:10000 },
      { type:"market_maker", risk:1, bankroll:10000 },
      { type:"momentum", risk:1, bankroll:10000 },
      { type:"noise", risk:1, bankroll:10000 },
      { type:"noise", risk:1, bankroll:10000 },
      { type:"market_maker", risk:1, bankroll:10000 },
      { type:"momentum", risk:1, bankroll:10000 },
      { type:"noise", risk:1, bankroll:10000 },
    ]
  };
  let idCnt = 1;
  for (const sym in cfgs) {
    cfgs[sym].forEach(cfg => {
      const id = `Agent_${idCnt++}`;
      agentState[id] = {
        agent_id:      id,
        symbol:        sym,
        is_active:     true,
        ...cfg,
        position:      0,
        realized_pnl:  0.0,
        unrealized_pnl:0.0,
        trade_count:   0,
        avg_entry_price: 0.0,
        open_orders:   []
      };
      addAgentToListUI(id, agentState[id]);
    });
  }
}

function updateAgentStatus(payload) {
  const id = payload.agent_id;
  if (!id) return;
  Object.assign(agentState[id] ||= {}, payload);
  const li = document.getElementById(`agent-${id}`);
  if (!li) {
    addAgentToListUI(id, agentState[id]);
    return;
  }
  li.querySelector(".agent-bankroll").textContent    = formatCurrency(payload.bankroll);
  li.querySelector(".agent-position").textContent    = formatNumber(payload.position);
  const pnlSpan   = li.querySelector(".agent-pnl");
  const upnlSpan  = li.querySelector(".agent-unrealized-pnl");
  const pnl       = payload.realized_pnl  ?? 0;
  const upnl      = payload.unrealized_pnl ?? 0;
  pnlSpan.textContent  = formatCurrency(pnl);
  upnlSpan.textContent = formatCurrency(upnl);
  pnlSpan.className    = `agent-pnl ${pnl>0?"positive":pnl<0?"negative":""}`;
  upnlSpan.className   = `agent-unrealized-pnl ${upnl>0?"positive":upnl<0?"negative":""}`;
  if (payload.open_orders) {
    const listEl = li.querySelector(".agent-open-orders-list");
    const count  = li.querySelector(".open-order-count");
    listEl.innerHTML = "";
    if (payload.open_orders.length === 0) {
      listEl.innerHTML = "<li>(No open orders)</li>";
      count.textContent = "0";
    } else {
      count.textContent = payload.open_orders.length;
      payload.open_orders.forEach(o => {
        const elem = document.createElement("li");
        const sideClass = o.side === "buy" ? "order-side-buy" : "order-side-sell";
        elem.innerHTML = `
          <span class="${sideClass}">${o.side.toUpperCase()}</span>
          ${formatNumber(o.quantity)}
          ${o.price?`@ ${formatCurrency(o.price)}`:"(Market)"}
          <small>(${o.id.substring(0,6)}...)</small>
        `;
        listEl.appendChild(elem);
      });
    }
  }
}

function addAgentToListUI(id, state) {
  let li = document.getElementById(`agent-${id}`);
  const isNew = !li;
  if (isNew) {
    li = document.createElement("li");
    li.id = `agent-${id}`;
    li.className = "agent-item";
  }
  // Build strategy options
  const stratOpts = AVAILABLE_STRATEGIES.map(s => {
    const sel = s === (state.strategy||"noise") ? " selected" : "";
    const name = s.replace(/_/g," ").replace(/\b\w/g,m=>m.toUpperCase());
    return `<option value="${s}"${sel}>${name}</option>`;
  }).join("");

  li.innerHTML = `
    <div class="agent-info">
      <div class="agent-info-basic">
        <span class="agent-id">${id}</span>
        <span class="agent-symbol">[${state.symbol}]</span>
        <select class="agent-strategy-select" data-agent-id="${id}">
          ${stratOpts}
        </select>
        <span class="agent-status ${state.is_active?"active":"inactive"}">
          ${state.is_active?"Active":"Inactive"}
        </span>
      </div>
      <div class="agent-info-stats">
        <span>Bankroll: $<span class="agent-bankroll">${formatCurrency(state.bankroll)}</span></span>
        <span>Pos: <span class="agent-position">${formatNumber(state.position)}</span></span>
        <span>Real PnL: $<span class="agent-pnl ${state.realized_pnl>0?"positive":state.realized_pnl<0?"negative":""}">
          ${formatCurrency(state.realized_pnl)}
        </span></span>
        <span>Unreal PnL: $<span class="agent-unrealized-pnl ${state.unrealized_pnl>0?"positive":state.unrealized_pnl<0?"negative":""}">
          ${formatCurrency(state.unrealized_pnl)}
        </span></span>
        <span>Trades: <span class="agent-trade-count">${formatNumber(state.trade_count)}</span></span>
      </div>
      <details class="agent-open-orders-details">
        <summary>Open Orders (<span class="open-order-count">0</span>)</summary>
        <ul class="agent-open-orders-list">
          <li>(No open orders)</li>
        </ul>
      </details>
    </div>
    <div class="agent-controls">
      <div class="control-group">
        <label for="risk-${id}">Risk:</label>
        <input type="number" id="risk-${id}" class="agent-risk-input" data-agent-id="${id}"
               min="0.1" max="2.0" step="0.1" value="${Number(state.risk).toFixed(1)}">
        <button class="set-risk-button" data-agent-id="${id}">Set</button>
      </div>
      <div class="control-group">
        <label for="bankroll-${id}">Bankroll:</label>
        <input type="number" id="bankroll-${id}" class="agent-bankroll-input" data-agent-id="${id}"
               min="0" step="1000" value="${Math.round(state.bankroll)}">
        <button class="set-bankroll-button" data-agent-id="${id}">Set</button>
      </div>
      <div class="control-group">
        <button class="toggle-button" data-agent-id="${id}" data-target-state="${!state.is_active}">
          ${state.is_active?"Deactivate":"Activate"}
        </button>
      </div>
    </div>
  `;

  if (isNew) {
    agentListUl.appendChild(li);
  }

  li.querySelector(".agent-strategy-select")
    .addEventListener("change", handleAgentStrategyChange);
  li.querySelector(".set-risk-button")
    .addEventListener("click", handleSetRiskClick);
  li.querySelector(".set-bankroll-button")
    .addEventListener("click", handleSetBankrollClick);
  li.querySelector(".toggle-button")
    .addEventListener("click", handleAgentToggleButtonClick);
}

function handleAgentToggleButtonClick(evt) {
  const id    = evt.target.dataset.agentId;
  const state = evt.target.dataset.targetState === "true";
  sendControlMessage(id, "set_active", state);
}

function handleAgentStrategyChange(evt) {
  const id    = evt.target.dataset.agentId;
  const strat = evt.target.value;
  sendControlMessage(id, "change_strategy", strat);
}

function handleSetRiskClick(evt) {
  const id  = evt.target.dataset.agentId;
  const inp = document.getElementById(`risk-${id}`);
  const val = parseFloat(inp.value);
  if (!isNaN(val) && val >= 0.1 && val <= 2.0) {
    sendControlMessage(id, "set_risk", val);
  } else {
    alert("Risk must be between 0.1 and 2.0");
    inp.value = Number(agentState[id].risk).toFixed(1);
  }
}

function handleSetBankrollClick(evt) {
  const id  = evt.target.dataset.agentId;
  const inp = document.getElementById(`bankroll-${id}`);
  const val = parseFloat(inp.value);
  if (!isNaN(val) && val >= 0) {
    sendControlMessage(id, "set_bankroll", val);
  } else {
    alert("Bankroll must be ≥ 0");
    inp.value = Math.round(agentState[id].bankroll);
  }
}

function updateExchangeStats(payload) {
  totalTradesSpan.textContent = formatNumber(payload.total_trades);
  totalValueSpan.textContent  = formatCurrency(payload.total_volume_value);
  statsTimestampSpan.textContent = formatTimestamp(payload.timestamp);
}

function sendControlMessage(agentId, parameter, value) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const msg = { agent_id: agentId, parameter, value };
    ws.send(JSON.stringify(msg));
    console.log("Sent agent ctrl:", msg);
  } else {
    console.error("WS closed; cannot send agent control");
  }
}

function sendGlobalCommand(command, payload=null) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    const msg = { command };
    if (payload !== null) msg.payload = payload;
    ws.send(JSON.stringify(msg));
    console.log("Sent global cmd:", msg);

    if (command === "set_pause")   isPaused = true;
    if (command === "set_resume")  isPaused = false;
    if (command === "reset")       isPaused = true;
    updatePauseResumeButton();
  } else {
    console.error("WS closed; cannot send global command");
    statusDiv.textContent = "Disconnected!";
    statusDiv.className   = "disconnected";
  }
}

function handleTriggerEvent() {
  const symbol = eventSymbolInput.value.trim().toUpperCase();
  const pctStr = eventPercentInput.value.trim();
  const pct    = parseFloat(pctStr);

  if (!KNOWN_SYMBOLS.includes(symbol)) {
    alert(`Symbol must be one of: ${KNOWN_SYMBOLS.join(", ")}`);
    return;
  }
  if (isNaN(pct) || pct < -100 || pct > 100) {
    alert("Shift must be between -100 and 100");
    return;
  }

  sendGlobalCommand("market_event", {
    symbol,
    percent_shift: pct / 100.0
  });
  alert(`Sent shock for ${symbol}: ${pct}%`);
}

function updatePauseResumeButton() {
  if (pauseResumeButton) {
    pauseResumeButton.textContent = isPaused ? "Resume" : "Pause";
  }
  if (pausedOverlay) {
    document.body.classList.toggle("is-paused", isPaused);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  pauseResumeButton?.addEventListener("click", () => {
    sendGlobalCommand(isPaused ? "set_resume" : "set_pause");
  });
  resetButton?.addEventListener("click", () => {
    if (confirm("Reset market simulation? This clears order books and resets all agent states.")) {
      sendGlobalCommand("reset");
    }
  });
  triggerEventButton?.addEventListener("click", handleTriggerEvent);

  connectWebSocket();
});
