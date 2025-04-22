// dashboard/main.js

// --- Configuration ---
const WEBSOCKET_URL = "ws://127.0.0.1:8000/api/v1/ws/dashboard";
const MAX_TRADES_DISPLAY = 30;
const KNOWN_SYMBOLS = ["ABC", "XYZ"]; // Use updated symbols
const AVAILABLE_STRATEGIES = ["noise", "market_maker", "momentum"];

// --- DOM Elements ---
const statusDiv = document.getElementById("status");
const tradesTableBody = document.querySelector("#trades-table tbody");
const agentListUl = document.getElementById("agent-list");
const totalTradesSpan = document.getElementById("total-trades");
const totalValueSpan = document.getElementById("total-value");
const statsTimestampSpan = document.getElementById("stats-timestamp");
// --- New Global Control Buttons ---
const pauseResumeButton = document.getElementById("pause-resume-button");
const resetButton = document.getElementById("reset-button");
const pausedOverlay = document.getElementById("paused-overlay");

// --- Simulation State ---
let isPaused = false; // Track pause state locally for UI

// --- Agent State Cache ---
const agentState = {};

// --- WebSocket Connection ---
let ws;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;

function connectWebSocket() { /* ... same as before ... */
    console.log(`Attempting connect to ${WEBSOCKET_URL} (Attempt ${reconnectAttempts + 1})...`);
    statusDiv.textContent = "Connecting..."; statusDiv.className = "connecting";
    ws = new WebSocket(WEBSOCKET_URL);
    ws.onopen = () => { console.log("WS opened."); statusDiv.textContent = "Connected"; statusDiv.className = "connected"; reconnectAttempts = 0; clearPlaceholders(); populateInitialAgentList(); updatePauseResumeButton(); /* Update button on connect */ };
    ws.onmessage = (event) => { try { const message = JSON.parse(event.data); handleMessage(message); } catch (error) { console.error("Msg handling error:", error, "Raw data:", event.data); } };
    ws.onerror = (error) => { console.error("WS error:", error); statusDiv.textContent = "Connection error!"; statusDiv.className = "disconnected"; };
    ws.onclose = (event) => { console.log("WS closed:", event.code, event.reason); statusDiv.textContent = `Disconnected (${event.code}). ${reconnectAttempts < MAX_RECONNECT_ATTEMPTS ? 'Reconnecting...' : 'Max attempts reached.'}`; statusDiv.className = "disconnected"; if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) { reconnectAttempts++; const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 10000); console.log(`Reconnect in ${delay / 1000}s...`); setTimeout(connectWebSocket, delay); } else { console.error("Max reconnect attempts."); } };
}

