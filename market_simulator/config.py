# market_simulator/config.py

import os
from decimal import Decimal

API_BASE_URL = "http://127.0.0.1:8000/api/v1"
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SYMBOLS_TO_SIMULATE = ["ABC", "XYZ"]

AGENT_ACTION_INTERVAL_SECONDS = 0.5
AGENT_STATUS_PUBLISH_INTERVAL_SECONDS = 1.0
EXCHANGE_STATS_PUBLISH_INTERVAL_SECONDS = 1.0
TRADE_CACHE_SIZE = 20
SHOCK_EXPIRY_DURATION_SECONDS = 30.0 

AGENT_CONFIG = { 
    "ABC": [ {"type": "noise", "risk": 1, "bankroll": 10000}, {"type": "market_maker", "risk": 1, "bankroll": 10000}, {"type": "momentum", "risk": 1.0, "bankroll": 10000}, {"type": "noise", "risk": 2.0, "bankroll": 10000}, {"type": "noise", "risk": 1, "bankroll": 10000}, {"type": "market_maker", "risk": 1, "bankroll": 10000}, {"type": "momentum", "risk": 1.0, "bankroll": 10000}, {"type": "noise", "risk": 2.0, "bankroll": 10000}, ],
    "XYZ": [ {"type": "noise", "risk": 1, "bankroll": 10000}, {"type": "market_maker", "risk": 1, "bankroll": 10000}, {"type": "momentum", "risk": 1, "bankroll": 10000}, {"type": "noise", "risk": 2.0, "bankroll": 10000}, {"type": "noise", "risk": 1, "bankroll": 10000}, {"type": "market_maker", "risk": 1, "bankroll": 10000}, {"type": "momentum", "risk": 1, "bankroll": 10000}, {"type": "noise", "risk": 2.0, "bankroll": 10000},]
}
INITIAL_AGENT_CONFIG = AGENT_CONFIG 

MAX_POSITION_LIMIT = 1000
MIN_BANKROLL_THRESHOLD = Decimal("100.00")

MM_DESIRED_SPREAD = {"ABC": Decimal("0.10"), "XYZ": Decimal("0.03")}
MM_BASE_ORDER_QTY = {"ABC": 8, "XYZ": 25}
MM_RISK_CHECK_PERCENT = Decimal("0.25")
MOMENTUM_WINDOW = 5
MOMENTUM_THRESHOLD = Decimal("0.02")
MOMENTUM_BASE_ORDER_QTY = 20
NOISE_BASE_ORDER_QTY = 15
NOISE_TRADE_PROBABILITY = 0.75
NOISE_RISK_CHECK_PERCENT = Decimal("0.12")

BBO_CHANNEL_PATTERN = "marketdata:bbo:*"
TRADE_CHANNEL = "trades:executed"
AGENT_ACTION_CHANNEL = "agent:actions"
AGENT_STATUS_CHANNEL = "agent:status"
EXCHANGE_STATS_CHANNEL = "exchange:stats"
SIMULATOR_CONTROL_CHANNEL = "simulator:control"
ENGINE_CONTROL_CHANNEL = "engine:control"
MARKET_EVENTS_CHANNEL = "market:events"