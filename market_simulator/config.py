# market_simulator/config.py

import os
from decimal import Decimal

# --- Core Configuration ---
API_BASE_URL = "http://127.0.0.1:8000/api/v1"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SYMBOLS_TO_SIMULATE = ["ABC", "XYZ"]

# --- Timing Configuration ---
AGENT_ACTION_INTERVAL_SECONDS = 0.5 # Faster trading cycle
AGENT_STATUS_PUBLISH_INTERVAL_SECONDS = 2.0
EXCHANGE_STATS_PUBLISH_INTERVAL_SECONDS = 2.0
TRADE_CACHE_SIZE = 20 # Number of recent trades to keep per symbol

# --- Agent Configuration (Increased Aggression & New Agent) ---
AGENT_CONFIG = {
    "ABC": [
        {"type": "noise",        "risk": 1.0, "bankroll": 10000},
        {"type": "market_maker", "risk": 1.0, "bankroll": 10000},
        {"type": "momentum",     "risk": 1.0, "bankroll": 10000}
    ],
    "XYZ": [
        {"type": "noise",        "risk": 1.0, "bankroll": 10000},
        {"type": "market_maker", "risk": 1.0, "bankroll": 10000}, # Agent 5
        {"type": "momentum",     "risk": 1.0, "bankroll": 10000}, # Agent 6
        {"type": "noise",        "risk": 1.0, "bankroll": 10000},  # ADDED: Agent 7 (More noise)
    ]
}

# --- Strategy Parameters (Tuned for more activity) ---
# Market Maker
MM_DESIRED_SPREAD = {"TEST": Decimal("0.10"), "XYZ": Decimal("0.03")} # Tighter spread for SIM
MM_BASE_ORDER_QTY = {"TEST": 8, "XYZ": 25} # Increased base qty for SIM
MM_RISK_CHECK_PERCENT = Decimal("0.25") # Max % bankroll per quote side

# Momentum
MOMENTUM_WINDOW = 5
MOMENTUM_THRESHOLD = Decimal("0.02") # Lower threshold, easier to trigger
MOMENTUM_BASE_ORDER_QTY = 20 # Increased base qty

# Noise
NOISE_BASE_ORDER_QTY = 15
NOISE_TRADE_PROBABILITY = 0.75 # Increased probability
NOISE_RISK_CHECK_PERCENT = Decimal("0.12") # Max % bankroll per noise trade

# --- Redis Channels ---
BBO_CHANNEL_PATTERN = "marketdata:bbo:*"
TRADE_CHANNEL = "trades:executed"
AGENT_ACTION_CHANNEL = "agent:actions"
AGENT_STATUS_CHANNEL = "agent:status"
EXCHANGE_STATS_CHANNEL = "exchange:stats"
SIMULATOR_CONTROL_CHANNEL = "simulator:control"