// --- Utility Functions ---
function clearPlaceholders() { /* ... same ... */
    const firstTradeRow = tradesTableBody.querySelector('tr'); if (firstTradeRow && firstTradeRow.cells.length === 1 && firstTradeRow.textContent.includes("Waiting")) { tradesTableBody.innerHTML = ''; }
    KNOWN_SYMBOLS.forEach(symbol => { const asksDiv = document.getElementById(`asks-display-${symbol}`); const bidsDiv = document.getElementById(`bids-display-${symbol}`); const askPlaceholder = asksDiv?.querySelector('.book-placeholder'); const bidPlaceholder = bidsDiv?.querySelector('.book-placeholder'); if (askPlaceholder) askPlaceholder.textContent = 'Waiting...'; if (bidPlaceholder) bidPlaceholder.textContent = 'Waiting...'; });
    const agentPlaceholder = agentListUl.querySelector('li'); if (agentPlaceholder && agentPlaceholder.textContent.includes("Loading")) { agentListUl.innerHTML = ''; }
}
function formatTimestamp(isoString) { /* ... same ... */ if (!isoString) return "---"; try { return new Date(isoString).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch (e) { console.error("Timestamp format error:", e); return "Invalid"; } }
function formatCurrency(value) { /* ... same ... */ if (value === null || value === undefined) return "---"; try { return Number(value).toFixed(2); } catch { return "NaN"; } }
function formatNumber(value) { /* ... same ... */ if (value === null || value === undefined) return "---"; try { return Number(value).toLocaleString(); } catch { return "NaN"; } }

// --- Message Handling ---
function handleMessage(message) { // ... same switch statement logic ...
    if (!message || !message.type || !message.payload) { console.warn("Invalid msg format:", message); return; }
    const payload = message.payload; const symbol = payload.symbol;
    // console.log("Handling message:", JSON.stringify(message, null, 2));
    switch (message.type) {
        case "bbo_update": if (symbol) updateBboDisplay(symbol, payload); else console.warn("BBO update missing symbol:", payload); break;
        case "trade": addTradeToTable(payload); break;
        case "order_update": /* console.log("Order Update:", payload); */ break;
        case "book_snapshot": if (symbol) updateOrderBookDisplay(symbol, payload); else console.warn("Book snapshot missing symbol:", payload); break;
        case "agent_action": /* console.log("Agent Action:", payload); */ break;
        case "agent_status": updateAgentStatus(payload); break;
        case "exchange_stats": updateExchangeStats(payload); break;
        default: console.warn("Received unknown message type:", message.type, payload);
    }
}

// --- UI Update Functions ---
function updateBboDisplay(symbol, bboData) { /* ... same ... */
    const bidPriceSpan = document.getElementById(`bbo-bid-price-${symbol}`); const bidQtySpan = document.getElementById(`bbo-bid-qty-${symbol}`); const askPriceSpan = document.getElementById(`bbo-ask-price-${symbol}`); const askQtySpan = document.getElementById(`bbo-ask-qty-${symbol}`); const timestampSpan = document.getElementById(`bbo-timestamp-${symbol}`);
    if (bidPriceSpan) bidPriceSpan.textContent = bboData.bid_price ?? "---"; if (bidQtySpan) bidQtySpan.textContent = formatNumber(bboData.bid_qty) ?? "---"; if (askPriceSpan) askPriceSpan.textContent = bboData.ask_price ?? "---"; if (askQtySpan) askQtySpan.textContent = formatNumber(bboData.ask_qty) ?? "---"; if (timestampSpan) timestampSpan.textContent = formatTimestamp(bboData.timestamp);
}
function addTradeToTable(tradeData) { /* ... same ... */
    clearPlaceholders(); const row = tradesTableBody.insertRow(0); const timestamp = formatTimestamp(tradeData.timestamp);
    row.innerHTML = `<td>${timestamp}</td><td>${tradeData.symbol}</td><td>${tradeData.price}</td><td>${formatNumber(tradeData.quantity)}</td><td>${tradeData.trade_id.substring(0, 8)}...</td>`;
    while (tradesTableBody.rows.length > MAX_TRADES_DISPLAY) { tradesTableBody.deleteRow(tradesTableBody.rows.length - 1); }
}
function updateOrderBookDisplay(symbol, snapshotData) { /* ... same ... */
    const asksDiv = document.getElementById(`asks-display-${symbol}`); const bidsDiv = document.getElementById(`bids-display-${symbol}`); if (!asksDiv || !bidsDiv) { console.warn(`Display elements not found for symbol ${symbol}`); return; } asksDiv.innerHTML = ''; bidsDiv.innerHTML = '';
    if (snapshotData.asks.length === 0) { asksDiv.innerHTML = '<div class="book-placeholder">No asks</div>'; } else { snapshotData.asks.forEach(level => { renderBookLevel(asksDiv, level); }); }
    if (snapshotData.bids.length === 0) { bidsDiv.innerHTML = '<div class="book-placeholder">No bids</div>'; } else { snapshotData.bids.forEach(level => { renderBookLevel(bidsDiv, level); }); }
}
function renderBookLevel(displayDiv, level) { /* ... same ... */
     const levelDiv = document.createElement('div'); levelDiv.className = 'book-level'; levelDiv.innerHTML = `<span class="book-price">${level.price}</span><span class="book-qty">${formatNumber(level.quantity)}</span>`; displayDiv.appendChild(levelDiv);
}

// Agent List/Status Updates (Includes Unrealized PnL handling)
function populateInitialAgentList() { // ... same, uses updated AGENT_CONFIG from sim ...
    agentListUl.innerHTML = '';
    // This ideally should fetch config from backend, but using JS copy for now
    const agentConfigs = { // Corresponds to INITIAL_AGENT_CONFIG in Python
        "ABC": [{"type": "noise", "risk": 0.7, "bankroll": 10000}, {"type": "market_maker", "risk": 0.4, "bankroll": 50000}, {"type": "momentum", "risk": 1.0, "bankroll": 20000}],
        "XYZ": [{"type": "noise", "risk": 0.8, "bankroll": 5000}, {"type": "market_maker", "risk": 0.5, "bankroll": 60000}, {"type": "momentum", "risk": 0.9, "bankroll": 15000}, {"type": "noise", "risk": 0.9, "bankroll": 8000}]
    };
    let agentIdCounter = 1;
    for (const symbol in agentConfigs) {
        agentConfigs[symbol].forEach(conf => {
             const agentId = `Agent_${agentIdCounter}`;
             agentState[agentId] = { agent_id: agentId, symbol: symbol, is_active: true, strategy: conf.type, risk_factor: conf.risk, bankroll: conf.bankroll, position: 0, realized_pnl: 0.0, unrealized_pnl: 0.0, trade_count: 0, average_entry_price: 0.0 };
             addAgentToListUI(agentId, agentState[agentId]);
             agentIdCounter++;
        });
    }
    console.log("Populated initial agent list from config:", agentState);
}

// MODIFIED: updateAgentStatus includes Unrealized PnL
function updateAgentStatus(statusPayload) {
    const agentId = statusPayload?.agent_id;
    if (!agentId) { console.warn("updateAgentStatus: no agent_id"); return; }
    console.log(`Inside updateAgentStatus for Agent ${agentId}`);

    try { // Update cache
        if (!agentState[agentId]) { agentState[agentId] = {}; console.warn(`Created cache entry for ${agentId}`); }
        Object.assign(agentState[agentId], statusPayload); // Merge payload into cache
        console.log(`Updated agentState[${agentId}] cache`);
    } catch (e) { console.error(`Error updating cache for ${agentId}:`, e); return; }

    const listItem = document.getElementById(`agent-${agentId}`);
    if (!listItem) { console.warn(`List item 'agent-${agentId}' not found. Creating.`); addAgentToListUI(agentId, agentState[agentId]); return; }
    // console.log(`Updating UI for ${agentId}.`); // Reduce log noise

    try { // Update UI elements
        const bankrollSpan = listItem.querySelector('.agent-bankroll');
        if (bankrollSpan) bankrollSpan.textContent = formatCurrency(statusPayload.bankroll);
        const positionSpan = listItem.querySelector('.agent-position');
        if (positionSpan) positionSpan.textContent = formatNumber(statusPayload.position);
        const pnlSpan = listItem.querySelector('.agent-pnl');
        if (pnlSpan) { const pnl = statusPayload.realized_pnl ?? 0; pnlSpan.textContent = formatCurrency(pnl); pnlSpan.className = `agent-pnl ${pnl > 0 ? 'positive' : (pnl < 0 ? 'negative' : '')}`; }
        const unrealizedPnlSpan = listItem.querySelector('.agent-unrealized-pnl'); // Find new span
        if (unrealizedPnlSpan) { const unrealPnl = statusPayload.unrealized_pnl ?? 0; unrealizedPnlSpan.textContent = formatCurrency(unrealPnl); unrealizedPnlSpan.className = `agent-unrealized-pnl ${unrealPnl > 0 ? 'positive' : (unrealPnl < 0 ? 'negative' : '')}`; } // Update new span
        const tradeCountSpan = listItem.querySelector('.agent-trade-count');
        if (tradeCountSpan) tradeCountSpan.textContent = formatNumber(statusPayload.trade_count);
        // ... (update status, strategy, risk, bankroll inputs - same as before) ...
        if (statusPayload.is_active !== undefined) { /* ... */ }
        if (statusPayload.strategy !== undefined) { /* ... */ }
        if (statusPayload.risk_factor !== undefined) { const riskInput = listItem.querySelector('.agent-risk-input'); if (riskInput && document.activeElement !== riskInput) riskInput.value = Number(statusPayload.risk_factor).toFixed(1); }
        if (statusPayload.bankroll !== undefined) { const bankrollInput = listItem.querySelector('.agent-bankroll-input'); if (bankrollInput && document.activeElement !== bankrollInput) bankrollInput.value = Math.round(statusPayload.bankroll); }

    } catch (e) { console.error(`Error updating UI elements for ${agentId}:`, e); }
}

// MODIFIED: addAgentToListUI includes Unrealized PnL span
function addAgentToListUI(agentId, state) {
    let listItem = document.getElementById(`agent-${agentId}`);
    const isNew = !listItem;
    if (isNew) { listItem = document.createElement('li'); listItem.id = `agent-${agentId}`; listItem.className = 'agent-item'; }

    const cache = agentState[agentId] || {}; // Use cache as fallback
    const isActive = state?.is_active ?? cache.is_active ?? true;
    const currentStrategy = state?.strategy ?? cache.strategy ?? 'noise';
    const riskFactor = state?.risk_factor ?? cache.risk_factor ?? 0.5;
    const bankroll = state?.bankroll ?? cache.bankroll ?? 0;
    const position = state?.position ?? cache.position ?? 0;
    const realizedPnl = state?.realized_pnl ?? cache.realized_pnl ?? 0;
    const unrealizedPnl = state?.unrealized_pnl ?? cache.unrealized_pnl ?? 0; // Get unrealized
    const tradeCount = state?.trade_count ?? cache.trade_count ?? 0;
    const symbol = state?.symbol ?? cache.symbol ?? 'N/A';

    const statusClass = isActive ? 'active' : 'inactive'; const statusText = isActive ? 'Active' : 'Inactive'; const toggleButtonText = isActive ? 'Deactivate' : 'Activate';
    const pnlClass = realizedPnl > 0 ? 'positive' : (realizedPnl < 0 ? 'negative' : '');
    const unrealPnlClass = unrealizedPnl > 0 ? 'positive' : (unrealizedPnl < 0 ? 'negative' : ''); // Class for unrealized

    let strategyOptions = ''; AVAILABLE_STRATEGIES.forEach(strat => { const selected = strat === currentStrategy ? ' selected' : ''; const displayName = strat.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()); strategyOptions += `<option value="${strat}"${selected}>${displayName}</option>`; });

    listItem.innerHTML = `
        <div class="agent-info">
            <div class="agent-info-basic">
                <span class="agent-id" title="${agentId}">${agentId}</span> <span class="agent-symbol">[${symbol}]</span>
                 <select class="agent-strategy-select" data-agent-id="${agentId}" title="Select Strategy">${strategyOptions}</select>
                <span class="agent-status ${statusClass}">${statusText}</span>
            </div>
            <div class="agent-info-stats">
                <span>Bankroll: $<span class="agent-bankroll">${formatCurrency(bankroll)}</span></span>
                <span>Pos: <span class="agent-position">${formatNumber(position)}</span></span>
                <span>Real PnL: $<span class="agent-pnl ${pnlClass}">${formatCurrency(realizedPnl)}</span></span>
                <span>Unreal PnL: $<span class="agent-unrealized-pnl ${unrealPnlClass}">${formatCurrency(unrealizedPnl)}</span></span> <!-- ADDED -->
                <span>Trades: <span class="agent-trade-count">${formatNumber(tradeCount)}</span></span>
            </div>
        </div>
        <div class="agent-controls">
             <div class="control-group"> <label for="risk-${agentId}">Risk:</label> <input type="number" id="risk-${agentId}" class="agent-risk-input" step="0.1" min="0.1" max="2.0" value="${Number(riskFactor).toFixed(1)}" data-agent-id="${agentId}"> <button class="set-risk-button" data-agent-id="${agentId}">Set</button> </div>
             <div class="control-group"> <label for="bankroll-${agentId}">Bankroll:</label> <input type="number" id="bankroll-${agentId}" class="agent-bankroll-input" step="1000" min="0" value="${Math.round(bankroll)}" data-agent-id="${agentId}"> <button class="set-bankroll-button" data-agent-id="${agentId}">Set</button> </div>
             <div class="control-group"> <button class="toggle-button" data-agent-id="${agentId}" data-target-state="${!isActive}" title="${toggleButtonText} Agent">${toggleButtonText}</button> </div>
        </div>`;

    if (isNew) { const placeholder = agentListUl.querySelector('li'); if (placeholder && placeholder.textContent.includes("Loading")) { agentListUl.innerHTML = ''; } agentListUl.appendChild(listItem); }

    const toggleButton = listItem.querySelector('.toggle-button'); const strategySelect = listItem.querySelector('.agent-strategy-select'); const setRiskButton = listItem.querySelector('.set-risk-button'); const setBankrollButton = listItem.querySelector('.set-bankroll-button');
    toggleButton?.removeEventListener('click', handleAgentToggleButtonClick); toggleButton?.addEventListener('click', handleAgentToggleButtonClick); strategySelect?.removeEventListener('change', handleAgentStrategyChange); strategySelect?.addEventListener('change', handleAgentStrategyChange); setRiskButton?.removeEventListener('click', handleSetRiskClick); setRiskButton?.addEventListener('click', handleSetRiskClick); setBankrollButton?.removeEventListener('click', handleSetBankrollClick); setBankrollButton?.addEventListener('click', handleSetBankrollClick);
}

