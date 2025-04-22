# market_simulator/config.py

import os
from decimal import Decimal

# --- Core Configuration ---
API_BASE_URL = "http://127.0.0.1:8000/api/v1"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SYMBOLS_TO_SIMULATE = ["ABC", "XYZ"]

# --- Timing Configuration ---
AGENT_ACTION_INTERVAL_SECONDS = 0.75
AGENT_STATUS_PUBLISH_INTERVAL_SECONDS = 5.0
EXCHANGE_STATS_PUBLISH_INTERVAL_SECONDS = 10.0
TRADE_CACHE_SIZE = 20
# *** NEW: Market Shock Expiry ***
SHOCK_EXPIRY_DURATION_SECONDS = 30.0 # How long the price override lasts

# --- Agent Configuration ---
AGENT_CONFIG = { # Used for initial agent creation
    "ABC": [ {"type": "noise", "risk": 0.7, "bankroll": 10000}, {"type": "market_maker", "risk": 0.4, "bankroll": 50000}, {"type": "momentum", "risk": 1.0, "bankroll": 20000} ],
    "XYZ": [ {"type": "noise", "risk": 0.8, "bankroll": 5000}, {"type": "market_maker", "risk": 0.5, "bankroll": 60000}, {"type": "momentum", "risk": 0.9, "bankroll": 15000}, {"type": "noise", "risk": 0.9, "bankroll": 8000}, ]
}
INITIAL_AGENT_CONFIG = AGENT_CONFIG # Use the same dict for reset

# --- Risk Management ---
MAX_POSITION_LIMIT = 1000
MIN_BANKROLL_THRESHOLD = Decimal("100.00")

# --- Strategy Parameters ---
MM_DESIRED_SPREAD = {"ABC": Decimal("0.10"), "XYZ": Decimal("0.03")}
MM_BASE_ORDER_QTY = {"ABC": 8, "XYZ": 25}
MM_RISK_CHECK_PERCENT = Decimal("0.25")
MOMENTUM_WINDOW = 5
MOMENTUM_THRESHOLD = Decimal("0.02")
MOMENTUM_BASE_ORDER_QTY = 20
NOISE_BASE_ORDER_QTY = 15
NOISE_TRADE_PROBABILITY = 0.75
NOISE_RISK_CHECK_PERCENT = Decimal("0.12")

# --- Redis Channels ---
BBO_CHANNEL_PATTERN = "marketdata:bbo:*"
TRADE_CHANNEL = "trades:executed"
AGENT_ACTION_CHANNEL = "agent:actions"
AGENT_STATUS_CHANNEL = "agent:status"
EXCHANGE_STATS_CHANNEL = "exchange:stats"
SIMULATOR_CONTROL_CHANNEL = "simulator:control"
ENGINE_CONTROL_CHANNEL = "engine:control"
MARKET_EVENTS_CHANNEL = "market:events"