// --- Event Handlers for Agent Controls ---
function handleAgentToggleButtonClick(event) { /* ... same ... */ const button = event.target; const agentId = button.getAttribute('data-agent-id'); const targetState = button.getAttribute('data-target-state') === 'true'; if (!agentId) return; console.log(`UI Toggle: Agent=${agentId}, Target State=${targetState}`); sendControlMessage(agentId, "set_active", targetState); }
function handleAgentStrategyChange(event) { /* ... same ... */ const select = event.target; const agentId = select.getAttribute('data-agent-id'); const newStrategy = select.value; if (!agentId) return; console.log(`UI Strategy Change: Agent=${agentId}, Strategy=${newStrategy}`); sendControlMessage(agentId, "change_strategy", newStrategy); }
function handleSetRiskClick(event) { /* ... same ... */ const button = event.target; const agentId = button.getAttribute('data-agent-id'); const input = document.getElementById(`risk-${agentId}`); if (!agentId || !input) return; try { const newRisk = parseFloat(input.value); if (!isNaN(newRisk) && newRisk >= 0.1 && newRisk <= 2.0) { sendControlMessage(agentId, "set_risk", newRisk); if (agentState[agentId]) agentState[agentId].risk_factor = newRisk; } else { alert("Risk must be 0.1-2.0."); input.value = agentState[agentId]?.risk_factor?.toFixed(1) ?? 0.5; } } catch (e) { alert("Invalid number."); input.value = agentState[agentId]?.risk_factor?.toFixed(1) ?? 0.5; } }
function handleSetBankrollClick(event) { /* ... same ... */ const button = event.target; const agentId = button.getAttribute('data-agent-id'); const input = document.getElementById(`bankroll-${agentId}`); if (!agentId || !input) return; try { const newBankroll = parseFloat(input.value); if (!isNaN(newBankroll) && newBankroll >= 0) { sendControlMessage(agentId, "set_bankroll", newBankroll); if (agentState[agentId]) { agentState[agentId].bankroll = newBankroll; const listItem = document.getElementById(`agent-${agentId}`); const span = listItem?.querySelector('.agent-bankroll'); if(span) span.textContent = formatCurrency(newBankroll); } } else { alert("Bankroll must be >= 0."); input.value = Math.round(agentState[agentId]?.bankroll ?? 0); } } catch (e) { alert("Invalid number."); input.value = Math.round(agentState[agentId]?.bankroll ?? 0); } }

// Exchange Stats Update
function updateExchangeStats(statsPayload) { /* ... same ... */
     console.log("Inside updateExchangeStats", statsPayload); try { if (totalTradesSpan) { totalTradesSpan.textContent = formatNumber(statsPayload.total_trades); } else { console.error("totalTradesSpan not found!"); } if (totalValueSpan) { totalValueSpan.textContent = formatCurrency(statsPayload.total_volume_value); } else { console.error("totalValueSpan not found!"); } if (statsTimestampSpan) { statsTimestampSpan.textContent = formatTimestamp(statsPayload.timestamp); } else { console.error("statsTimestampSpan not found!"); } } catch (e) { console.error("Error updateExchangeStats:", e); }
}

// --- Send Control Message (Agent Params) ---
function sendControlMessage(agentId, parameter, value) {
    if (ws && ws.readyState === WebSocket.OPEN) {
        const message = { agent_id: agentId, parameter: parameter, value: value };
        const messageJson = JSON.stringify(message);
        console.log("Sending agent control:", messageJson);
        ws.send(messageJson);
        // Optimistic UI updates (cache + key elements)
        if (agentState[agentId]) { if (parameter === "set_active") { agentState[agentId].is_active = value; addAgentToListUI(agentId, agentState[agentId]); } else if (parameter === "change_strategy") { agentState[agentId].strategy = value; } else if (parameter === "set_risk") { agentState[agentId].risk_factor = value; } else if (parameter === "set_bankroll") { agentState[agentId].bankroll = value; const listItem = document.getElementById(`agent-${agentId}`); const bankrollSpan = listItem?.querySelector('.agent-bankroll'); if (bankrollSpan) bankrollSpan.textContent = formatCurrency(value); } }
    } else { console.error("WS not connected."); statusDiv.textContent = "Disconnected!"; statusDiv.className = "disconnected"; }
}

// --- NEW: Send Global Command Message (Pause/Resume/Reset) ---
function sendGlobalCommand(command) {
     if (ws && ws.readyState === WebSocket.OPEN) {
        const message = { command: command }; // Use 'command' key
        const messageJson = JSON.stringify(message);
        console.log("Sending global command:", messageJson);
        ws.send(messageJson);

        // Optimistic UI update for pause/resume
        if(command === 'set_pause') {
            isPaused = true;
            updatePauseResumeButton();
        } else if (command === 'set_resume') {
            isPaused = false;
            updatePauseResumeButton();
        } else if (command === 'reset') {
             console.log("Reset command sent. Expecting backend state update.");
             // Optionally clear some UI elements immediately?
             // tradesTableBody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#888;">Market Resetting...</td></tr>';
             // No easy way to reset agent UI without full refresh or agent_status msgs
        }

    } else {
        console.error("WebSocket is not connected. Cannot send global command.");
        statusDiv.textContent = "Disconnected! Cannot send command.";
        statusDiv.className = "disconnected";
    }
}

// --- NEW: Update Pause/Resume Button Text & UI State ---
function updatePauseResumeButton() {
    if (pauseResumeButton) {
        pauseResumeButton.textContent = isPaused ? "Resume" : "Pause";
    }
    if(pausedOverlay) {
        // Add/remove class on body for overlay visibility via CSS
        document.body.classList.toggle('is-paused', isPaused);
    }
}

// --- Event Listeners ---
document.addEventListener('DOMContentLoaded', () => {
    console.log("DOM loaded. Connecting WS.");
    connectWebSocket();

    // --- NEW: Add Listeners for Global Control Buttons ---
    pauseResumeButton?.addEventListener('click', () => {
        if (isPaused) {
            sendGlobalCommand('set_resume');
        } else {
            sendGlobalCommand('set_pause');
        }
    });

    resetButton?.addEventListener('click', () => {
        if (confirm("Are you sure you want to reset the market simulation?\nThis will reset all agent states and clear order books.")) {
            sendGlobalCommand('reset');
        }
    });
});